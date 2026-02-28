# import asyncio
# import pandas as pd
# from session_store import store
# from agent_setup import agent


# async def main():
#     # 1. Create a session
#     session = store.create(user_id="aryan")
#     print(f"Session created: {session.session_id}\n")

#     # 2. Load your data
#     df = pd.read_csv(r"D:\Aryan Work\Projects\TestDA\data\Walmart_Sales.csv")
#     var_name = session.add_dataframe("walmart", df)
#     print(f"Loaded '{var_name}' — {df.shape[0]} rows x {df.shape[1]} cols\n")

#     # 3. Ask questions
#     config = {"configurable": {"thread_id": session.session_id}}
#     context = {"session_id": session.session_id}

#     questions = [
#         "What are the top 5 stores by total Weekly_Sales?",
#         "What is the average weekly sales per month?",
#         "WHat was the sales of store 14 on 24-12-2010?"
#     ]

#     for q in questions:
#         print(f"Q: {q}")
#         result = await agent.ainvoke(
#             {"messages": [{"role": "user", "content": q}]},
#             config=config,
#             context=context,
#         )
#         # Extract last AI message
#         for msg in reversed(result["messages"]):
#             if hasattr(msg, "type") and msg.type == "ai" and msg.content:
#                 print(f"A: {msg.content}\n")
#                 break
#         print("-" * 60)


# if __name__ == "__main__":
#     asyncio.run(main())
