# Data Analysis Agent

A production-grade AI agent that analyzes CSV/Excel data using natural language. Ask questions in plain English, and the agent writes and executes pandas code to answer them — showing its full reasoning process (Thought → Action → Observation).

Built with **LangChain v1**, **LangGraph**, **Groq (Llama 3.3 70B)**, and a 10-layer middleware stack for security, PII redaction, and session isolation.

```
You:   "What are the top 5 stores by total sales?"

Agent: [THOUGHT]  I'll group by Store and sum Weekly_Sales, then get the top 5.
       [ACTION]   walmart.groupby('Store')['Weekly_Sales'].sum().nlargest(5)
       [OBSERVATION]  Store 20: $301M, Store 4: $299M, ...
       [ANSWER]   The top 5 stores by total Weekly Sales are: ...
```

---

## Project Structure

```
DataAnalysisAgent/
├── config.py           # Settings, API keys, security lists
├── session_store.py    # Per-user session & DataFrame isolation
├── agent_setup.py      # Agent creation + 10-layer middleware stack
├── schemas.py          # Pydantic models (for API mode)
├── api.py              # FastAPI REST endpoints (optional)
├── main.py             # Uvicorn server entry point (optional)
├── run.py              # Direct library usage — single file
├── runmulti.py         # Multi-file loading example
├── requirements.txt    # All dependencies
├── EXPLANATION.md       # Full code walkthrough (line-by-line)
└── README.md           # This file
```

---

## Setup

### 1. Create virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

Or install the core packages manually:

```bash
pip install langchain langchain-groq langchain-experimental langgraph python-dotenv pandas numpy openpyxl fastapi uvicorn[standard] python-multipart
```

### 3. Set your API key

Create a `.env` file in the project root:

```
GROQ_API_KEY=gsk_your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com).

---

## Usage

### Library Mode (recommended for scripting)

Edit `run.py` — point to your files and write your questions:

```python
import asyncio
import pandas as pd
from session_store import store
from agent_setup import agent

async def main():
    session = store.create(user_id="aryan")

    # Load your data
    df = pd.read_csv("path/to/your/data.csv")
    session.add_dataframe("sales", df)

    # Ask questions
    config = {"configurable": {"thread_id": session.session_id}}
    context = {"session_id": session.session_id}

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "What are the top 5 products?"}]},
        config=config,
        context=context,
    )

    # Get the answer
    for msg in reversed(result["messages"]):
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
            print(msg.content)
            break

asyncio.run(main())
```

Run:

```bash
python run.py
```

### Loading Multiple Files (CSV + Excel mixed)

```python
files = [
    r"data\Walmart_Sales.csv",         # CSV  → variable: Walmart_Sales
    r"data\Inventory.xlsx",             # Excel → variable: Inventory
    r"data\Customer_Feedback.csv",      # CSV  → variable: Customer_Feedback
]

for filepath in files:
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath)
    else:
        df = pd.read_csv(filepath)

    name = filepath.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    session.add_dataframe(name, df)
```

The agent sees all DataFrames and can query across them (joins, comparisons, etc).

### API Server Mode

Start the server:

```bash
python main.py
```

Server runs at `http://localhost:8000`. Swagger docs at `http://localhost:8000/docs`.

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Health check |
| `POST` | `/sessions` | Create a new session |
| `GET` | `/sessions/{id}` | Get session info |
| `DELETE` | `/sessions/{id}` | Delete session |
| `POST` | `/sessions/{id}/upload` | Upload CSV/Excel file |
| `POST` | `/sessions/{id}/ask` | Ask a question |
| `POST` | `/sessions/{id}/ask/stream` | Ask with SSE streaming |

**Example flow with curl:**

```bash
# Create session
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"user_id": "aryan"}'
# → {"session_id": "abc-123", ...}

# Upload file
curl -X POST http://localhost:8000/sessions/abc-123/upload \
  -F "file=@data/Walmart_Sales.csv"
# → {"variable_name": "Walmart_Sales", "shape": [6435, 8], ...}

# Ask question
curl -X POST http://localhost:8000/sessions/abc-123/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the top 5 stores by sales?"}'
# → {"answer": "The top 5 stores by total Weekly Sales are: ..."}
```

---

## Streaming Output (ReAct Trace)

`run.py` uses `agent.astream()` to show the full reasoning loop:

```
======================================================================
QUESTION: What are the top 5 stores by total Weekly_Sales?
======================================================================

[Step 1] THOUGHT:
  Let me find the top 5 stores by total weekly sales.

[Step 1] ACTION: call tool 'python_repl_ast'
  Code:
  ┌──────────────────────────────────────────────────
  │ top_stores = walmart.groupby('Store')['Weekly_Sales'].sum().nlargest(5)
  │ print(top_stores)
  └──────────────────────────────────────────────────

[Step 2] OBSERVATION (from 'python_repl_ast'):
  > Store
  > 20    301397792.46
  > 4     299543953.82
  > 14    288999911.54
  > 13    286293800.72
  > 2     275382642.62

[Step 3] THOUGHT:
  Here are the top 5 stores by total Weekly Sales:
  1. Store 20: $301,397,792.46
  2. Store 4: $299,543,953.82
  ...
```

