import asyncio
import shutil
from pathlib import Path
import atexit
import pandas as pd
from config import UPLOAD_DIR
from session_store import store
from agent_setup import agent


async def main():
    # 1. Create a session
    session = store.create(user_id="aryan")
    print(f"Session created: {session.session_id}\n")

    # 2. Load your data — copy to uploads dir so Snekbox can access via Docker mount
    source_path = Path(r"/home/aryan-chavan/Aryan Work/StructureRAG/DA Agent v2/data_analysis_agent/Walmart_Sales.csv")
    dest_path = UPLOAD_DIR / f"{session.session_id}_Walmart_Sales.csv"
    shutil.copy2(source_path, dest_path)
    atexit.register(lambda: dest_path.unlink(missing_ok=True))
    
    df = pd.read_csv(dest_path)
    var_name = session.add_dataframe("walmart", df, file_path=str(dest_path))
    print(f"Loaded '{var_name}' — {df.shape[0]} rows x {df.shape[1]} cols\n")

    # 3. Ask questions
    config = {"configurable": {"thread_id": session.session_id}}
    context = {"session_id": session.session_id}

    questions = [
        "What are the top 5 stores by total Weekly_Sales?",
        "What is the average weekly sales per month?",
        "WHat was the sales of store 14 on 24-12-2010?"
    ]

    for q in questions:
        print("=" * 70)
        print(f"QUESTION: {q}")
        print("=" * 70)

        step = 0
        llm_calls = 0
        tool_calls_count = 0

        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": q}]},
            config=config,
            context=context,
            stream_mode="updates",
        ):
            for node_name, update in chunk.items():
                step += 1
                if not isinstance(update, dict):
                    continue
                messages = update.get("messages", [])
                for msg in messages:
                    msg_type = getattr(msg, "type", "unknown")

                    # THOUGHT — LLM reasoning + deciding to call a tool
                    if msg_type == "ai":
                        llm_calls += 1
                        if msg.content:
                            print(f"\n[Step {step}] THOUGHT (LLM call #{llm_calls}):")
                            print(f"  {msg.content}")

                        tool_calls = getattr(msg, "tool_calls", [])
                        if tool_calls:
                            for tc in tool_calls:
                                tool_calls_count += 1
                                print(f"\n[Step {step}] ACTION (tool call #{tool_calls_count}): '{tc['name']}'")
                                code = tc["args"].get("query", "")
                                print(f"  Code:\n  ┌{'─'*50}")
                                for line in code.splitlines():
                                    print(f"  │ {line}")
                                print(f"  └{'─'*50}")

                    # OBSERVATION — tool execution result
                    elif msg_type == "tool":
                        tool_name = getattr(msg, "name", "?")
                        print(f"\n[Step {step}] OBSERVATION (from '{tool_name}'):")
                        content = msg.content or "(no output)"
                        for line in content.splitlines():
                            print(f"  > {line}")

        print(f"\n{'─'*70}")
        print(f"  STATS: {llm_calls} LLM call(s)  |  {tool_calls_count} tool call(s)  |  {step} total step(s)")
        print(f"{'─'*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
