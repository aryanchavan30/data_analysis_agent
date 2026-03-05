"""
test_hybrid_agent.py — End-to-end test for the hybrid RAG + Data Analysis Agent

Demonstrates all 3 tool routing scenarios:
    1. python_repl_ast     → pure CSV/Excel analysis
    2. rag_search_tool     → pure PDF document lookup
    3. hybrid_analysis_tool → PDF formula + CSV calculation

Run:
    python test_hybrid_agent.py
"""

import asyncio
import shutil
from pathlib import Path

import pandas as pd

from config import UPLOAD_DIR
from session_store import store
from agent_setup import agent
from rag_indexer import index_pdfs_for_session


async def main():
    # ── 1. Create session ─────────────────────────────────────────────────────
    session = store.create(user_id="test_user")
    print(f"\n{'='*70}")
    print(f"Session: {session.session_id}")
    print(f"{'='*70}\n")

    # ── 2. Load CSV (Walmart sales data) ─────────────────────────────────────
    csv_source = Path("/home/aryan-chavan/Aryan Work/StructureRAG/AgentV3/Walmart_Sales.csv")
    csv_dest = UPLOAD_DIR / f"{session.session_id}_Walmart_Sales.csv"
    shutil.copy2(csv_source, csv_dest)

    df = pd.read_csv(csv_dest)
    var_name = session.add_dataframe("walmart", df, file_path=str(csv_dest))
    print(f"✅ CSV loaded as `{var_name}` — {df.shape[0]} rows × {df.shape[1]} cols")
    print(f"   Columns: {list(df.columns)}\n")

    # ── 3. Load PDF (e.g. a retail analytics formula guide) ───────────────────
    # Replace with your actual PDF path
    pdf_source = Path("/home/aryan-chavan/Aryan Work/StructureRAG/AgentV3/retail_formulas.pdf")
    if pdf_source.exists():
        pdf_dest = UPLOAD_DIR / f"{session.session_id}_retail_formulas.pdf"
        shutil.copy2(pdf_source, pdf_dest)

        print(f"📄 Indexing PDF: {pdf_source.name} ...")
        summaries = await index_pdfs_for_session(
            session=session,
            pdf_paths=[str(pdf_dest)],
            filenames=[pdf_source.name],
        )
        print(f"✅ PDF indexed. Collection: {session.get_pdf_collection_name()}")
        for fname, desc in summaries.items():
            print(f"   {fname}: {desc[:100]}...")
    else:
        print("⚠️  No PDF found at 'retail_formulas.pdf' — RAG and hybrid tests will be limited.")

    print()

    # ── 4. Questions covering all 3 routing modes ──────────────────────────────
    questions = [
        # Mode 1: Pure CSV analysis → python_repl_ast
        "What are the top 5 stores by total Weekly_Sales?",
        "Show average weekly sales grouped by month.",
        "What was the Weekly_Sales of store 14 on 24-12-2010?",

        # Mode 2: Pure PDF lookup → rag_search_tool
        "What is the formula for Compound Annual Growth Rate (CAGR) as defined in the document?",
        "Explain the KPI definitions mentioned in the uploaded PDF.",

        # Mode 3: Hybrid — extract from PDF, apply to CSV → hybrid_analysis_tool
        "Use the CAGR formula from the PDF to calculate the sales growth for Store 1 across all years.",
        "Find the seasonality adjustment formula in the PDF and apply it to the weekly sales data.",
    ]

    config = {"configurable": {"thread_id": session.session_id}}
    context = {"session_id": session.session_id}

    for q in questions:
        print(f"\n{'='*70}")
        print(f"❓ QUESTION: {q}")
        print(f"{'='*70}")

        step = llm_calls = tool_count = 0
        tools_used = []

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

                for msg in update.get("messages", []):
                    msg_type = getattr(msg, "type", "unknown")

                    if msg_type == "ai":
                        llm_calls += 1
                        if msg.content:
                            print(f"\n[Step {step}] 🧠 THOUGHT:")
                            print(f"  {msg.content[:300]}{'...' if len(msg.content) > 300 else ''}")

                        for tc in getattr(msg, "tool_calls", []):
                            tool_count += 1
                            tools_used.append(tc["name"])
                            print(f"\n[Step {step}] 🔧 TOOL CALL: `{tc['name']}`")

                            if tc["name"] == "python_repl_ast":
                                code = tc["args"].get("query", "")
                                print(f"  Python code:")
                                for line in code.splitlines():
                                    print(f"    │ {line}")

                            elif tc["name"] == "rag_search_tool":
                                print(f"  PDF query: {tc['args'].get('query', '')}")

                            elif tc["name"] == "hybrid_analysis_tool":
                                print(f"  PDF query:  {tc['args'].get('pdf_query', '')}")
                                code = tc["args"].get("analysis_code", "")
                                print(f"  Analysis code:")
                                for line in code.splitlines():
                                    print(f"    │ {line}")

                    elif msg_type == "tool":
                        tool_name = getattr(msg, "name", "?")
                        content = msg.content or "(no output)"
                        print(f"\n[Step {step}] 📋 OBSERVATION from `{tool_name}`:")
                        for line in content.splitlines()[:15]:   # limit output
                            print(f"    > {line}")
                        if len(content.splitlines()) > 15:
                            print(f"    > ... ({len(content.splitlines())} lines total)")

        print(f"\n{'─'*70}")
        unique_tools = list(dict.fromkeys(tools_used))  # preserve order, dedup
        routing = (
            "🟦 python_repl_ast (CSV only)"     if unique_tools == ["python_repl_ast"] else
            "🟩 rag_search_tool (PDF only)"      if unique_tools == ["rag_search_tool"] else
            "🟨 hybrid_analysis_tool (PDF+CSV)"  if "hybrid_analysis_tool" in unique_tools else
            f"🔀 Mixed: {unique_tools}"
        )
        print(f"  Routing: {routing}")
        print(f"  Stats:   {llm_calls} LLM calls | {tool_count} tool calls | {step} steps")
        print(f"{'─'*70}")


if __name__ == "__main__":
    asyncio.run(main())