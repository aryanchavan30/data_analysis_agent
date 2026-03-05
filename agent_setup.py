# import ast
# from typing import TypedDict, Optional

# import httpx
# from langchain_core.messages import ToolMessage
# from langchain_core.tools import StructuredTool
# from langchain_experimental.tools import PythonAstREPLTool
# from langchain_groq import ChatGroq
# from langchain.agents import create_agent
# from langchain.agents.middleware import (
#     ModelCallLimitMiddleware,
#     ModelRetryMiddleware,
#     PIIMiddleware,
#     SummarizationMiddleware,
#     ToolCallLimitMiddleware,
#     ToolRetryMiddleware,
#     dynamic_prompt,
#     wrap_tool_call,
# )
# from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
# from langgraph.checkpoint.memory import InMemorySaver
# from pydantic import BaseModel, Field

# from config import (
#     BANNED_FUNCTIONS,
#     BANNED_MODULES,
#     GROQ_API_KEY,
#     GROQ_MODEL,
#     MODEL_CALL_LIMIT,
#     SNEKBOX_TIMEOUT,
#     SNEKBOX_URL,
#     TOOL_CALL_LIMIT,
#     TOOL_RETRY_MAX,
# )
# from session_store import store
# from rag_tool import rag_search
# from llm import azure_llm
# # from logger import project_logger


# # ─────────────────────────────────────────────
# # Context schema
# # ─────────────────────────────────────────────

# class SessionContext(TypedDict):
#     session_id: str


# # ─────────────────────────────────────────────
# # LLM
# # ─────────────────────────────────────────────

# # llm = ChatGroq(
# #     model=GROQ_MODEL,
# #     api_key=GROQ_API_KEY,
# #     temperature=0,
# #     max_tokens=4096,
# # )
# llm = azure_llm

# # ─────────────────────────────────────────────
# # Tool input schemas
# # ─────────────────────────────────────────────

# class RagToolInput(BaseModel):
#     query: str = Field(
#         ...,
#         description=(
#             "Natural language question to search in the uploaded PDF/TXT documents. "
#             "Be specific — ask for definitions, formulas, thresholds, policies, "
#             "methodologies, or any factual content from the documents."
#         )
#     )


# class AnalysisToolInput(BaseModel):
#     query: str = Field(
#         ...,
#         description=(
#             "Valid Python code to execute for data analysis on uploaded CSV/Excel files. "
#             "DataFrames are pre-loaded as variables. "
#             "You can hardcode values retrieved from a previous rag_search_tool call. "
#             "Always use print() to display results."
#         )
#     )


# # ─────────────────────────────────────────────
# # Middleware #1: AST security sandbox
# # ─────────────────────────────────────────────

# @wrap_tool_call
# async def code_sandbox(request: ToolCallRequest, handler):
#     """
#     Intercepts python_repl_ast calls.
#     Parses code with AST before it reaches Snekbox.
#     Blocks banned imports, function calls, and dunder access.
#     Short-circuits with a ToolMessage on violation (code never executes).
#     """
#     if request.tool_call["name"] != "python_repl_ast":
#         return await handler(request)

#     code = request.tool_call["args"].get("query", "")
#     error = _ast_security_check(code, request.tool_call["id"])
#     if error:
#         return error

#     return await handler(request)


# def _ast_security_check(code: str, tool_call_id: str) -> Optional[ToolMessage]:
#     """Returns ToolMessage on violation, None if clean."""
#     try:
#         tree = ast.parse(code)
#     except SyntaxError as e:
#         return ToolMessage(content=f"SyntaxError: {e}", tool_call_id=tool_call_id)

#     for node in ast.walk(tree):
#         if isinstance(node, ast.Import):
#             for alias in node.names:
#                 root = alias.name.split(".")[0]
#                 if root in BANNED_MODULES:
#                     return ToolMessage(
#                         content=f"Security error: import of '{alias.name}' is not allowed.",
#                         tool_call_id=tool_call_id,
#                     )
#         elif isinstance(node, ast.ImportFrom):
#             if node.module:
#                 root = node.module.split(".")[0]
#                 if root in BANNED_MODULES:
#                     return ToolMessage(
#                         content=f"Security error: import from '{node.module}' is not allowed.",
#                         tool_call_id=tool_call_id,
#                     )
#         elif isinstance(node, ast.Call):
#             func_name = None
#             if isinstance(node.func, ast.Name):
#                 func_name = node.func.id
#             elif isinstance(node.func, ast.Attribute):
#                 func_name = node.func.attr
#             if func_name and func_name in BANNED_FUNCTIONS:
#                 return ToolMessage(
#                     content=f"Security error: call to '{func_name}' is not allowed.",
#                     tool_call_id=tool_call_id,
#                 )
#         elif isinstance(node, ast.Attribute):
#             if node.attr.startswith("__") and node.attr.endswith("__"):
#                 return ToolMessage(
#                     content=f"Security error: dunder access to '{node.attr}' is not allowed.",
#                     tool_call_id=tool_call_id,
#                 )
#     return None


# # ─────────────────────────────────────────────
# # Middleware #2: Snekbox router
# # ─────────────────────────────────────────────

# @wrap_tool_call
# async def session_tool_router(request: ToolCallRequest, handler):
#     """
#     Routes python_repl_ast to Snekbox Docker sandbox.

#     Snekbox is stateless — every execution is a fresh Python process.
#     We prepend session.get_setup_code() which re-imports pandas/numpy
#     and reloads all DataFrames from /data/ (Docker volume mount).
#     """
#     if request.tool_call["name"] != "python_repl_ast":
#         return await handler(request)

#     code = request.tool_call["args"].get("query", "")
#     tool_call_id = request.tool_call["id"]

