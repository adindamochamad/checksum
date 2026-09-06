"""Gate check phase 2: a real Gemini agent driving ClickHouse through MCP.

This is the last GATE criterion. Needs GOOGLE_API_KEY (AI Studio, no billing).
    export GOOGLE_GENAI_USE_VERTEXAI=FALSE
    export GOOGLE_API_KEY=AIza...
    .venv/bin/python scripts/gate_agent.py
"""

import asyncio
import os
import sys

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from gate_check import build_toolset, load_env

MODEL = os.environ.get("CHECKSUM_MODEL", "gemini-2.5-flash")
APP = "checksum-gate"


def build_agent() -> LlmAgent:
    return LlmAgent(
        name="gate_probe",
        model=MODEL,
        instruction=(
            "You query ClickHouse using the run_query tool. "
            "Always call the tool -- never answer from memory. "
            "Report the number you got back, nothing else."
        ),
        tools=[build_toolset()],
    )


async def main() -> int:
    load_env()
    if not os.environ.get("GOOGLE_API_KEY"):
        print("BLOCKED  GOOGLE_API_KEY is not set -> https://aistudio.google.com/apikey")
        return 2
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

    runner = InMemoryRunner(agent=build_agent(), app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="gate")

    prompt = "Run exactly this query and tell me the number: SELECT count() FROM system.tables"
    tool_calls, final = [], ""

    async for event in runner.run_async(
        user_id="gate",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        for call in event.get_function_calls() or []:
            tool_calls.append(call.name)
            print(f"  tool call -> {call.name}({str(dict(call.args or {}))[:90]})")
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts)

    print(f"\n  agent said: {final.strip()[:220]}")

    if not tool_calls:
        print("\nFAIL  agent answered without ever calling the MCP tool")
        return 1
    print(f"\nPASS  Gemini drove {len(tool_calls)} MCP tool call(s) against real ClickHouse")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
