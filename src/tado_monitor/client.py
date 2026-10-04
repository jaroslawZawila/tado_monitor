"""Minimal client for matterjs-server's WebSocket API.

Protocol (docs/websockets_api.md in matter-js/matterjs-server): on connect
the server sends one ``server_info`` message; after that we send
``{"message_id", "command", "args"}`` and get back a message with the same
``message_id`` carrying ``result`` or ``error_code``. Anything else on the
socket is an event (``{"event", "data"}``) once we've sent start_listening.

Large integers (node ids, fabric ids) arrive as bare JSON numbers; Python's
json keeps them exact, unlike JavaScript's.
"""

import itertools
import json
from typing import Any

from websockets.asyncio.client import ClientConnection

_message_ids = itertools.count(1)

# A start_listening reply carries every node with every attribute; the
# websockets default cap of 1 MiB is too tight once a few devices are paired.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class MatterError(Exception):
    def __init__(self, code: int, details: str | None) -> None:
        super().__init__(f"Matter server error {code}: {details}")
        self.code = code


async def call(
    ws: ClientConnection, command: str, args: dict[str, Any] | None = None
) -> Any:
    """Send one command and wait for its reply, skipping unrelated messages."""
    message_id = str(next(_message_ids))
    await ws.send(
        json.dumps({"message_id": message_id, "command": command, "args": args or {}})
    )
    while True:
        message = json.loads(await ws.recv())
        if message.get("message_id") != message_id:
            continue
        if "error_code" in message:
            raise MatterError(message["error_code"], message.get("details"))
        return message.get("result")