#     session_id = request.runtime.context.get("session_id")
#     if not session_id:
#         return ToolMessage(content="Error: no session_id in context.", tool_call_id=tool_call_id)

#     session = store.get(session_id)
#     if not session:
#         return ToolMessage(content=f"Error: session '{session_id}' not found.", tool_call_id=tool_call_id)

#     setup = session.get_setup_code()
#     full_code = setup + "\n\n" + code

#     # project_logger.info(f"[Snekbox] session={session_id} | code_len={len(code)}")

#     try:
#         async with httpx.AsyncClient() as client:
#             resp = await client.post(
#                 SNEKBOX_URL,
#                 json={"input": full_code},
#                 timeout=SNEKBOX_TIMEOUT,
#             )
#             resp.raise_for_status()

#         result = resp.json()
#         stdout = result.get("stdout", "")
#         returncode = result.get("returncode", None)

#         if returncode and returncode != 0:
#             output = stdout if stdout else "Code execution failed (non-zero exit code)."
#         elif stdout:
#             output = stdout
#         else:
#             output = "Code executed successfully (no output)."

#     except httpx.TimeoutException:
#         output = "Execution error: code timed out."
#     except httpx.HTTPStatusError as e:
#         output = f"Execution error: Snekbox returned HTTP {e.response.status_code}."
#     except httpx.ConnectError:
#         output = "Execution error: could not connect to Snekbox. Is the container running?"
#     except Exception as e:
#         output = f"Execution error: {type(e).__name__}: {e}"

#     # project_logger.info(f"[Snekbox] output preview: {output[:200]}")
#     return ToolMessage(content=output, tool_call_id=tool_call_id)


# # ─────────────────────────────────────────────
# # Middleware #3: RAG tool router
# # ─────────────────────────────────────────────

# @wrap_tool_call
# async def rag_tool_router(request: ToolCallRequest, handler):
#     """Routes rag_search_tool to Qdrant vector search + LLM synthesis."""
#     if request.tool_call["name"] != "rag_search_tool":
#         return await handler(request)

#     query = request.tool_call["args"].get("query", "")
#     tool_call_id = request.tool_call["id"]
#     session_id = request.runtime.context.get("session_id")

#     if not session_id:
#         return ToolMessage(content="Error: no session_id.", tool_call_id=tool_call_id)

#     session = store.get(session_id)
#     if not session:
#         return ToolMessage(content=f"Error: session '{session_id}' not found.", tool_call_id=tool_call_id)

#     if not session.has_pdfs():
#         return ToolMessage(
#             content=(
#                 "No PDF/TXT documents uploaded for this session. "
#                 "Please upload documents first via the /upload/pdf endpoint."
#             ),
#             tool_call_id=tool_call_id,
#         )

#     # project_logger.info(f"[RAG] query='{query}' session={session_id}")

#     try:
#         result = await rag_search(query=query, session_id=session_id)
#     except Exception as e:
#         result = f"RAG search error: {type(e).__name__}: {e}"
#         # project_logger.error(f"[RAG] Error: {e}", exc_info=True)

#     return ToolMessage(content=result, tool_call_id=tool_call_id)


# # ─────────────────────────────────────────────
# # Middleware #4: Dynamic system prompt
# # ─────────────────────────────────────────────

# @dynamic_prompt
# def data_aware_prompt(request: ModelRequest) -> str:
#     """
#     Rebuilds the system prompt on every LLM call.
#     Injects: current CSV schemas, variable names, PDF document summaries.
#     This ensures the LLM always knows what data is available.
#     """
#     session_id = request.runtime.context.get("session_id")
#     if not session_id:
#         return _build_system_prompt("No session.", [], "No PDFs.")

#     session = store.get(session_id)
#     if not session:
#         return _build_system_prompt("Session not found.", [], "No PDFs.")

#     return _build_system_prompt(
#         csv_summary=session.get_data_summary(),
#         variable_names=session.get_variable_names(),
#         pdf_summary=session.get_pdf_summary(),
#     )


# def _build_system_prompt(
#     csv_summary: str,
#     variable_names: list[str],
#     pdf_summary: str,
# ) -> str:
#     vars_str = ", ".join(f"`{v}`" for v in variable_names) if variable_names else "None loaded"

#     return f"""You are an expert data analyst and document intelligence assistant.

# You have TWO tools. You can call them multiple times, in any order.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔧 TOOLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 1. `rag_search_tool`
#    Search uploaded PDF/TXT documents for any information.
#    Use for: definitions, formulas, thresholds, policies, methodologies,
#             product specs, rules, classification criteria, or any document content.
#    Returns: relevant text with source citations.

# 2. `python_repl_ast`
#    Execute Python code on uploaded CSV/Excel data.
#    Use for: filtering, aggregation, statistics, calculations, plotting.
#    Pre-loaded DataFrames: {vars_str}
#    Always print() results.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔄 MULTI-STEP REASONING — HOW TO HANDLE HYBRID QUERIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# When a query needs BOTH documents and data, call tools in sequence.
# You see each tool's result before deciding your next step.

# Examples:

#   "Use the depreciation formula from the PDF on the assets CSV"
#     → Step 1: rag_search_tool("depreciation formula")
#     → Step 2: Read result. Extract the formula values.
#     → Step 3: python_repl_ast(code using the actual formula from step 1)

#   "Flag any CSV rows that violate the risk thresholds in the compliance doc"
#     → Step 1: rag_search_tool("risk threshold values")
#     → Step 2: Read result. Note the exact threshold numbers.
#     → Step 3: python_repl_ast(filter rows using those thresholds)

#   "Classify each product in the spreadsheet using the PDF's category rules"
#     → Step 1: rag_search_tool("product category classification rules")
#     → Step 2: python_repl_ast(apply classification logic to each row)
#     → Step 3: rag_search_tool("edge cases or exceptions in classification")
#     → Step 4: python_repl_ast(handle edge cases, show final result)

