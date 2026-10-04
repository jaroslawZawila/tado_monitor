"""Pairing and inspection helpers, run against the live Matter server.

    uv run tado-cli commission 12345678901
    uv run tado-cli nodes
    uv run tado-cli dump 1 > tests/fixtures/node_1.json

``commission`` takes the 11-digit code from the tado app's Matter linking
screen. The device is already on the Thread network (via Bridge X), so no
Bluetooth and no Thread credentials are needed: ``network_only`` finds it by
mDNS and joins it to our fabric as an extra admin, alongside tado's.
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from websockets.asyncio.client import connect

from tado_monitor.client import MAX_MESSAGE_BYTES, MatterError, call
from tado_monitor.config import Settings, load_names
from tado_monitor.matter import device_info, display_name, readings


async def commission(url: str, code: str) -> None:
    async with connect(url, max_size=MAX_MESSAGE_BYTES) as ws:
        await ws.recv()  # server_info
        print("commissioning... (Thread devices can take a minute)")
        node = await call(
            ws, "commission_with_code", {"code": code, "network_only": True}
        )
        info = device_info(int(node["node_id"]), node.get("attributes") or {})
        print(f"done: node_id={info.node_id} product={info.product!r}")
        print(f'add to rooms.toml:  {info.node_id} = "<room name>"')


async def nodes(url: str, names: dict[int, str]) -> None:
    async with connect(url, max_size=MAX_MESSAGE_BYTES) as ws:
        await ws.recv()
        for node in await call(ws, "get_nodes"):
            attributes = node.get("attributes") or {}
            info = device_info(int(node["node_id"]), attributes)
            print(
                f"{info.node_id:>4}  {display_name(info, names):<24}"
                f" {info.product or '?':<28} serial={info.serial}"
                f" available={node.get('available')}"
            )
            print(f"      {readings(attributes)}")


async def dump(url: str, node_id: int) -> None:
    async with connect(url, max_size=MAX_MESSAGE_BYTES) as ws:
        await ws.recv()
        node: Any = await call(ws, "get_node", {"node_id": node_id})
        print(json.dumps(node, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="tado-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("commission").add_argument("code")
    sub.add_parser("nodes")
    sub.add_parser("dump").add_argument("node_id", type=int)
    args = parser.parse_args()

    settings = Settings()
    url = settings.matter_ws_url
    try:
        match args.cmd:
            case "commission":
                asyncio.run(commission(url, args.code.replace("-", "")))
            case "nodes":
                asyncio.run(nodes(url, load_names(settings.names_file)))
            case "dump":
                asyncio.run(dump(url, args.node_id))
    except MatterError as e:
        # "already commissioned into this fabric" = that code was for a device
        # we already have; a timeout usually means a weak Thread link -- just
        # generate a fresh code and retry.
        sys.exit(str(e))


if __name__ == "__main__":
    main()
