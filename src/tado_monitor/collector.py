"""Collector: keep a live subscription to the Matter server, write readings.

    uv run tado-collector

matterjs-server holds the actual Matter subscriptions to the devices; we
listen on its WebSocket and get an ``attribute_updated`` event whenever a
device reports. Changed readings are written immediately, and every
``snapshot_interval_s`` all current values are written again so quiet
sensors still produce a steady line in Grafana.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from tado_monitor.client import MAX_MESSAGE_BYTES, call
from tado_monitor.config import Settings, load_names
from tado_monitor.matter import (
    DeviceInfo,
    device_info,
    display_name,
    is_relevant,
    readings,
)
from tado_monitor.store import insert_readings, upsert_device

log = logging.getLogger("tado_monitor")


@dataclass
class Node:
    info: DeviceInfo
    name: str
    available: bool
    attributes: dict[str, Any]
    # Last value stored per metric, so events only write what changed.
    written: dict[str, float] = field(default_factory=dict)


class Collector:
    def __init__(self, db: AsyncConnection, names: dict[int, str]) -> None:
        self.db = db
        self.names = names
        self.nodes: dict[int, Node] = {}

    async def load_node(self, data: dict[str, Any]) -> None:
        """A full node, from start_listening, node_added or node_updated."""
        node_id = int(data["node_id"])
        attributes = dict(data.get("attributes") or {})
        info = device_info(node_id, attributes)
        name = display_name(info, self.names)
        previous = self.nodes.get(node_id)
        self.nodes[node_id] = Node(
            info=info,
            name=name,
            available=bool(data.get("available")),
            attributes=attributes,
            written=previous.written if previous else {},
        )
        await upsert_device(self.db, info, name)
        log.info(
            "node %s %r (%s), available=%s, now: %s",
            node_id,
            name,
            info.product,
            data.get("available"),
            readings(attributes),
        )
        # Store right away rather than at the next snapshot, so a freshly
        # paired device shows up immediately.
        await self.write_changed(self.nodes[node_id])

    async def handle(self, message: dict[str, Any]) -> None:
        data: Any = message.get("data")
        match message.get("event"):
            case "attribute_updated":
                node_id, path, value = data
                await self.on_attribute(int(node_id), path, value)
            case "node_added" | "node_updated":
                await self.load_node(data)
            case "node_removed":
                self.nodes.pop(int(data), None)
            case "server_shutdown":
                log.warning("Matter server is shutting down")

    async def on_attribute(self, node_id: int, path: str, value: Any) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            return
        node.attributes[path] = value
        if is_relevant(path):
            await self.write_changed(node, log_changes=True)

    async def write_changed(self, node: Node, log_changes: bool = False) -> None:
        if not node.available:
            return
        changed = {
            metric: v
            for metric, v in readings(node.attributes).items()
            if node.written.get(metric) != v
        }
        if changed:
            if log_changes:
                log.info("%s: %s", node.name, changed)
            await self.write(node, changed)

    async def snapshot(self) -> None:
        for node in self.nodes.values():
            # An unavailable node's cached values are stale; don't re-stamp them.
            if node.available:
                await self.write(node, readings(node.attributes))

    async def write(self, node: Node, values: dict[str, float]) -> None:
        await insert_readings(self.db, node.info.node_id, datetime.now(UTC), values)
        node.written.update(values)


async def session(ws: ClientConnection, collector: Collector, interval: int) -> None:
    server_info = json.loads(await ws.recv())
    log.info(
        "connected to %s (schema %s)",
        server_info.get("sdk_version"),
        server_info.get("schema_version"),
    )
    collector.nodes.clear()
    for data in await call(ws, "start_listening"):
        await collector.load_node(data)

    next_snapshot = asyncio.get_running_loop().time() + interval
    while True:
        try:
            # recv() is cancellation-safe, so timing it out loses no message.
            async with asyncio.timeout_at(next_snapshot):
                message = json.loads(await ws.recv())
        except TimeoutError:
            await collector.snapshot()
            next_snapshot += interval
            continue
        await collector.handle(message)


async def run(settings: Settings) -> None:
    names = load_names(settings.names_file)
    async with await AsyncConnection.connect(
        settings.database_url, autocommit=True
    ) as db:
        collector = Collector(db, names)
        # Iterating connect() reconnects with exponential backoff when the
        # server is down; a dropped session just loops back to here.
        async for ws in connect(settings.matter_ws_url, max_size=MAX_MESSAGE_BYTES):
            try:
                await session(ws, collector, settings.snapshot_interval_s)
            except ConnectionClosed:
                log.warning("lost the Matter server, reconnecting")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