#   "Analyze the data and then check if the findings match what the report says"
#     → Step 1: python_repl_ast(run the analysis)
#     → Step 2: rag_search_tool("expected findings or benchmarks from report")
#     → Step 3: Synthesize comparison

# YOU decide how many steps are needed. Do not stop early.
# Read each tool result carefully before writing the next tool call.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📊 LOADED CSV/EXCEL DATA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# {csv_summary}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📄 LOADED DOCUMENTS (PDF/TXT)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# {pdf_summary}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 RULES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# - Always print() in Python code to display results.
# - Do NOT import os, subprocess, sys, socket, or system modules.
# - Limit output to .head(10) unless asked for more.
# - WAIT for rag_search_tool results before writing code that depends on them.
# - Hardcode specific values from RAG results into your Python code.
# - Cite document sources in your final answer.
# - If info is missing from documents or data, say so clearly.
# """


# # ─────────────────────────────────────────────
# # Tool definitions
# # (schemas only — execution via middleware)
# # ─────────────────────────────────────────────

# python_repl_tool = PythonAstREPLTool(
#     name="python_repl_ast",
#     description=(
#         "Execute Python code on uploaded CSV/Excel files. "
#         "DataFrames are pre-loaded. Use pandas (pd), numpy (np). "
#         "You can hardcode values from rag_search_tool results into this code. "
#         "Always use print() to show results."
#     ),
# )


# def _rag_placeholder(query: str) -> str:
#     """Placeholder — execution handled by rag_tool_router middleware."""
#     return f"[RAG placeholder — should never appear] {query}"

# rag_search_tool = StructuredTool.from_function(
#     func=_rag_placeholder,
#     name="rag_search_tool",
#     description=(
#         "Search uploaded PDF/TXT documents. "
#         "Call this BEFORE python_repl_ast when your analysis depends on document content. "
#         "Returns relevant text with citations."
#     ),
#     args_schema=RagToolInput,
# )


# # ─────────────────────────────────────────────
# # Middleware stack (outermost → innermost)
# # ─────────────────────────────────────────────

# middleware_stack = [
#     PIIMiddleware("email", strategy="redact", apply_to_input=True),
#     PIIMiddleware("credit_card", strategy="mask", apply_to_input=True),
#     data_aware_prompt,
#     SummarizationMiddleware(model=llm, trigger=("messages", 40), keep=("messages", 20)),
#     ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
#     ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
#     code_sandbox,
#     ToolRetryMiddleware(max_retries=TOOL_RETRY_MAX),
#     ModelRetryMiddleware(max_retries=TOOL_RETRY_MAX),
#     rag_tool_router,
#     session_tool_router,   # innermost — executes Python in Snekbox
# ]


# # ─────────────────────────────────────────────
# # Agent — only 2 clean tools, no hybrid tool
# # ─────────────────────────────────────────────

# agent = create_agent(
#     model=llm,
#     tools=[python_repl_tool, rag_search_tool],
#     middleware=middleware_stack,
#     context_schema=SessionContext,
#     checkpointer=InMemorySaver(),
#     name="hybrid_data_rag_agent",
# )


# import ast
# from typing import TypedDict, Optional

# import httpx
# from langchain_core.messages import ToolMessage
# from langchain_core.tools import StructuredTool
# from langchain_experimental.tools import PythonAstREPLTool
# from langchain_groq import ChatGroq
# from langchain.agents import create_agent
# from langchain.agents.middleware import (
#     ModelCallLimitMiddleware,
#     ModelRetryMiddleware,
#     PIIMiddleware,
#     SummarizationMiddleware,
#     ToolCallLimitMiddleware,
#     ToolRetryMiddleware,
#     dynamic_prompt,
#     wrap_tool_call,
# )
# from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
# from langgraph.checkpoint.memory import InMemorySaver
# from pydantic import BaseModel, Field

# from config import (
#     BANNED_FUNCTIONS,
#     BANNED_MODULES,
#     GROQ_API_KEY,
#     GROQ_MODEL,
#     MODEL_CALL_LIMIT,
#     SNEKBOX_TIMEOUT,
#     SNEKBOX_URL,
#     TOOL_CALL_LIMIT,
#     TOOL_RETRY_MAX,
# )
# from session_store import store
# from rag_tool import rag_search
# from llm import azure_llm
# # from logger import project_logger


# # ─────────────────────────────────────────────
# # Context schema
# # ─────────────────────────────────────────────

# class SessionContext(TypedDict):
#     session_id: str


# # ─────────────────────────────────────────────
# # LLM
# # ─────────────────────────────────────────────

# llm = azure_llm


# # ─────────────────────────────────────────────
# # Tool input schemas
# # ─────────────────────────────────────────────

# class RagToolInput(BaseModel):
#     query: str = Field(
#         ...,
#         description=(
#             "Natural language question to search in the uploaded PDF/TXT documents. "
#             "Be specific — ask for definitions, formulas, thresholds, policies, "
#             "methodologies, or any factual content from the documents."
#         )
#     )


# class AnalysisToolInput(BaseModel):
#     query: str = Field(
#         ...,
#         description=(
#             "Valid Python code to execute for data analysis on uploaded CSV/Excel files. "
#             "DataFrames are pre-loaded as variables. "
#             "You can hardcode values retrieved from a previous rag_search_tool call. "
#             "Always use print() to display results."
#         )
#     )


# # ─────────────────────────────────────────────
# # Middleware #1: AST security sandbox
# # ─────────────────────────────────────────────

# @wrap_tool_call
# async def code_sandbox(request: ToolCallRequest, handler):
#     """
#     Intercepts python_repl_ast calls.
#     Parses code with AST before it reaches Snekbox.
#     Blocks banned imports, function calls, and dunder access.
#     Short-circuits with a ToolMessage on violation (code never executes).
#     """
#     if request.tool_call["name"] != "python_repl_ast":
#         return await handler(request)

