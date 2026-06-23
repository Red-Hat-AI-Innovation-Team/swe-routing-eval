"""Manual smoke test for AnthropicVertexClient."""

import os

from swe_routing_eval.llm import AnthropicVertexClient, Message, ToolCall, ToolDef, ToolResult

MODEL = "claude-sonnet-4-6@default"

client = AnthropicVertexClient(
    project_id=os.environ["ANTHROPIC_VERTEX_PROJECT_ID"],
    region=os.environ["CLOUD_ML_REGION"],
)

# -- simple tool def ----------------------------------------------------------

weather_tool = ToolDef(
    name="get_weather",
    description="Get the current weather for a city.",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
        },
        "required": ["city"],
    },
)

# -- turn 1: expect tool call -------------------------------------------------

messages = [
    Message(role="system", content="You are a helpful assistant. Use tools when appropriate."),
    Message(role="user", content="What's the weather in Tokyo?"),
]

print("=== Turn 1 ===")
r1 = client.chat(MODEL, messages, [weather_tool], max_tokens=1024)
print(f"content:    {r1.content}")
print(f"tool_calls: {r1.tool_calls}")
print(f"finished:   {r1.finished}")
print(f"tokens:     {r1.tokens_in} in / {r1.tokens_out} out")

# -- turn 2: feed tool result, expect text ------------------------------------

if r1.tool_calls:
    tc = r1.tool_calls[0]
    print(f"\n-> model called {tc.name}({tc.arguments})")

    messages.append(Message(role="assistant", content=r1.content, tool_calls=r1.tool_calls))
    messages.append(Message(
        role="user",
        tool_results=[ToolResult(tool_call_id=tc.id, content='{"temp_c": 22, "condition": "sunny"}')],
    ))

    print("\n=== Turn 2 ===")
    r2 = client.chat(MODEL, messages, [weather_tool], max_tokens=1024)
    print(f"content:    {r2.content}")
    print(f"tool_calls: {r2.tool_calls}")
    print(f"finished:   {r2.finished}")
    print(f"tokens:     {r2.tokens_in} in / {r2.tokens_out} out")
else:
    print("\nNo tool calls returned — model replied directly.")
