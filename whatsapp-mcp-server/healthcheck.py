"""Read-only stdio handshake and tool inventory check for the local WhatsApp MCP server."""
import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["main.py"], cwd=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {tool.name for tool in result.tools}
            required = {"list_messages", "search_all", "get_message_context", "send_message", "get_status"}
            missing = required - names
            if missing:
                print("missing tools: " + ", ".join(sorted(missing)))
                return 1
            print(f"MCP handshake ok; {len(names)} tools; required tools present")
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