#     code = request.tool_call["args"].get("query", "")
#     error = _ast_security_check(code, request.tool_call["id"])
#     if error:
#         return error

#     return await handler(request)


# def _ast_security_check(code: str, tool_call_id: str) -> Optional[ToolMessage]:
#     """Returns ToolMessage on violation, None if clean."""
#     try:
#         tree = ast.parse(code)
#     except SyntaxError as e:
#         return ToolMessage(content=f"SyntaxError: {e}", tool_call_id=tool_call_id)

#     for node in ast.walk(tree):
#         if isinstance(node, ast.Import):
#             for alias in node.names:
#                 root = alias.name.split(".")[0]
#                 if root in BANNED_MODULES:
#                     return ToolMessage(
#                         content=f"Security error: import of '{alias.name}' is not allowed.",
#                         tool_call_id=tool_call_id,
#                     )
#         elif isinstance(node, ast.ImportFrom):
#             if node.module:
#                 root = node.module.split(".")[0]
#                 if root in BANNED_MODULES:
#                     return ToolMessage(
#                         content=f"Security error: import from '{node.module}' is not allowed.",
#                         tool_call_id=tool_call_id,
#                     )
#         elif isinstance(node, ast.Call):
#             func_name = None
#             if isinstance(node.func, ast.Name):
#                 func_name = node.func.id
#             elif isinstance(node.func, ast.Attribute):
#                 func_name = node.func.attr
#             if func_name and func_name in BANNED_FUNCTIONS:
#                 return ToolMessage(
#                     content=f"Security error: call to '{func_name}' is not allowed.",
#                     tool_call_id=tool_call_id,
#                 )
#         elif isinstance(node, ast.Attribute):
#             if node.attr.startswith("__") and node.attr.endswith("__"):
#                 return ToolMessage(
#                     content=f"Security error: dunder access to '{node.attr}' is not allowed.",
#                     tool_call_id=tool_call_id,
#                 )
#     return None


# def _enforce_print(code: str) -> str:
#     """
#     Auto-wraps bare expression statements with print() so results are
#     always visible to the LLM.

#     Handles common patterns the LLM writes without print():
#         df.head()                           → print(df.head())
#         df.groupby(...)['col'].sum()        → print(df.groupby(...)['col'].sum())
#         result                              → print(result)

#     Leaves alone:
#         print(...)          already has print
#         x = df.head()       assignment — not an expression statement
#         import / def / for  control flow
#     """
#     try:
#         tree = ast.parse(code)
#     except SyntaxError:
#         return code  # let execution surface the syntax error naturally

#     lines = code.splitlines()
#     # Collect line numbers of bare Expr nodes that aren't already print() calls
#     wrap_lines = set()
#     for node in ast.walk(tree):
#         if isinstance(node, ast.Expr):
#             # Skip if it's already a print() call
#             if (
#                 isinstance(node.value, ast.Call)
#                 and isinstance(node.value.func, ast.Name)
#                 and node.value.func.id == "print"
#             ):
#                 continue
#             wrap_lines.add(node.lineno)  # 1-based

#     if not wrap_lines:
#         return code

#     new_lines = []
#     for i, line in enumerate(lines, start=1):
#         if i in wrap_lines:
#             stripped = line.lstrip()
#             indent = line[: len(line) - len(stripped)]
#             new_lines.append(f"{indent}print({stripped})")
#         else:
#             new_lines.append(line)

#     return "\n".join(new_lines)


# # ─────────────────────────────────────────────
# # Middleware #2: Snekbox router
# # ─────────────────────────────────────────────

# @wrap_tool_call
# async def session_tool_router(request: ToolCallRequest, handler):
#     """
#     Routes python_repl_ast to Snekbox Docker sandbox.

#     Snekbox is stateless — every execution is a fresh Python process.
#     We prepend session.get_setup_code() which re-imports pandas/numpy
#     and reloads all DataFrames from /data/ (Docker volume mount).
#     """
#     if request.tool_call["name"] != "python_repl_ast":
#         return await handler(request)

#     code = request.tool_call["args"].get("query", "")
#     tool_call_id = request.tool_call["id"]

#     session_id = request.runtime.context.get("session_id")
#     if not session_id:
#         return ToolMessage(content="Error: no session_id in context.", tool_call_id=tool_call_id)

#     session = store.get(session_id)
#     if not session:
#         return ToolMessage(content=f"Error: session '{session_id}' not found.", tool_call_id=tool_call_id)

#     # Auto-wrap bare expressions with print() so LLM always sees output
#     code = _enforce_print(code)
#     setup = session.get_setup_code()
#     full_code = setup + "\n\n" + code

#     # project_logger.info(f"[Snekbox] session={session_id} | code_len={len(code)}")
#     # project_logger.debug(f"[Snekbox] full_code sent:\n{full_code}")

#     try:
#         async with httpx.AsyncClient() as client:
#             resp = await client.post(
#                 SNEKBOX_URL,
#                 json={"input": full_code},
#                 timeout=SNEKBOX_TIMEOUT,
#             )
#             resp.raise_for_status()

#         result = resp.json()
#         # project_logger.info(f"[Snekbox] raw response: {result}")

#         # Snekbox merges stdout+stderr into the "stdout" key.
#         # returncode != 0 means a runtime error occurred — we still surface
#         # the traceback to the LLM so it can self-correct its code.
#         stdout = result.get("stdout", "").strip()
#         returncode = result.get("returncode", 0)