---

## Architecture

```
User Question
    │
    ▼
┌──────────────────────────────────────────────────┐
│              Middleware Stack (10 layers)          │
│                                                   │
│  [1-2] PII Redaction (emails, credit cards)       │
│  [3]   Dynamic Prompt (injects data schema)       │
│  [4]   Summarization (compress long conversations)│
│  [5-6] Call Limits (25 LLM / 15 tool max)         │
│  [7]   Code Sandbox (AST validation)              │
│  [8-9] Retry Logic (tool + model)                 │
│  [10]  Session Router (per-user REPL isolation)   │
│                                                   │
│  ┌──────────┐  loop   ┌───────────┐              │
│  │  Agent   │◄───────▶│   Tools   │              │
│  │  (LLM)  │         │  (pandas) │              │
│  └──────────┘         └───────────┘              │
└──────────────────────────────────────────────────┘
    │
    ▼
  Answer
```

**Key design decisions:**

- **Placeholder tool pattern**: The `PythonAstREPLTool` registered with `create_agent` is never executed. It only provides the tool schema to the LLM. The `session_tool_router` middleware intercepts calls and routes them to the correct user's isolated REPL.

- **AST sandbox**: Code is parsed with `ast.parse()` and walked BEFORE execution. Banned imports (`os`, `subprocess`, etc.), banned functions (`exec`, `eval`, `open`), and dunder access (`__class__`, `__globals__`) are blocked at the syntax tree level.

- **Safe builtins**: The REPL's `globals["__builtins__"]` is replaced with a whitelist of 55 safe builtins. Dangerous functions like `eval()` simply don't exist in the namespace.

- **Session isolation**: Each user gets their own `PythonAstREPLTool` with its own `locals` dict. User A's DataFrames are invisible to User B.

---

## Security

7 layers of defense:

| Layer | What It Does |
|-------|-------------|
| Safe builtins whitelist | `eval`, `exec`, `open` don't exist in the REPL namespace |
| AST code sandbox | Parses code before execution, blocks banned imports/functions/dunders |
| Banned modules list | 35 modules blocked: `os`, `subprocess`, `socket`, `pickle`, etc. |
| Banned functions list | `exec`, `eval`, `open`, `__import__`, `getattr`, etc. |
| Session isolation | Each user has a separate REPL with separate variables |
| Call limits | Max 25 LLM calls, 15 tool calls per request |
| File upload limits | Max 50 MB, 5 files per session, only CSV/Excel |

**Testing the sandbox:**

```
Q: "Run import os and list files"
→ Security error: import of 'os' is not allowed.

Q: "Use eval to execute code"
→ Security error: call to 'eval' is not allowed.
```

---

## Configuration

All settings are in `config.py`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | LLM model |
| `MAX_FILE_SIZE_MB` | `50` | Max upload file size |
| `MAX_FILES_PER_SESSION` | `5` | Max files per session |
| `MODEL_CALL_LIMIT` | `25` | Max LLM calls per request |
| `TOOL_CALL_LIMIT` | `15` | Max code executions per request |
| `TOOL_RETRY_MAX` | `2` | Retries on failure |

---

## Conversation Memory

The agent remembers previous questions within a session:

```python
config = {"configurable": {"thread_id": session.session_id}}
```

The `thread_id` ties questions to the same conversation. Question 2 sees the context of question 1. The `SummarizationMiddleware` automatically compresses the conversation when it exceeds 40 messages.

---

## File Descriptions

| File | Lines | What It Does |
|------|-------|-------------|
| `config.py` | 100 | Loads `.env`, defines all constants, security lists, safe builtins whitelist |
| `session_store.py` | 102 | `SessionData` (holds DataFrames + isolated REPL per user), `SessionStore` (thread-safe registry) |
| `agent_setup.py` | 231 | Creates LLM, 3 custom middleware (`code_sandbox`, `session_tool_router`, `data_aware_prompt`), assembles 10-layer stack, calls `create_agent()` |
| `schemas.py` | 37 | Pydantic models for API requests/responses |
| `api.py` | 199 | FastAPI app with 7 endpoints: health, session CRUD, upload, ask, stream |
| `main.py` | 4 | `uvicorn.run("api:app")` |
| `run.py` | 91 | Library mode: load data, ask questions, see THOUGHT/ACTION/OBSERVATION trace |
| `runmulti.py` | 91 | Same as run.py but with multi-file loading example |

For a complete line-by-line explanation of every file, see [EXPLANATION.md](./EXPLANATION.md).
