# Architecture — Data Analysis Agent V2

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Client (Browser / CLI)                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP
┌──────────────────────────────▼──────────────────────────────────────┐
│                      FastAPI Server (:8000)                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌───────────────────┐  │
│  │ /health  │ │/sessions │ │  /upload      │ │  /ask  /ask/stream│  │
│  └──────────┘ └──────────┘ └──────┬───────┘ └────────┬──────────┘  │
│                                   │                   │             │
│                          ┌────────▼───────────────────▼──────────┐  │
│                          │         SessionStore                  │  │
│                          │  (in-memory sessions + file paths)    │  │
│                          └────────────────────┬──────────────────┘  │
│                                               │                     │
│                          ┌────────────────────▼──────────────────┐  │
│                          │     LangChain Agent + Middleware      │  │
│                          │  ┌──────────────────────────────────┐ │  │
│                          │  │ PII → Prompt → Summarize → Limits│ │  │
│                          │  │ → AST Sandbox → Retry → Snekbox  │ │  │
│                          │  └──────────────────────────────────┘ │  │
│                          └────────────────────┬──────────────────┘  │
└───────────────────────────────────────────────┼─────────────────────┘
                                                │ HTTP POST
┌───────────────────────────────────────────────▼─────────────────────┐
│                     Snekbox Container (:8060)                       │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────────┐  │
│  │  HTTP API   │───▶│   NsJail     │───▶│  Python Interpreter   │  │
│  │  (gunicorn) │    │  (sandbox)   │    │  (pandas, numpy, etc) │  │
│  └─────────────┘    └──────────────┘    └────────────────────────┘  │
│                                                                     │
│  Mounted: ./data/uploads → /data (read-only)                       │
│  Limits: 2GB RAM, 2 CPUs, no network, no filesystem write          │
└─────────────────────────────────────────────────────────────────────┘
```

## Request Lifecycle

```
User Question
    │
    ▼
FastAPI endpoint receives question
    │
    ▼
Agent.ainvoke() / Agent.astream()
    │
    ▼
Middleware stack processes request:
    │
    ├─ 1. PIIMiddleware (email)      — redacts emails from input
    ├─ 2. PIIMiddleware (credit_card) — masks credit cards
    ├─ 3. data_aware_prompt          — injects DataFrame context into system prompt
    ├─ 4. SummarizationMiddleware    — compresses long conversations
    ├─ 5. ModelCallLimitMiddleware   — caps LLM calls at 25/run
    ├─ 6. ToolCallLimitMiddleware    — caps tool calls at 15/run
    ├─ 7. code_sandbox (AST)         — blocks banned imports/functions
    ├─ 8. ToolRetryMiddleware        — retries failed tool calls (2x)
    ├─ 9. ModelRetryMiddleware       — retries failed LLM calls (2x)
    └─ 10. session_tool_router       — sends code to Snekbox via HTTP
           │
           ▼
    Snekbox receives full_code = setup_code + user_code
           │
           ▼
    NsJail isolates execution (cgroups, namespaces, seccomp)
           │
           ▼
    stdout returned → ToolMessage → Agent continues reasoning
```

## Snekbox Execution Flow

Since Snekbox is **stateless** (each execution is a fresh process), we prepend setup code before every execution:

```python
# Auto-generated setup code (session.get_setup_code())
import pandas as pd
import numpy as np
walmart = pd.read_csv("/data/session123_Walmart_Sales.csv")

# User's actual code (from LLM tool call)
print(walmart.groupby('Store')['Weekly_Sales'].sum().nlargest(5))
```

This full code block is sent as a single POST to `http://snekbox:8060/eval`.

## Security Model — Double Layer

### Layer 1: AST Sandbox (Application Level)

The `code_sandbox` middleware parses code with Python's `ast` module before it reaches Snekbox:

- **Banned modules**: os, subprocess, sys, socket, http, requests, etc. (33 modules)
- **Banned functions**: exec, eval, open, __import__, compile, etc. (18 functions)
- **Dunder blocking**: Any `__dunder__` attribute access is rejected

This provides friendly, descriptive error messages to the LLM so it can self-correct.

### Layer 2: NsJail (Kernel Level)

Even if the AST check misses something, Snekbox's NsJail enforces:

- **Process isolation**: Separate PID/mount/network namespaces
- **No network access**: Network namespace has no interfaces
- **Read-only filesystem**: Only `/data` is mounted (read-only)
- **Resource limits**: cgroups enforce CPU time and memory caps
- **Seccomp filters**: Blocks dangerous system calls
- **No new processes**: Cannot fork or exec

## Scaling Architecture

```
                    ┌──────────┐
                    │  Nginx   │
                    │  :8060   │
                    └────┬─────┘
                         │ round-robin
              ┌──────────┼──────────┐
              ▼          ▼          ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Snekbox  │ │ Snekbox  │ │ Snekbox  │
        │ replica 1│ │ replica 2│ │ replica 3│
        └──────────┘ └──────────┘ └──────────┘
```

Each Snekbox container handles one execution at a time. For N concurrent users, deploy N/throughput containers behind Nginx.

## Component Descriptions

| Component | File | Purpose |
|-----------|------|---------|
| Config | `config.py` | All settings: LLM, Snekbox URL, security lists, limits |
| Session Store | `session_store.py` | Per-user state: DataFrames, file paths, setup code generation |
| Agent Setup | `agent_setup.py` | LLM, middleware stack, Snekbox HTTP routing, agent creation |
| Schemas | `schemas.py` | Pydantic request/response models for API validation |
| API | `api.py` | FastAPI endpoints for sessions, upload, questions, health |
| Entry Point | `main.py` | Uvicorn server startup |
| Demo Runner | `run.py` | Library-mode demo with streaming output and stats |
| Snekbox Image | `snekbox/Dockerfile` | Custom Snekbox with pandas/numpy pre-installed |
| Orchestration | `docker-compose.yaml` | Snekbox container with mounts and resource limits |

## V1 → V2 Diff Summary

| Aspect | V1 | V2 |
|--------|----|----|
| Execution | `PythonAstREPLTool._run()` (local) | Snekbox HTTP POST (containerized) |
| State | In-process REPL locals | Stateless — setup code regenerated per call |
| Isolation | AST parsing only | AST + NsJail (cgroups, namespaces, seccomp) |
| CPU/Memory limits | None | 2GB RAM, 2 CPUs per container |
| Network blocking | None | NsJail network namespace (no interfaces) |
| Filesystem | Full host access | Read-only `/data` mount only |
| Scaling | Single process | Multiple Snekbox containers + Nginx |
| Dependencies | langchain-experimental | langchain-experimental + httpx + Docker |