#         if stdout and returncode != 0:
#             # Show traceback — LLM uses this to fix its code
#             output = f"Execution error (exit code {returncode}):\n{stdout}"
#         elif stdout:
#             output = stdout
#         else:
#             # Empty output almost always means LLM forgot print()
#             output = (
#                 "Code ran but produced no output. "
#                 "You MUST use print() to display results. "
#                 "Example: print(df.groupby(\'Store\')[\'Weekly_Sales\'].sum().nlargest(5))"
#             )

#     except httpx.TimeoutException:
#         output = "Execution error: code timed out."
#     except httpx.HTTPStatusError as e:
#         output = f"Execution error: Snekbox returned HTTP {e.response.status_code}."
#     except httpx.ConnectError:
#         output = "Execution error: could not connect to Snekbox. Is the container running?"
#     except Exception as e:
#         output = f"Execution error: {type(e).__name__}: {e}"

#     # project_logger.info(f"[Snekbox] output preview: {output[:300]}")
#     return ToolMessage(content=output, tool_call_id=tool_call_id, name="python_repl_ast")


# # ─────────────────────────────────────────────
# # Middleware #3: RAG tool router
# # ─────────────────────────────────────────────

# @wrap_tool_call
# async def rag_tool_router(request: ToolCallRequest, handler):
#     """Routes rag_search_tool to Qdrant vector search + LLM synthesis."""
#     if request.tool_call["name"] != "rag_search_tool":
#         return await handler(request)

#     query = request.tool_call["args"].get("query", "")
#     tool_call_id = request.tool_call["id"]
#     session_id = request.runtime.context.get("session_id")

#     if not session_id:
#         return ToolMessage(content="Error: no session_id.", tool_call_id=tool_call_id)

#     session = store.get(session_id)
#     if not session:
#         return ToolMessage(content=f"Error: session '{session_id}' not found.", tool_call_id=tool_call_id)

#     if not session.has_pdfs():
#         return ToolMessage(
#             content=(
#                 "No PDF/TXT documents uploaded for this session. "
#                 "Please upload documents first via the /upload/pdf endpoint."
#             ),
#             tool_call_id=tool_call_id,
#         )

#     # project_logger.info(f"[RAG] query='{query}' session={session_id}")

#     try:
#         result = await rag_search(query=query, session_id=session_id)
#     except Exception as e:
#         result = f"RAG search error: {type(e).__name__}: {e}"
#         # project_logger.error(f"[RAG] Error: {e}", exc_info=True)

#     return ToolMessage(content=result, tool_call_id=tool_call_id, name="rag_search_tool")


# # ─────────────────────────────────────────────
# # Middleware #4: Dynamic system prompt
# # ─────────────────────────────────────────────

# @dynamic_prompt
# def data_aware_prompt(request: ModelRequest) -> str:
#     """
#     Rebuilds the system prompt on every LLM call.
#     Injects: current CSV schemas, variable names, PDF document summaries.
#     This ensures the LLM always knows what data is available.
#     """
#     session_id = request.runtime.context.get("session_id")
#     if not session_id:
#         return _build_system_prompt("No session.", [], "No PDFs.")

#     session = store.get(session_id)
#     if not session:
#         return _build_system_prompt("Session not found.", [], "No PDFs.")

#     return _build_system_prompt(
#         csv_summary=session.get_data_summary(),
#         variable_names=session.get_variable_names(),
#         pdf_summary=session.get_pdf_summary(),
#     )


# def _build_system_prompt(
#     csv_summary: str,
#     variable_names: list[str],
#     pdf_summary: str,
# ) -> str:
#     vars_str = ", ".join(f"`{v}`" for v in variable_names) if variable_names else "None loaded"

#     return f"""You are an expert data analyst and document intelligence assistant.

# You have TWO tools. You can call them multiple times, in any order.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔧 TOOLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 1. `rag_search_tool`
#    Search uploaded PDF/TXT documents for any information.
#    Use for: definitions, formulas, thresholds, policies, methodologies,
#             product specs, rules, classification criteria, or any document content.
#    Returns: relevant text with source citations.

# 2. `python_repl_ast`
#    Execute Python code on uploaded CSV/Excel data.
#    Use for: filtering, aggregation, statistics, calculations, plotting.
#    Pre-loaded DataFrames: {vars_str}
#    Always print() results.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔄 MULTI-STEP REASONING — HOW TO HANDLE HYBRID QUERIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# When a query needs BOTH documents and data, call tools in sequence.
# You see each tool's result before deciding your next step.

# Examples:

#   "Use the depreciation formula from the PDF on the assets CSV"
#     → Step 1: rag_search_tool("depreciation formula")
#     → Step 2: Read result. Extract the formula values.
#     → Step 3: python_repl_ast(code using the actual formula from step 1)

#   "Flag any CSV rows that violate the risk thresholds in the compliance doc"
#     → Step 1: rag_search_tool("risk threshold values")
#     → Step 2: Read result. Note the exact threshold numbers.
#     → Step 3: python_repl_ast(filter rows using those thresholds)

#   "Classify each product in the spreadsheet using the PDF's category rules"
#     → Step 1: rag_search_tool("product category classification rules")
#     → Step 2: python_repl_ast(apply classification logic to each row)
#     → Step 3: rag_search_tool("edge cases or exceptions in classification")
#     → Step 4: python_repl_ast(handle edge cases, show final result)

#   "Analyze the data and then check if the findings match what the report says"
#     → Step 1: python_repl_ast(run the analysis)
#     → Step 2: rag_search_tool("expected findings or benchmarks from report")
#     → Step 3: Synthesize comparison

# YOU decide how many steps are needed. Do not stop early.
# Read each tool result carefully before writing the next tool call.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📊 LOADED CSV/EXCEL DATA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# {csv_summary}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📄 LOADED DOCUMENTS (PDF/TXT)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# {pdf_summary}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 RULES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# - Always print() in Python code to display results.
# - Do NOT import os, subprocess, sys, socket, or system modules.
# - Limit output to .head(10) unless asked for more.
# - WAIT for rag_search_tool results before writing code that depends on them.
# - Hardcode specific values from RAG results into your Python code.
# - Cite document sources in your final answer.
# - If info is missing from documents or data, say so clearly.
# """


