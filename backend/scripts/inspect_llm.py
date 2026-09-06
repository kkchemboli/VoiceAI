import sys
from livekit.agents import llm

class AssistantTools:
    @llm.function_tool(description="Test tool")
    async def transfer_call(self, reason: str):
        pass

try:
    fnc_ctx = AssistantTools()
    tools = llm.tool_context.find_function_tools(fnc_ctx)
    print(f"Discovered tools count: {len(tools)}")
    for t in tools:
        print(f"Tool: {t.info.name}")
except Exception as e:
    print(f"Error: {e}")
