"""End-to-end MCP test: spawn the stdio server like Claude Code would, list tools, call a few."""
import asyncio, json, os, sys, time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "evision_rgb.mcp_server"], cwd=ROOT)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])

            async def call(name, **args):
                res = await s.call_tool(name, args)
                txt = res.content[0].text if res.content else ""
                print(f"{name}({args}) -> {txt[:160]}")
                return txt

            await call("keyboard_status")
            await call("run_live_effect", effect="rainbow", params={"speed": 0.2})
            await asyncio.sleep(2)
            await call("set_live_keys", colors={"Esc": "ff0000", "F12": "ff0000"})
            await asyncio.sleep(2)
            await call("set_hardware_effect", mode="static", brightness=99)  # must be rejected, live keeps running
            await call("keyboard_status")
            await call("stop_live")


asyncio.run(main())