# # ─────────────────────────────────────────────
# # Tool definitions
# # (schemas only — execution via middleware)
# # ─────────────────────────────────────────────

# python_repl_tool = PythonAstREPLTool(
#     name="python_repl_ast",
#     description=(
#         "Execute Python code on uploaded CSV/Excel files. "
#         "DataFrames are pre-loaded. Use pandas (pd), numpy (np). "
#         "You can hardcode values from rag_search_tool results into this code. "
#         "Always use print() to show results."
#     ),
# )


# def _rag_placeholder(query: str) -> str:
#     """Placeholder — execution handled by rag_tool_router middleware."""
#     return f"[RAG placeholder — should never appear] {query}"

# rag_search_tool = StructuredTool.from_function(
#     func=_rag_placeholder,
#     name="rag_search_tool",
#     description=(
#         "Search uploaded PDF/TXT documents. "
#         "Call this BEFORE python_repl_ast when your analysis depends on document content. "
#         "Returns relevant text with citations."
#     ),
#     args_schema=RagToolInput,
# )


# # ─────────────────────────────────────────────
# # Middleware stack (outermost → innermost)
# # ─────────────────────────────────────────────

# middleware_stack = [
#     PIIMiddleware("email", strategy="redact", apply_to_input=True),
#     PIIMiddleware("credit_card", strategy="mask", apply_to_input=True),
#     data_aware_prompt,
#     SummarizationMiddleware(model=llm, trigger=("messages", 40), keep=("messages", 20)),
#     ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
#     ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
#     code_sandbox,
#     ToolRetryMiddleware(max_retries=TOOL_RETRY_MAX),
#     ModelRetryMiddleware(max_retries=TOOL_RETRY_MAX),
#     rag_tool_router,
#     session_tool_router,   # innermost — executes Python in Snekbox
# ]


# # ─────────────────────────────────────────────
# # Agent — only 2 clean tools, no hybrid tool
# # ─────────────────────────────────────────────

# agent = create_agent(
#     model=llm,
#     tools=[python_repl_tool, rag_search_tool],
#     middleware=middleware_stack,
#     context_schema=SessionContext,
#     checkpointer=InMemorySaver(),
#     name="hybrid_data_rag_agent",
# )


import ast
from typing import TypedDict, Optional

import httpx
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langchain_experimental.tools import PythonAstREPLTool
from langchain_groq import ChatGroq
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
    dynamic_prompt,
    wrap_tool_call,
)
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
import db  # PostgreSQL checkpointer + store — initialised in main.py lifespan
from pydantic import BaseModel, Field

from config import (
    BANNED_FUNCTIONS,
    BANNED_MODULES,
    GROQ_API_KEY,
    GROQ_MODEL,
    MODEL_CALL_LIMIT,
    SNEKBOX_TIMEOUT,
    SNEKBOX_URL,
    TOOL_CALL_LIMIT,
    TOOL_RETRY_MAX,
)
from session_store import store
from rag_tool import rag_search
# from logger import project_logger
from llm import azure_llm


# ─────────────────────────────────────────────
# Context schema
# ─────────────────────────────────────────────

class SessionContext(TypedDict):
    session_id: str


# ─────────────────────────────────────────────
# LLM
# ─────────────────────────────────────────────

llm = azure_llm


# ─────────────────────────────────────────────
# Tool input schemas
# ─────────────────────────────────────────────

class RagToolInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Natural language question to search in the uploaded PDF/TXT documents. "
            "Be specific — ask for definitions, formulas, thresholds, policies, "
            "methodologies, or any factual content from the documents."
        )
    )


class AnalysisToolInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Valid Python code to execute for data analysis on uploaded CSV/Excel files. "
            "DataFrames are pre-loaded as variables. "
            "You can hardcode values retrieved from a previous rag_search_tool call. "
            "Always use print() to display results."
        )
    )


# ─────────────────────────────────────────────
# Middleware #1: AST security sandbox
# ─────────────────────────────────────────────

@wrap_tool_call
async def code_sandbox(request: ToolCallRequest, handler):
    """
    Intercepts python_repl_ast calls.
    Parses code with AST before it reaches Snekbox.
    Blocks banned imports, function calls, and dunder access.
    Short-circuits with a ToolMessage on violation (code never executes).
    """
    if request.tool_call["name"] != "python_repl_ast":
        return await handler(request)

    code = request.tool_call["args"].get("query", "")
    error = _ast_security_check(code, request.tool_call["id"])
    if error:
        return error

    return await handler(request)


def _ast_security_check(code: str, tool_call_id: str) -> Optional[ToolMessage]:
    """Returns ToolMessage on violation, None if clean."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return ToolMessage(content=f"SyntaxError: {e}", tool_call_id=tool_call_id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_MODULES:
                    return ToolMessage(
                        content=f"Security error: import of '{alias.name}' is not allowed.",
                        tool_call_id=tool_call_id,
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in BANNED_MODULES:
                    return ToolMessage(
                        content=f"Security error: import from '{node.module}' is not allowed.",
                        tool_call_id=tool_call_id,
                    )
        elif isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name and func_name in BANNED_FUNCTIONS:
                return ToolMessage(
                    content=f"Security error: call to '{func_name}' is not allowed.",
                    tool_call_id=tool_call_id,
                )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return ToolMessage(
                    content=f"Security error: dunder access to '{node.attr}' is not allowed.",
                    tool_call_id=tool_call_id,
                )
    return None


def _enforce_print(code: str) -> str:
    """
    Auto-wraps bare expression statements with print() so results are
    always visible to the LLM.

    Handles common patterns the LLM writes without print():
        df.head()                           → print(df.head())
        df.groupby(...)['col'].sum()        → print(df.groupby(...)['col'].sum())
        result                              → print(result)

    Leaves alone:
        print(...)          already has print
        x = df.head()       assignment — not an expression statement
        import / def / for  control flow
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code  # let execution surface the syntax error naturally

    lines = code.splitlines()
    # Collect line numbers of bare Expr nodes that aren't already print() calls
    wrap_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr):
            # Skip if it's already a print() call
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "print"
            ):
                continue
            wrap_lines.add(node.lineno)  # 1-based

    if not wrap_lines:
        return code

    new_lines = []
    for i, line in enumerate(lines, start=1):
        if i in wrap_lines:
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]
            new_lines.append(f"{indent}print({stripped})")
        else:
            new_lines.append(line)

    return "\n".join(new_lines)


