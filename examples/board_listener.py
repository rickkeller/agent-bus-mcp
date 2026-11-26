"""Example only. This file is not a package command, and install does not start it.

Subscribe to board.> and reply to each request. The post is already written
before the offer, so a reply only marks it delivered. Also print task.> notices. A task notice is a
publish, not a request, so this script does not reply to those.

    export AGENT_BUS_NATS_URL=nats://127.0.0.1:4222
    python examples/board_listener.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import nats


async def main() -> None:
    url = os.environ.get("AGENT_BUS_NATS_URL", "")
    if not url:
        sys.exit("set AGENT_BUS_NATS_URL, for example nats://127.0.0.1:4222")
    connection = await nats.connect(url)

    async def on_board(message) -> None:
        print(message.subject, message.data.decode(), flush=True)
        if message.reply:
            await message.respond(b"ok")

    async def on_task(message) -> None:
        print(message.subject, message.data.decode(), flush=True)

    await connection.subscribe("board.>", cb=on_board)
    await connection.subscribe("task.>", cb=on_task)
    print("listening", url, flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
