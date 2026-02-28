# Data Analysis Agent — Complete Code Explanation

## Table of Contents

- [What Is This Project?](#what-is-this-project)
- [How It Works (The Big Picture)](#how-it-works-the-big-picture)
- [The ReAct Pattern](#the-react-pattern)
- [Architecture Diagram](#architecture-diagram)
- [File-by-File Breakdown](#file-by-file-breakdown)
  - [1. config.py — Settings & Security Lists](#1-configpy--settings--security-lists)
  - [2. session_store.py — Per-User Isolation](#2-session_storepy--per-user-isolation)
  - [3. schemas.py — API Data Shapes](#3-schemaspy--api-data-shapes)
  - [4. agent_setup.py — The Brain (Agent + Middleware)](#4-agent_setuppy--the-brain-agent--middleware)
  - [5. api.py — REST API Layer](#5-apipy--rest-api-layer)
  - [6. main.py — Server Entry Point](#6-mainpy--server-entry-point)
  - [7. run.py — Direct Library Usage](#7-runpy--direct-library-usage)
- [The Middleware Stack (Deep Dive)](#the-middleware-stack-deep-dive)
- [How create_agent Works Internally](#how-create_agent-works-internally)
- [Key Concepts Explained](#key-concepts-explained)
- [Security Model](#security-model)
- [Data Flow: Full Request Lifecycle](#data-flow-full-request-lifecycle)

---

## What Is This Project?

A production-grade data analysis agent that:
1. Takes CSV/Excel files as input
2. Lets you ask natural language questions about the data
3. Writes and executes Python (pandas/numpy) code to answer
4. Shows you its reasoning: what it thought, what code it wrote, what the output was
5. Supports multiple users with isolated sessions (User A's data never leaks to User B)

**Tech stack:**
- **LLM**: Groq (llama-3.3-70b-versatile) — fast inference, free tier
- **Agent framework**: LangChain v1 `create_agent` + LangGraph
- **Code execution**: `PythonAstREPLTool` (runs pandas code in a sandboxed Python namespace)
- **API**: FastAPI (optional — you can also use it as a library)
- **Memory**: `InMemorySaver` (conversation persists across questions in the same session)

---

## How It Works (The Big Picture)

```
You: "What are the top 5 stores by sales?"
          │
          ▼
   ┌─────────────┐
   │   Agent      │  LLM reads your question + knows what data you uploaded
   │  (Groq LLM)  │  because the dynamic prompt injected column names, shapes, previews
   └──────┬──────┘
          │ LLM decides: "I need to run pandas code"
          ▼
   ┌─────────────────┐
   │  code_sandbox    │  Parses the code with Python's `ast` module
   │  (middleware)    │  Checks: no `import os`, no `eval()`, no `__dunder__` access
   └──────┬──────────┘
          │ Code is clean ✓
          ▼
   ┌─────────────────────┐
   │  session_tool_router │  Looks up YOUR session by ID
   │  (middleware)        │  Runs code in YOUR isolated REPL (not a shared one)
   └──────┬──────────────┘
          │ Returns the pandas output
          ▼
   ┌─────────────┐
   │   Agent      │  LLM reads the output, formats a human-friendly answer
   │  (Groq LLM)  │
   └──────┬──────┘
          │
          ▼
You see: "The top 5 stores by total Weekly Sales are: Store 20 ($301M), ..."
```

---

## The ReAct Pattern

This agent follows the **ReAct** (Reason + Act) pattern. Every question goes through a loop:

```
         ┌──────────────────────────────────────────┐
         │                                          │
         ▼                                          │
   ┌──────────┐    has tool calls?    ┌──────────┐  │
   │  THOUGHT  │───── yes ──────────▶│  ACTION   │  │
   │  (LLM)   │                      │  (tool)   │──┘
   └──────────┘                      └──────────┘
         │                                 │
         no                           OBSERVATION
         │                           (tool output)
         ▼
   ┌──────────┐
   │  ANSWER  │  ← final response to user
   └──────────┘
```

**THOUGHT**: The LLM reasons about what to do. It sees:
- Your question
- The system prompt (with data schema injected)
- Previous conversation history
- Previous tool outputs (if this isn't the first loop)

**ACTION**: The LLM generates a tool call — specifically, it outputs Python code to run via `python_repl_ast`.

**OBSERVATION**: The code runs, and the output (e.g., a pandas DataFrame printed to stdout) comes back as a `ToolMessage`.

The loop repeats if the LLM needs more information. Once it has enough, it writes a final text answer (no tool call) and the loop ends.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Your Code (run.py)                       │
│                                                                 │
│  session = store.create("aryan")                                │
│  session.add_dataframe("walmart", df)                           │
│  result = await agent.ainvoke(messages, config, context)        │
└─────────────┬───────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    agent (CompiledStateGraph)                    │
│                                                                 │
│  Built by create_agent() with:                                  │
│  - model = ChatGroq (llama-3.3-70b)                             │
│  - tools = [placeholder PythonAstREPLTool]                      │
│  - middleware = [10 middleware layers]                           │
│  - checkpointer = InMemorySaver (conversation memory)           │
│  - context_schema = SessionContext (carries session_id)         │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Middleware Onion (10 layers)                │    │
│  │                                                         │    │
│  │  [1] PIIMiddleware("email")          ← outermost        │    │
│  │  [2] PIIMiddleware("credit_card")                       │    │
│  │  [3] data_aware_prompt               ← dynamic sys msg  │    │
│  │  [4] SummarizationMiddleware                            │    │
│  │  [5] ModelCallLimitMiddleware                           │    │
│  │  [6] ToolCallLimitMiddleware                            │    │
│  │  [7] code_sandbox                    ← AST validation   │    │
│  │  [8] ToolRetryMiddleware                                │    │
│  │  [9] ModelRetryMiddleware                               │    │
│  │  [10] session_tool_router            ← innermost        │    │
│  │                                                         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌──────────┐         ┌───────────┐                             │
│  │  agent   │ ◀─────▶ │   tools   │                             │
│  │  node    │  loop    │   node    │                             │
│  │  (LLM)  │         │  (REPL)   │                             │
│  └──────────┘         └───────────┘                             │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   session_store (SessionStore)                   │
│                                                                 │
│  session_abc123:                                                │
│    ├── user_id: "aryan"                                         │
│    ├── dataframes: {"walmart": <DataFrame 6435x8>}              │
│    └── repl_tool: PythonAstREPLTool                             │
│          ├── locals: {pd, np, walmart}   ← isolated namespace   │
│          └── globals: {__builtins__: SAFE_BUILTINS}             │
│                                                                 │
│  session_def456:                                                │
│    ├── user_id: "bob"                                           │
│    ├── dataframes: {"sales": <DataFrame>}                       │
│    └── repl_tool: PythonAstREPLTool      ← different namespace  │
│          ├── locals: {pd, np, sales}                            │
│          └── globals: {__builtins__: SAFE_BUILTINS}             │
└─────────────────────────────────────────────────────────────────┘
```

---

## File-by-File Breakdown

---

### 1. `config.py` — Settings & Security Lists

This file is the single source of truth for all configuration. No magic numbers or hardcoded values anywhere else.

```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
```

**`load_dotenv()`** reads the `.env` file in the project root. This file contains:
```
GROQ_API_KEY=gsk_xxxxx
```
It loads this into `os.environ` so we can read it with `os.getenv()`. This keeps secrets out of source code.

```python
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
```

- **`GROQ_API_KEY`**: Your API key for Groq's inference service. The `""` default means it won't crash on import if the key is missing (it'll fail later when the LLM tries to call the API — better error message).
- **`GROQ_MODEL`**: Which model to use. `llama-3.3-70b-versatile` is Meta's Llama 3.3 70B parameter model, hosted on Groq's fast inference hardware.

```python
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
```

- **`BASE_DIR`**: The directory where `config.py` lives. `Path(__file__).resolve().parent` means "get the absolute path of this file, then go up one level to its parent directory."
- **`UPLOAD_DIR`**: Where uploaded files are saved. `mkdir(parents=True, exist_ok=True)` creates the directory (and any missing parent directories) if it doesn't exist, and does nothing if it already does.

```python
MAX_FILE_SIZE_MB = 50
MAX_FILES_PER_SESSION = 5
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
```

Guardrails for file uploads:
- No file larger than 50 MB
- Max 5 files per session (prevents memory exhaustion)
- Only CSV and Excel files (not arbitrary files)

```python
MODEL_CALL_LIMIT = 25
TOOL_CALL_LIMIT = 15
TOOL_RETRY_MAX = 2
```

Agent loop limits:
- **`MODEL_CALL_LIMIT = 25`**: The LLM can be called at most 25 times per `ainvoke()`. This prevents infinite loops where the agent keeps reasoning without converging.
- **`TOOL_CALL_LIMIT = 15`**: The tool can be called at most 15 times per run. Prevents runaway code execution.
- **`TOOL_RETRY_MAX = 2`**: If a tool call or model call fails (e.g., network error), retry up to 2 times before giving up.

#### BANNED_MODULES

```python
BANNED_MODULES = frozenset({
    "os", "subprocess", "shutil", "sys", "socket", "http", "requests",
    "urllib", "ftplib", "smtplib", "telnetlib", "xmlrpc", "ctypes",
    "multiprocessing", "threading", "signal", "importlib", "code",
    "codeop", "compile", "compileall", "py_compile", "zipimport",
    "pkgutil", "pathlib", "tempfile", "glob", "fnmatch", "io",
    "pickle", "shelve", "marshal", "dbm", "sqlite3", "webbrowser",
})
```

**Why `frozenset`?** It's an immutable set — faster lookups than a list (O(1) vs O(n)), and can't be accidentally modified at runtime.

**Why these modules?** Every module here gives access to something dangerous:
- `os`, `subprocess`, `shutil` — run shell commands, delete files
- `sys` — access interpreter internals, exit the process
- `socket`, `http`, `requests`, `urllib` — make network requests (data exfiltration)
- `pickle`, `marshal` — deserialize arbitrary objects (remote code execution)
- `importlib` — dynamically import any module (bypass all bans)
- `io`, `pathlib`, `tempfile`, `glob` — read/write files on disk
- `sqlite3`, `dbm` — access databases
- `ctypes` — call C functions directly (full system access)

The agent only needs `pandas` and `numpy`. Everything else is blocked.

#### BANNED_FUNCTIONS

```python
BANNED_FUNCTIONS = frozenset({
    "exec", "eval", "open", "__import__", "compile",
    "execfile", "input", "breakpoint", "exit", "quit",
    "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "dir", "help", "type.__subclasses__",
})
```

Even without importing modules, Python has built-in functions that are dangerous:
- **`exec()`** / **`eval()`** — execute arbitrary Python code strings (bypasses AST checking)
- **`open()`** — read/write any file on disk
- **`__import__()`** — import any module (bypasses `import` statement blocking)
- **`compile()`** — compile code objects
- **`getattr()` / `setattr()` / `delattr()`** — access any attribute dynamically, including private/protected ones
- **`globals()` / `locals()`** — access the entire namespace (could leak other sessions' data)
- **`breakpoint()`** — drop into debugger (hangs the server)
- **`exit()` / `quit()`** — kill the process

#### SAFE_BUILTINS

```python
SAFE_BUILTINS = {
    "range": range, "len": len, "int": int, "float": float,
    "str": str, "bool": bool, "list": list, "dict": dict,
    # ... 55 entries total
}
```

**Why do we need this?** The `PythonAstREPLTool` accepts a `globals` parameter. If we set `globals={"__builtins__": {}}` (empty), it blocks ALL builtins — including ones that pandas needs internally (like `isinstance`, `type`, `ValueError`, etc.). Pandas would crash.

So instead, we provide a whitelist: only these 55 safe builtins are available. The dangerous ones (`exec`, `eval`, `open`, etc.) are not in this dict, so they simply don't exist in the execution namespace.

This is **defense in depth** — even if the AST checker in `code_sandbox` somehow misses a call to `eval()`, the function literally doesn't exist in the REPL's namespace.

---

### 2. `session_store.py` — Per-User Isolation

This file solves the multi-user problem: how do you let multiple users upload different datasets and run code without their data leaking into each other?

#### SessionData

```python
@dataclass
class SessionData:
    session_id: str
    user_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    dataframes: dict[str, pd.DataFrame] = field(default_factory=dict)
    repl_tool: PythonAstREPLTool = field(init=False)
```

**`@dataclass`** auto-generates `__init__`, `__repr__`, etc. Each session holds:
- **`session_id`**: UUID, uniquely identifies this session
- **`user_id`**: Who created it
- **`created_at`**: When
- **`dataframes`**: All uploaded DataFrames, keyed by sanitized name
- **`repl_tool`**: This session's private Python REPL

**`field(init=False)`** on `repl_tool` means it's NOT a constructor parameter — it's created in `__post_init__` instead.

```python
def __post_init__(self) -> None:
    self.repl_tool = PythonAstREPLTool(
        locals={"pd": pd, "np": np},
        globals={"__builtins__": SAFE_BUILTINS},
    )
```

**`__post_init__`** runs after `__init__`. It creates a fresh `PythonAstREPLTool` with:
- **`locals`**: Variables available in the REPL. Starts with just `pd` (pandas) and `np` (numpy). DataFrames get added later via `add_dataframe()`.
- **`globals`**: The global namespace. We override `__builtins__` with our `SAFE_BUILTINS` whitelist.

**Key insight**: Each session gets its OWN `PythonAstREPLTool` with its OWN `locals` dict. Session A's `walmart` variable doesn't exist in Session B's namespace.

#### add_dataframe

```python
def add_dataframe(self, name: str, df: pd.DataFrame) -> str:
    if len(self.dataframes) >= MAX_FILES_PER_SESSION:
        raise ValueError(...)

    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    safe_name = re.sub(r"^[^a-zA-Z]", "df_", safe_name)
    if not safe_name:
        safe_name = "df"

    self.dataframes[safe_name] = df
    self.repl_tool.locals[safe_name] = df
    self.repl_tool.locals["pd"] = pd
    self.repl_tool.locals["np"] = np
    return safe_name
```

**Name sanitization**: File names can contain anything — spaces, hyphens, dots, unicode. But Python variable names must be `[a-zA-Z_][a-zA-Z0-9_]*`. So:
1. `re.sub(r"[^a-zA-Z0-9_]", "_", name)` — replace anything that's not alphanumeric or underscore with `_`
2. `re.sub(r"^[^a-zA-Z]", "df_", safe_name)` — if it starts with a number, prefix with `df_`

Example: `"2024-Sales Data.csv"` becomes stem `"2024-Sales Data"` becomes `"2024_Sales_Data"` becomes `"df_024_Sales_Data"`.

**`self.repl_tool.locals[safe_name] = df`** — This is the magic. After this line, when the LLM writes `walmart.groupby('Store')...`, the variable `walmart` exists in the REPL and points to the actual DataFrame.

#### get_data_summary

```python
def get_data_summary(self) -> str:
    parts: list[str] = []
    for name, df in self.dataframes.items():
        cols_info = ", ".join(
            f"`{col}` ({dtype})" for col, dtype in zip(df.columns, df.dtypes)
        )
        preview = df.head(3).to_string(index=False)
        parts.append(
            f"### `{name}`\n"
            f"- **Shape**: {df.shape[0]} rows x {df.shape[1]} columns\n"
            f"- **Columns**: {cols_info}\n"
            f"- **Preview** (first 3 rows):\n```\n{preview}\n```"
        )
    return "\n\n".join(parts)
```

This generates a markdown string like:
```
### `walmart`
- **Shape**: 6435 rows x 8 columns
- **Columns**: `Store` (int64), `Date` (object), `Weekly_Sales` (float64), ...
- **Preview** (first 3 rows):
   Store       Date  Weekly_Sales  ...
       1  05-02-2010    1643690.90  ...
```

This summary gets injected into the LLM's system prompt by the `data_aware_prompt` middleware. The LLM never sees the raw data — just the schema and a 3-row preview. This is enough for it to write correct pandas code.

#### SessionStore

```python
class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}
        self._lock = threading.Lock()
```

**`threading.Lock()`** — FastAPI handles multiple requests concurrently. Without the lock, two requests could modify `_sessions` at the same time and corrupt it. The lock ensures only one thread reads/writes at a time.

```python
def create(self, user_id: str) -> SessionData:
    session_id = str(uuid.uuid4())
    session = SessionData(session_id=session_id, user_id=user_id)
    with self._lock:
        self._sessions[session_id] = session
    return session
```

**`uuid.uuid4()`** generates a random UUID like `"ac6b6ef6-c206-489d-8171-07dabc4e3f64"`. Guaranteed unique.

```python
store = SessionStore()  # Module-level singleton
```

**Singleton pattern**: There's exactly ONE `SessionStore` instance. Every file that does `from session_store import store` gets the SAME object. This is how `api.py` and `agent_setup.py` both see the same sessions.

---

### 3. `schemas.py` — API Data Shapes

These are Pydantic models that define the shape of request/response JSON for the FastAPI endpoints. Only used when running as an API server — `run.py` (library mode) doesn't use these.

```python
class CreateSessionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
```

**`Field(..., min_length=1)`** — The `...` means "required" (no default). `min_length=1` means empty strings are rejected. FastAPI automatically returns `422 Unprocessable Entity` with a clear error if validation fails.

```python
class UploadResponse(BaseModel):
    filename: str
    variable_name: str
    shape: list[int]         # e.g., [6435, 8]
    columns: list[str]       # e.g., ["Store", "Date", "Weekly_Sales", ...]
    preview: str             # first 5 rows as a string
    message: str = "File uploaded and loaded successfully."
```

**Why return `variable_name`?** So the user knows what variable name to reference in their questions. If they uploaded `"2024-Sales.csv"`, the sanitized name might be `"df_024_Sales"` — they need to know this.

---

### 4. `agent_setup.py` — The Brain (Agent + Middleware)

This is the most complex file. It wires together the LLM, the tool, and 10 middleware layers into a single agent.

#### Context Schema

```python
class SessionContext(TypedDict):
    session_id: str
```

**`TypedDict`** defines the shape of the `context` parameter you pass to `agent.ainvoke()`. When you call:
```python
await agent.ainvoke(messages, config, context={"session_id": "abc123"})
```
This context is available to every middleware via `request.runtime.context["session_id"]`.

**Why not just pass session_id in the messages?** Because:
1. The LLM could hallucinate or modify it
2. Middleware needs it before the LLM even sees the messages
3. It's metadata about the request, not part of the conversation

#### LLM

```python
llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=GROQ_API_KEY,
    temperature=0,
    max_tokens=4096,
)
```

- **`temperature=0`**: Deterministic output. The model always picks the highest-probability token. Important for data analysis — you want consistent, reproducible answers.
- **`max_tokens=4096`**: Maximum length of the LLM's response. 4096 tokens is roughly 3000 words — enough for a detailed analysis.

#### Middleware #1: code_sandbox

```python
@wrap_tool_call
async def code_sandbox(request: ToolCallRequest, handler):
```

**`@wrap_tool_call`** is a decorator that turns a function into an `AgentMiddleware`. It intercepts tool calls. The function receives:
- **`request`**: Contains `tool_call` (name, args, id), `tool`, `state`, `runtime`
- **`handler`**: A function that calls the next middleware in the chain (or the actual tool if this is the last one)

**`async`**: Required because the agent is invoked with `ainvoke()` / `astream()` (async). Without `async`, you get `NotImplementedError: Asynchronous implementation of awrap_tool_call is not available`.

```python
if request.tool_call["name"] != "python_repl_ast":
    return await handler(request)
```

**Early return for non-REPL tools**: If someone adds more tools later, this middleware only cares about `python_repl_ast`. All other tools pass through unchanged.

```python
code = request.tool_call["args"].get("query", "")
try:
    tree = ast.parse(code)
except SyntaxError as e:
    return ToolMessage(
        content=f"SyntaxError: {e}",
        tool_call_id=request.tool_call["id"],
    )
```

**`ast.parse(code)`** parses the Python code string into an Abstract Syntax Tree (AST). If the code has a syntax error, it catches it here and returns the error as a `ToolMessage` (the LLM will see this and try to fix the code).

```python
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            module_root = alias.name.split(".")[0]
            if module_root in BANNED_MODULES:
                return ToolMessage(
                    content=f"Security error: import of '{alias.name}' is not allowed.",
                    ...
                )
```

**`ast.walk(tree)`** visits every node in the AST. For each node:

1. **`ast.Import`** — catches `import os`, `import subprocess.run`
   - `.split(".")[0]` gets the root module: `"subprocess.run"` → `"subprocess"`
   - Checks against `BANNED_MODULES`

2. **`ast.ImportFrom`** — catches `from os import system`, `from pathlib import Path`

3. **`ast.Call`** — catches function calls like `eval("code")`, `exec("code")`
   - `ast.Name` handles `eval(...)` (bare function name)
   - `ast.Attribute` handles `obj.eval(...)` (method call)

4. **`ast.Attribute`** — catches dunder access like `obj.__class__`, `obj.__globals__`
   - This blocks Python jail-break tricks like `().__class__.__bases__[0].__subclasses__()`

If ANY check fails, it returns a `ToolMessage` with the error. The code is NEVER executed. The LLM sees the error and typically says "I can't do that" or tries a safe alternative.

If all checks pass: `return await handler(request)` — passes the request to the next middleware.

#### Middleware #2: session_tool_router

```python
@wrap_tool_call
async def session_tool_router(request: ToolCallRequest, handler):
```

This is the **innermost** tool middleware. It **replaces** default tool execution entirely.

```python
session_id = request.runtime.context.get("session_id")
session = store.get(session_id)
```

Gets the session from the context → looks it up in the store.

```python
code = request.tool_call["args"].get("query", "")
try:
    result = session.repl_tool._run(code)
except Exception as e:
    result = f"Execution error: {type(e).__name__}: {e}"

return ToolMessage(
    content=str(result) if result else "Code executed successfully (no output).",
    tool_call_id=request.tool_call["id"],
)
```

**`session.repl_tool._run(code)`** — Runs the code in that session's isolated REPL. The `_run` method:
1. Compiles the code with `compile()`
2. Executes it with `exec()` using the session's `locals` and `globals`
3. Captures stdout (anything printed) and returns it as a string

**Notice**: This middleware NEVER calls `handler(request)`. It fully replaces the tool execution. The placeholder tool that was registered with `create_agent` is never actually invoked.

#### Middleware #3: data_aware_prompt

```python
@dynamic_prompt
def data_aware_prompt(request: ModelRequest) -> str:
```

**`@dynamic_prompt`** is a decorator for middleware that intercepts MODEL calls (not tool calls). It runs BEFORE every LLM invocation and sets the system prompt.

```python
session_id = request.runtime.context.get("session_id")
session = store.get(session_id)
summary = session.get_data_summary()
variables = session.get_variable_names()
return _default_system_prompt(summary, variables)
```

Every time the LLM is about to be called, this middleware:
1. Looks up the session
2. Gets the current data summary (shapes, columns, 3-row preview)
3. Gets the variable names available in the REPL
4. Returns a system prompt string with all of this injected

**Why dynamic?** Because data can change. If the user uploads a second file mid-conversation, the next LLM call will see the updated schema.

The system prompt tells the LLM:
- What DataFrames are available and their schemas
- What variable names to use
- Rules: use `print()`, don't import system modules, limit output

#### Placeholder Tool

```python
placeholder_repl = PythonAstREPLTool(
    name="python_repl_ast",
    description="A Python shell for data analysis. ..."
)
```

This tool is **never actually executed**. It exists for ONE reason: so `create_agent` registers its **name** and **description** in the LLM's tool schema. When the LLM sees the tool list, it sees:

```json
{
  "name": "python_repl_ast",
  "description": "A Python shell for data analysis...",
  "parameters": {"query": {"type": "string"}}
}
```

And knows it can generate tool calls with `name="python_repl_ast"` and `args={"query": "print(walmart.head())"}`.

The actual execution is handled by `session_tool_router` middleware, which intercepts these calls and routes them to the correct session's REPL.

#### Middleware Stack

```python
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
    session_tool_router,
]
```

**Order matters.** First in the list = outermost = runs first on request, last on response. See the [Middleware Deep Dive](#the-middleware-stack-deep-dive) section.

#### Agent Creation

```python
agent = create_agent(
    model=llm,
    tools=[placeholder_repl],
    middleware=middleware_stack,
    context_schema=SessionContext,
    checkpointer=InMemorySaver(),
    name="data_analysis_agent",
)
```

- **`model`**: The LLM to use for reasoning
- **`tools`**: Tools the LLM can call (just the placeholder REPL)
- **`middleware`**: The 10-layer stack
- **`context_schema`**: Tells the agent what shape `context=` should be
- **`checkpointer=InMemorySaver()`**: Stores conversation history in memory. The `thread_id` in config determines which conversation to load.
- **`name`**: Human-readable name for debugging

`create_agent` returns a `CompiledStateGraph` — a LangGraph graph with two nodes (`agent` and `tools`) connected in a loop.

---

### 5. `api.py` — REST API Layer

This file wraps the agent in HTTP endpoints. Only needed if you want to interact via HTTP (e.g., from a frontend). If you use `run.py`, you skip this entirely.

#### FastAPI Setup

```python
app = FastAPI(title="Data Analysis Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**CORS middleware** allows any frontend (any origin) to call this API. In production, you'd restrict `allow_origins` to your specific domain.

#### Upload Endpoint

```python
@app.post("/sessions/{session_id}/upload", response_model=UploadResponse)
async def upload_file(session_id: str, file: UploadFile = File(...)):
```

Step by step:
1. **Validate session exists** — 404 if not
2. **Validate extension** — only `.csv`, `.xlsx`, `.xls`
3. **Validate file size** — max 50 MB
4. **Save to disk** — `data/uploads/{session_id}_{filename}`
5. **Parse with pandas** — `pd.read_csv()` or `pd.read_excel()`
6. **Register in session** — `session.add_dataframe()` sanitizes name and injects into REPL
7. **Return metadata** — filename, variable name, shape, columns, 5-row preview

If parsing fails, the saved file is deleted (`save_path.unlink()`).

#### Ask Endpoint (Non-Streaming)

```python
result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": req.question}]},
    config=config,
    context=context,
)
```

- **`messages`**: The user's question, formatted as a chat message
- **`config`**: Contains `thread_id` — tells the checkpointer which conversation thread this belongs to
- **`context`**: Contains `session_id` — tells middleware which session to use

After the agent finishes, it extracts the last AI message from the result.

#### Stream Endpoint (SSE)

```python
async for chunk in agent.astream(..., stream_mode="updates"):
    for node_name, update in chunk.items():
        if "messages" in update:
            for msg in update["messages"]:
                yield f"data: {json.dumps(event_data)}\n\n"
```

**`astream()`** with `stream_mode="updates"` yields chunks as the agent works. Each chunk tells you which node produced it (`agent` or `tools`) and what messages were generated. The endpoint converts these to **Server-Sent Events (SSE)** — a standard for streaming data over HTTP.

---

### 6. `main.py` — Server Entry Point

```python
import uvicorn

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
```

- **`"api:app"`**: Import the `app` object from `api.py`
- **`host="0.0.0.0"`**: Listen on all network interfaces
- **`port=8000`**: HTTP port
- **`reload=True`**: Auto-restart when files change (dev mode)

---

### 7. `run.py` — Direct Library Usage

This is the "no server" mode. You import the components directly and use them as a library.

```python
session = store.create(user_id="aryan")
```

Creates a new session with an isolated REPL.

```python
df = pd.read_csv(r"D:\Aryan Work\Projects\TestDA\data\Walmart_Sales.csv")
var_name = session.add_dataframe("walmart", df)
```

Loads the CSV and injects it into the session's REPL as the variable `walmart`.

```python
config = {"configurable": {"thread_id": session.session_id}}
context = {"session_id": session.session_id}
```

- **`thread_id`**: Tells the checkpointer to persist this conversation. Same `thread_id` across questions = LLM remembers previous Q&A.
- **`session_id`**: Tells middleware which session's data to use.

```python
async for chunk in agent.astream(..., stream_mode="updates"):
    for node_name, update in chunk.items():
```

**`astream`** yields updates as the agent works. Each chunk is a dict mapping `node_name` → `update`:
- **`node_name = "agent"`**: The LLM node produced output (THOUGHT or FINAL ANSWER)
- **`node_name = "tools"`**: The tool node produced output (OBSERVATION)

The code then categorizes each message:
- **`msg.type == "ai"`** with `tool_calls` → **ACTION** (LLM wants to run code)
- **`msg.type == "ai"`** with `content` only → **THOUGHT** or **FINAL ANSWER**
- **`msg.type == "tool"`** → **OBSERVATION** (code execution result)

---

## The Middleware Stack (Deep Dive)

Middleware wraps the agent like layers of an onion. Requests pass **outside-in** (1 → 10), responses pass **inside-out** (10 → 1).

```
                     REQUEST FLOW
                     ════════════
User Question
     │
     ▼
┌─ [1] PIIMiddleware("email") ──────────────────────────────────┐
│  Scans user input for email addresses.                        │
│  "My email is john@example.com" → "My email is [REDACTED]"   │
│  Strategy: "redact" — completely removes the PII.             │
│  apply_to_input=True → runs on user messages before LLM sees │
│                                                               │
│  ┌─ [2] PIIMiddleware("credit_card") ──────────────────────┐  │
│  │  Scans for credit card numbers.                         │  │
│  │  "Card: 4111-1111-1111-1111" → "Card: 4111-****-****-1111" │
│  │  Strategy: "mask" — keeps first/last digits, masks middle│  │
│  │                                                         │  │
│  │  ┌─ [3] data_aware_prompt (@dynamic_prompt) ─────────┐  │  │
│  │  │  Runs before every LLM call.                      │  │  │
│  │  │  Looks up session → builds system prompt with:    │  │  │
│  │  │  - DataFrame shapes and column names              │  │  │
│  │  │  - 3-row data preview                             │  │  │
│  │  │  - Variable names available in REPL               │  │  │
│  │  │  - Rules (use print(), don't import os, etc.)     │  │  │
│  │  │                                                   │  │  │
│  │  │  ┌─ [4] SummarizationMiddleware ───────────────┐  │  │  │
│  │  │  │  Monitors conversation length.              │  │  │  │
│  │  │  │  trigger=("messages", 40) → when convo      │  │  │  │
│  │  │  │    exceeds 40 messages, summarize.           │  │  │  │
│  │  │  │  keep=("messages", 20) → keep last 20       │  │  │  │
│  │  │  │    messages + a summary of the rest.         │  │  │  │
│  │  │  │  Uses the same LLM to generate the summary. │  │  │  │
│  │  │  │  Prevents context window overflow.           │  │  │  │
│  │  │  │                                             │  │  │  │
│  │  │  │  ┌─ [5] ModelCallLimitMiddleware ─────────┐  │  │  │  │
│  │  │  │  │  Counts LLM calls. Max 25 per run.    │  │  │  │  │
│  │  │  │  │  If exceeded → agent stops gracefully. │  │  │  │  │
│  │  │  │  │                                       │  │  │  │  │
│  │  │  │  │  ┌─ [6] ToolCallLimitMiddleware ────┐  │  │  │  │  │
│  │  │  │  │  │  Counts tool calls. Max 15/run.  │  │  │  │  │  │
│  │  │  │  │  │                                  │  │  │  │  │  │
│  │  │  │  │  │  ┌─ [7] code_sandbox ─────────┐  │  │  │  │  │  │
│  │  │  │  │  │  │  AST-parses every code     │  │  │  │  │  │  │
│  │  │  │  │  │  │  block before execution.   │  │  │  │  │  │  │
│  │  │  │  │  │  │  Blocks banned imports,    │  │  │  │  │  │  │
│  │  │  │  │  │  │  banned functions, dunder  │  │  │  │  │  │  │
│  │  │  │  │  │  │  attribute access.         │  │  │  │  │  │  │
│  │  │  │  │  │  │                            │  │  │  │  │  │  │
│  │  │  │  │  │  │  ┌─ [8] ToolRetryMW ────┐  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  If tool fails →     │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  retry up to 2x      │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │                      │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  ┌─ [9] ModelRetry ┐  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  If LLM fails → │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  retry 2x       │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │                 │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  ┌─ [10] ────┐  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  │ session   │  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  │ _tool_    │  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  │ router    │  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  │ (runs     │  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  │  code in  │  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  │  session  │  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  │  REPL)    │  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  │  └──────────┘  │  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  │  └───────────────┘  │  │  │  │  │  │  │  │
│  │  │  │  │  │  │  └─────────────────────┘  │  │  │  │  │  │  │
│  │  │  │  │  │  └───────────────────────────┘  │  │  │  │  │  │
│  │  │  │  │  └─────────────────────────────────┘  │  │  │  │  │
│  │  │  │  └───────────────────────────────────────┘  │  │  │  │
│  │  │  └─────────────────────────────────────────────┘  │  │  │
│  │  └───────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
     │
     ▼
  Response
```

---

## How create_agent Works Internally

`create_agent` (from `langchain.agents`) is a wrapper around LangGraph's `create_react_agent`. It builds a **state machine graph**:

```
                    START
                      │
                      ▼
              ┌──────────────┐
              │  agent node  │◄─────────────────┐
              │  (call LLM)  │                  │
              └──────┬───────┘                  │
                     │                          │
              has tool_calls?                   │
              ├── NO → END                      │
              │        (return final answer)    │
              │                                 │
              └── YES                           │
                     │                          │
                     ▼                          │
              ┌──────────────┐                  │
              │  tools node  │──────────────────┘
              │  (run code)  │  (output goes back as
              └──────────────┘   ToolMessage for the
                                 LLM to read)
```

The `state` flowing through this graph is:
```python
{
    "messages": [
        HumanMessage("What are the top 5 stores?"),
        AIMessage(content="", tool_calls=[{name: "python_repl_ast", args: {query: "..."}}]),
        ToolMessage(content="Store 20: $301M\n..."),
        AIMessage(content="Here are the top 5 stores by sales: ..."),
    ]
}
```

Each loop iteration adds messages. The LLM sees the FULL history on each call.

---

## Key Concepts Explained

### What is `PythonAstREPLTool`?

A LangChain tool that executes Python code safely:
1. Parses the code into an AST (Abstract Syntax Tree) — catches syntax errors early
2. Compiles and executes it using Python's `exec()` with a restricted namespace
3. Captures `stdout` (anything `print()`ed) and returns it as a string

The `locals` dict acts as the variable namespace. When you do `locals={"pd": pd, "walmart": df}`, the code can reference `pd` and `walmart` as if they were already defined.

### What is `InMemorySaver`?

A checkpointer that stores conversation state in a Python dict (in RAM). Each `thread_id` maps to a saved state. When the agent is called with the same `thread_id`, it loads the previous messages and continues the conversation.

**Limitation**: Data is lost when the process restarts. For persistence, you'd swap this for `SqliteSaver` or `PostgresSaver`.

### What is `ToolMessage`?

A LangChain message type that represents tool output. It has:
- **`content`**: The tool's output (string)
- **`tool_call_id`**: Links this result back to the specific tool call that generated it

The LLM sees this as "the result of the code I just ran" and uses it to form its answer.

### What is `TypedDict` (SessionContext)?

A way to define the shape of a dict with type hints. Unlike `dataclass` or `BaseModel`, it's just a dict at runtime — no overhead. The agent framework uses it to validate the `context` parameter.

---

## Security Model

The security is **defense in depth** — multiple layers, each catching different attack vectors:

```
Layer 1: SAFE_BUILTINS (globals)
  └─ Dangerous functions don't exist in the namespace
     eval, exec, open, __import__ → NameError

Layer 2: code_sandbox (AST middleware)
  └─ Parses code BEFORE execution
     Catches: import os, eval("..."), obj.__class__
     Returns error ToolMessage (code never runs)

Layer 3: BANNED_MODULES list
  └─ Even if the LLM tries "from os import system"
     The AST walker catches it by module name

Layer 4: BANNED_FUNCTIONS list
  └─ Even if the LLM tries calling dangerous builtins
     The AST walker catches the function name

Layer 5: Session isolation
  └─ Each session has its own REPL with its own locals
     User A cannot access User B's variables

Layer 6: Call limits
  └─ Max 25 LLM calls and 15 tool calls per request
     Prevents infinite loops and resource exhaustion

Layer 7: File upload limits
  └─ Max 50 MB, max 5 files, only CSV/Excel
     Prevents disk/memory exhaustion
```

**What this does NOT protect against:**
- CPU-intensive code (e.g., `while True: pass` — would need a timeout/sandbox)
- Memory exhaustion from pandas operations on very large DataFrames
- The LLM itself leaking data through its responses (prompt injection)

For production, you'd add: execution timeouts, memory limits, and a container sandbox (Docker).

---

## Data Flow: Full Request Lifecycle

Here's what happens when you ask "What are the top 5 stores by sales?":

```
1. YOUR CODE
   await agent.ainvoke(
       {"messages": [{"role": "user", "content": "What are the top 5 stores?"}]},
       config={"configurable": {"thread_id": "session-abc"}},
       context={"session_id": "session-abc"}
   )

2. CHECKPOINTER (InMemorySaver)
   Loads previous messages for thread_id="session-abc"
   Appends the new HumanMessage

3. MIDDLEWARE (outside-in, model call path):
   [1] PIIMiddleware: scans question for emails → none found → pass through
   [2] PIIMiddleware: scans for credit cards → none found → pass through
   [3] data_aware_prompt: builds system prompt:
       "You are a data analyst. Available: walmart (6435x8, columns: Store, Date, Weekly_Sales...)"
   [4] SummarizationMiddleware: message count < 40 → pass through
   [5] ModelCallLimitMiddleware: call count = 1 (< 25) → pass through

4. LLM CALL (Groq)
   Input:  system prompt + conversation history + user question
   Output: AIMessage with tool_calls=[{
       name: "python_repl_ast",
       args: {query: "top_stores = walmart.groupby('Store')['Weekly_Sales'].sum().nlargest(5)\nprint(top_stores)"}
   }]

5. TOOLS NODE — MIDDLEWARE (outside-in, tool call path):
   [6] ToolCallLimitMiddleware: tool count = 1 (< 15) → pass through
   [7] code_sandbox:
       - ast.parse() the code → OK
       - Walk AST: no banned imports, no banned functions, no dunders → PASS
       - return await handler(request)
   [8] ToolRetryMiddleware: wraps execution in try/catch → pass through
   [10] session_tool_router:
       - Gets session_id from context → "session-abc"
       - Looks up session from store
       - Calls session.repl_tool._run(code)
       - Code executes: walmart.groupby('Store')['Weekly_Sales'].sum().nlargest(5)
       - Returns ToolMessage(content="Store\n20    301397792.46\n4     299543953.82\n...")

6. BACK TO AGENT NODE
   LLM sees the ToolMessage output
   Decides it has enough info → generates final AIMessage:
   "Here are the top 5 stores by total Weekly Sales:
    1. Store 20: $301,397,792.46
    2. Store 4: $299,543,953.82
    ..."

7. LOOP CHECK
   No tool_calls in this AIMessage → END

8. CHECKPOINTER
   Saves all messages to thread_id="session-abc"

9. RETURN
   Returns {"messages": [HumanMessage, AIMessage(tool_calls), ToolMessage, AIMessage(answer)]}
```