# ─────────────────────────────────────────────
# Middleware #2: Snekbox router
# ─────────────────────────────────────────────

@wrap_tool_call
async def session_tool_router(request: ToolCallRequest, handler):
    """
    Routes python_repl_ast to Snekbox Docker sandbox.

    Snekbox is stateless — every execution is a fresh Python process.
    We prepend session.get_setup_code() which re-imports pandas/numpy
    and reloads all DataFrames from /data/ (Docker volume mount).
    """
    if request.tool_call["name"] != "python_repl_ast":
        return await handler(request)

    code = request.tool_call["args"].get("query", "")
    tool_call_id = request.tool_call["id"]

    session_id = request.runtime.context.get("session_id")
    if not session_id:
        return ToolMessage(content="Error: no session_id in context.", tool_call_id=tool_call_id)

    session = store.get(session_id)
    if not session:
        return ToolMessage(content=f"Error: session '{session_id}' not found.", tool_call_id=tool_call_id)

    # Auto-wrap bare expressions with print() so LLM always sees output
    code = _enforce_print(code)
    setup = session.get_setup_code()
    full_code = setup + "\n\n" + code

    # project_logger.info(f"[Snekbox] session={session_id} | code_len={len(code)}")
    # project_logger.debug(f"[Snekbox] full_code sent:\n{full_code}")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SNEKBOX_URL,
                json={"input": full_code},
                timeout=SNEKBOX_TIMEOUT,
            )
            resp.raise_for_status()

        result = resp.json()
        # project_logger.info(f"[Snekbox] raw response: {result}")

        # Snekbox merges stdout+stderr into the "stdout" key.
        # returncode != 0 means a runtime error occurred — we still surface
        # the traceback to the LLM so it can self-correct its code.
        stdout = result.get("stdout", "").strip()
        returncode = result.get("returncode", 0)

        if stdout and returncode != 0:
            # Show traceback — LLM uses this to fix its code
            output = f"Execution error (exit code {returncode}):\n{stdout}"
        elif stdout:
            output = stdout
        else:
            # Empty output almost always means LLM forgot print()
            output = (
                "Code ran but produced no output. "
                "You MUST use print() to display results. "
                "Example: print(df.groupby(\'Store\')[\'Weekly_Sales\'].sum().nlargest(5))"
            )

    except httpx.TimeoutException:
        output = "Execution error: code timed out."
    except httpx.HTTPStatusError as e:
        output = f"Execution error: Snekbox returned HTTP {e.response.status_code}."
    except httpx.ConnectError:
        output = "Execution error: could not connect to Snekbox. Is the container running?"
    except Exception as e:
        output = f"Execution error: {type(e).__name__}: {e}"

    # project_logger.info(f"[Snekbox] output preview: {output[:300]}")
    return ToolMessage(content=output, tool_call_id=tool_call_id, name="python_repl_ast")


# ─────────────────────────────────────────────
# Middleware #3: RAG tool router
# ─────────────────────────────────────────────

@wrap_tool_call
async def rag_tool_router(request: ToolCallRequest, handler):
    """Routes rag_search_tool to Qdrant vector search + LLM synthesis."""
    if request.tool_call["name"] != "rag_search_tool":
        return await handler(request)

    query = request.tool_call["args"].get("query", "")
    tool_call_id = request.tool_call["id"]
    session_id = request.runtime.context.get("session_id")

    if not session_id:
        return ToolMessage(content="Error: no session_id.", tool_call_id=tool_call_id)

    session = store.get(session_id)
    if not session:
        return ToolMessage(content=f"Error: session '{session_id}' not found.", tool_call_id=tool_call_id)

    if not session.has_pdfs():
        return ToolMessage(
            content=(
                "No PDF/TXT documents uploaded for this session. "
                "Please upload documents first via the /upload/pdf endpoint."
            ),
            tool_call_id=tool_call_id,
        )

    # project_logger.info(f"[RAG] query='{query}' session={session_id}")

    try:
        result = await rag_search(query=query, session_id=session_id)
    except Exception as e:
        result = f"RAG search error: {type(e).__name__}: {e}"
        # project_logger.error(f"[RAG] Error: {e}", exc_info=True)

    return ToolMessage(content=result, tool_call_id=tool_call_id, name="rag_search_tool")


# ─────────────────────────────────────────────
# Middleware #4: Dynamic system prompt
# ─────────────────────────────────────────────

@dynamic_prompt
def data_aware_prompt(request: ModelRequest) -> str:
    """
    Rebuilds the system prompt on every LLM call.
    Injects: current CSV schemas, variable names, PDF document summaries.
    This ensures the LLM always knows what data is available.
    """
    session_id = request.runtime.context.get("session_id")
    if not session_id:
        return _build_system_prompt("No session.", [], "No PDFs.")

    session = store.get(session_id)
    if not session:
        return _build_system_prompt("Session not found.", [], "No PDFs.")

    return _build_system_prompt(
        csv_summary=session.get_data_summary(),
        variable_names=session.get_variable_names(),
        pdf_summary=session.get_pdf_summary(),
    )


