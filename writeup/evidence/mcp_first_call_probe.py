"""Start the MCP server over stdio and compare sequential vs concurrent tool calls."""
import asyncio
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FOLDER = Path(sys.argv[1])
ARGS = {"metric": "revenue", "start_date": "2005-05-24", "end_date": "2006-02-14", "group_by": "store"}


async def timed_call(session: ClientSession, label: str) -> None:
    started = time.time()
    try:
        result = await asyncio.wait_for(session.call_tool("aggregate", ARGS), timeout=60)
        print(f"{label}: ok in {time.time() - started:.1f}s, error={result.isError}", flush=True)
    except asyncio.TimeoutError:
        print(f"{label}: TIMED OUT after 60s", flush=True)


async def main() -> None:
    env = dict(os.environ, SNOWFLAKE_CONNECTION="claude", SNOWFLAKE_ROLE="A5_APP_RO", PYTHONWARNINGS="ignore")
    params = StdioServerParameters(command=str(FOLDER / ".venv/Scripts/python.exe"), args=[str(FOLDER / "mcp_server.py")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mode = sys.argv[2]
            if mode == "sequential":
                await timed_call(session, "call 1")
                await timed_call(session, "call 2")
            else:
                await asyncio.gather(timed_call(session, "parallel A"), timed_call(session, "parallel B"))
                await timed_call(session, "follow-up")


asyncio.run(main())
