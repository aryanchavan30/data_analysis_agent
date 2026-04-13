import ast
from typing import TypedDict

from langchain_core.messages import ToolMessage
from langchain_experimental.tools import PythonAstREPLTool
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
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
from langgraph.checkpoint.memory import InMemorySaver

from config import (
    BANNED_FUNCTIONS,
    BANNED_MODULES,
    GROQ_API_KEY,
    GROQ_MODEL,
    MODEL_CALL_LIMIT,
    TOOL_CALL_LIMIT,
    TOOL_RETRY_MAX,
)
from session_store import store


# ---------- Context schema ----------


class SessionContext(TypedDict):
    session_id: str


# ---------- LLM ----------

# llm = ChatGroq(
#     model=GROQ_MODEL,
#     api_key=GROQ_API_KEY,
#     temperature=0,
#     max_tokens=4096,
# )

llm = ChatOpenAI(
    model="aryanchavan/Phi-4-mini-instruct-FP8-Dynamic",
    base_url="http://localhost:8000/v1",
    api_key="EMPTY",
)

# ---------- Middleware #1: code_sandbox ----------


@wrap_tool_call
async def code_sandbox(request: ToolCallRequest, handler):
    """AST-based sandbox that blocks dangerous imports and function calls."""
    if request.tool_call["name"] != "python_repl_ast":
        return await handler(request)

    code = request.tool_call["args"].get("query", "")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return ToolMessage(
            content=f"SyntaxError: {e}",
            tool_call_id=request.tool_call["id"],
        )

    for node in ast.walk(tree):
        # Block banned imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_root = alias.name.split(".")[0]
                if module_root in BANNED_MODULES:
                    return ToolMessage(
                        content=f"Security error: import of '{alias.name}' is not allowed.",
                        tool_call_id=request.tool_call["id"],
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_root = node.module.split(".")[0]
                if module_root in BANNED_MODULES:
                    return ToolMessage(
                        content=f"Security error: import from '{node.module}' is not allowed.",
                        tool_call_id=request.tool_call["id"],
                    )

        # Block banned function calls
        elif isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name and func_name in BANNED_FUNCTIONS:
                return ToolMessage(
                    content=f"Security error: call to '{func_name}' is not allowed.",
                    tool_call_id=request.tool_call["id"],
                )

        # Block dunder attribute access
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return ToolMessage(
                    content=f"Security error: access to '{node.attr}' is not allowed.",
                    tool_call_id=request.tool_call["id"],
                )

    return await handler(request)


# ---------- Middleware #2: session_tool_router ----------


@wrap_tool_call
async def session_tool_router(request: ToolCallRequest, handler):
    """Routes python_repl_ast execution to the correct per-session REPL tool."""
    if request.tool_call["name"] != "python_repl_ast":
        return await handler(request)

    session_id = request.runtime.context.get("session_id")
    if not session_id:
        return ToolMessage(
            content="Error: no session_id in context.",
            tool_call_id=request.tool_call["id"],
        )

    session = store.get(session_id)
    if not session:
        return ToolMessage(
            content=f"Error: session '{session_id}' not found.",
            tool_call_id=request.tool_call["id"],
        )

    code = request.tool_call["args"].get("query", "")
    try:
        result = session.repl_tool._run(code)
    except Exception as e:
        result = f"Execution error: {type(e).__name__}: {e}"

    return ToolMessage(
        content=str(result) if result else "Code executed successfully (no output).",
        tool_call_id=request.tool_call["id"],
    )


# ---------- Middleware #3: data_aware_prompt ----------


@dynamic_prompt
def data_aware_prompt(request: ModelRequest) -> str:
    """Injects dataset context into the system prompt dynamically."""
    session_id = request.runtime.context.get("session_id")
    if not session_id:
        return _default_system_prompt("No datasets loaded.", [])

    session = store.get(session_id)
    if not session:
        return _default_system_prompt("Session not found.", [])

    summary = session.get_data_summary()
    variables = session.get_variable_names()
    return _default_system_prompt(summary, variables)


def _default_system_prompt(data_summary: str, variable_names: list[str]) -> str:
    vars_str = ", ".join(f"`{v}`" for v in variable_names) if variable_names else "None"
    return f"""You are a expert data analyst assistant. You analyze datasets using Python (pandas, numpy).

## Available DataFrames
{data_summary}

## Available Variables: {vars_str}

## Rules
- Use the variable names listed above directly — they are already loaded in your Python environment.
- Always use `print()` to display results so the user can see the output.
- Do NOT import os, subprocess, sys, or any system modules.
- Limit output to `.head(10)` or summaries unless the user asks for more.
- When asked for statistics, provide clear formatted output.
- For plotting, use matplotlib (`import matplotlib.pyplot as plt`) and always call `plt.show()` or `print()` the figure info.
- If the user asks about data you don't have, tell them to upload it first.
- Be concise and helpful. Show your work with code, then explain the results.
"""


# ---------- Placeholder tool (schema only — execution handled by middleware) ----------

placeholder_repl = PythonAstREPLTool(
    name="python_repl_ast",
    description=(
        "A Python shell for data analysis. Use this to execute python commands. "
        "Input should be a valid python command. Available libraries: pandas (pd), "
        "numpy (np). DataFrames are pre-loaded as variables. Always use print() to "
        "show results."
    ),
)


# ---------- Middleware stack ----------

middleware_stack = [
    # 1-2: PII detection
    PIIMiddleware("email", strategy="redact", apply_to_input=True),
    PIIMiddleware("credit_card", strategy="mask", apply_to_input=True),
    # 3: Dynamic system prompt with data context
    data_aware_prompt,
    # 4: Conversation summarization
    SummarizationMiddleware(
        model=llm,
        trigger=("messages", 40),
        keep=("messages", 20),
    ),
    # 5-6: Call limits
    ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
    ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
    # 7: AST-based code sandbox (runs before session_tool_router)
    code_sandbox,
    # 8-9: Retry logic
    ToolRetryMiddleware(max_retries=TOOL_RETRY_MAX),
    ModelRetryMiddleware(max_retries=TOOL_RETRY_MAX),
    # 10: Session-aware REPL routing (innermost — replaces default execution)
    session_tool_router,
]


# ---------- Agent creation ----------

agent = create_agent(
    model=llm,
    tools=[placeholder_repl],
    middleware=middleware_stack,
    context_schema=SessionContext,
    checkpointer=InMemorySaver(),
    name="data_analysis_agent",
)