def _build_system_prompt(
    csv_summary: str,
    variable_names: list[str],
    pdf_summary: str,
) -> str:
    vars_str = ", ".join(f"`{v}`" for v in variable_names) if variable_names else "None loaded"

    return f"""You are an expert data analyst and document intelligence assistant.

You have TWO tools. You can call them multiple times, in any order.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 TOOLS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. `rag_search_tool`
   Search uploaded PDF/TXT documents for any information.
   Use for: definitions, formulas, thresholds, policies, methodologies,
            product specs, rules, classification criteria, or any document content.
   Returns: relevant text with source citations.

2. `python_repl_ast`
   Execute Python code on uploaded CSV/Excel data.
   Use for: filtering, aggregation, statistics, calculations, plotting.
   Pre-loaded DataFrames: {vars_str}
   Always print() results.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔄 MULTI-STEP REASONING — HOW TO HANDLE HYBRID QUERIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When a query needs BOTH documents and data, call tools in sequence.
You see each tool's result before deciding your next step.

Examples:

  "Use the depreciation formula from the PDF on the assets CSV"
    → Step 1: rag_search_tool("depreciation formula")
    → Step 2: Read result. Extract the formula values.
    → Step 3: python_repl_ast(code using the actual formula from step 1)

  "Flag any CSV rows that violate the risk thresholds in the compliance doc"
    → Step 1: rag_search_tool("risk threshold values")
    → Step 2: Read result. Note the exact threshold numbers.
    → Step 3: python_repl_ast(filter rows using those thresholds)

  "Classify each product in the spreadsheet using the PDF's category rules"
    → Step 1: rag_search_tool("product category classification rules")
    → Step 2: python_repl_ast(apply classification logic to each row)
    → Step 3: rag_search_tool("edge cases or exceptions in classification")
    → Step 4: python_repl_ast(handle edge cases, show final result)

  "Analyze the data and then check if the findings match what the report says"
    → Step 1: python_repl_ast(run the analysis)
    → Step 2: rag_search_tool("expected findings or benchmarks from report")
    → Step 3: Synthesize comparison

YOU decide how many steps are needed. Do not stop early.
Read each tool result carefully before writing the next tool call.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 LOADED CSV/EXCEL DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{csv_summary}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📄 LOADED DOCUMENTS (PDF/TXT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{pdf_summary}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Always print() in Python code to display results.
- Do NOT import os, subprocess, sys, socket, or system modules.
- Limit output to .head(10) unless asked for more.
- WAIT for rag_search_tool results before writing code that depends on them.
- Hardcode specific values from RAG results into your Python code.
- Cite document sources in your final answer.
- If info is missing from documents or data, say so clearly.
"""


# ─────────────────────────────────────────────
# Tool definitions
# (schemas only — execution via middleware)
# ─────────────────────────────────────────────

python_repl_tool = PythonAstREPLTool(
    name="python_repl_ast",
    description=(
        "Execute Python code on uploaded CSV/Excel files. "
        "DataFrames are pre-loaded. Use pandas (pd), numpy (np). "
        "You can hardcode values from rag_search_tool results into this code. "
        "Always use print() to show results."
    ),
)


def _rag_placeholder(query: str) -> str:
    """Placeholder — execution handled by rag_tool_router middleware."""
    return f"[RAG placeholder — should never appear] {query}"

rag_search_tool = StructuredTool.from_function(
    func=_rag_placeholder,
    name="rag_search_tool",
    description=(
        "Search uploaded PDF/TXT documents. "
        "Call this BEFORE python_repl_ast when your analysis depends on document content. "
        "Returns relevant text with citations."
    ),
    args_schema=RagToolInput,
)


# ─────────────────────────────────────────────
# Middleware stack (outermost → innermost)
# ─────────────────────────────────────────────

middleware_stack = [
    PIIMiddleware("email", strategy="redact", apply_to_input=True),
    PIIMiddleware("credit_card", strategy="mask", apply_to_input=True),
    data_aware_prompt,
    SummarizationMiddleware(model=llm, trigger=("messages", 40), keep=("messages", 20)),
    ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
    ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
    code_sandbox,
    ToolRetryMiddleware(max_retries=TOOL_RETRY_MAX),
    ModelRetryMiddleware(max_retries=TOOL_RETRY_MAX),
    rag_tool_router,
    session_tool_router,   # innermost — executes Python in Snekbox
]


# ─────────────────────────────────────────────
# Agent factory
#
# Called AFTER db.init_db() so the checkpointer
# and store are already connected and migrated.
# ─────────────────────────────────────────────

def create_hybrid_agent():
    """
    Build and return the agent using the Postgres checkpointer + store
    that were initialised during FastAPI lifespan startup.

    Short-term memory (checkpointer):
        Stores the full message history per thread_id (= session_id).
        Survives server restarts — conversations resume seamlessly.

    Long-term memory (store):
        Namespace: (user_id, "preferences") — cross-session user context.
        Agents can read/write facts that persist beyond a single session.
        e.g. "user prefers metric units", "favourite store is Store 14"
    """
    if db.checkpointer is None or db.store is None:
        raise RuntimeError(
            "Database not initialised. Call await db.init_db() before create_hybrid_agent()."
        )

    return create_agent(
        model=llm,
        tools=[python_repl_tool, rag_search_tool],
        middleware=middleware_stack,
        context_schema=SessionContext,
        checkpointer=db.checkpointer,   # AsyncPostgresSaver
        store=db.store,                 # AsyncPostgresStore
        name="hybrid_data_rag_agent",
    )


# Module-level reference — populated by main.py after lifespan init
agent = None