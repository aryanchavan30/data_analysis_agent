import asyncio
import pandas as pd
from session_store import store
from agent_setup import agent


async def main():
    # 1. Create a session
    session = store.create(user_id="aryan")
    print(f"Session created: {session.session_id}\n")

    # 2. Load your files — just list them here
    files = [
        r"D:\Aryan Work\Projects\TestDA\data\Walmart_Sales.csv",        # CSV
        r"D:\Aryan Work\Projects\TestDA\data\Inventory.xlsx",           # Excel
        r"D:\Aryan Work\Projects\TestDA\data\Customer_Feedback.csv",    # CSV
    ]

    for filepath in files:
        path = pd.io.common.stringify_path(filepath)
        if path.endswith((".xlsx", ".xls")):
            df = pd.read_excel(filepath)
        else:
            df = pd.read_csv(filepath)

        # Use filename stem as the variable name (e.g. "Walmart_Sales")
        name = filepath.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        var_name = session.add_dataframe(name, df)
        print(f"Loaded '{var_name}' — {df.shape[0]} rows x {df.shape[1]} cols")

    # 3. Ask questions
    config = {"configurable": {"thread_id": session.session_id}}
    context = {"session_id": session.session_id}

    print()

    questions = [
        "What are the top 5 stores by total Weekly_Sales?",
        "Compare average inventory levels across all products",
        "Merge walmart sales with customer feedback and show any patterns",
    ]

    for q in questions:
        print("=" * 70)
        print(f"QUESTION: {q}")
        print("=" * 70)

        step = 0
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
                        if msg.content:
                            print(f"\n[Step {step}] THOUGHT:")
                            print(f"  {msg.content}")

                        tool_calls = getattr(msg, "tool_calls", [])
                        if tool_calls:
                            for tc in tool_calls:
                                print(f"\n[Step {step}] ACTION: call tool '{tc['name']}'")
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

        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
