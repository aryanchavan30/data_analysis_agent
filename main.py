"""
main.py — FastAPI application entrypoint

Startup sequence:
    1. init_db()             — connect Postgres, run schema migrations
    2. create_hybrid_agent() — build agent with live checkpointer + store
    3. App ready to serve requests

Shutdown sequence:
    1. close_db()            — cleanly close Postgres connections

This ordering guarantees that agent_setup.agent is never None when a
request arrives, and that DB connections are always closed on exit.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import db
import agent_setup
from agent_setup import create_hybrid_agent
from router import router
# from logger import project_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    # project_logger.info("=== Hybrid RAG + Data Analysis Agent starting ===")

    # 1. Connect Postgres + run migrations (idempotent)
    await db.init_db()

    # 2. Build agent with live Postgres checkpointer + store
    agent_setup.agent = create_hybrid_agent()
    # project_logger.info("Agent created with PostgreSQL memory")

    # project_logger.info("=== App ready ===")

    yield  # App is running — handle requests

    # ── Shutdown ──────────────────────────────────────────────────────────────
    # project_logger.info("=== Shutting down ===")
    await db.close_db()
    # project_logger.info("=== Shutdown complete ===")


app = FastAPI(
    title="Hybrid RAG + Data Analysis Agent",
    description=(
        "Upload CSV/Excel and PDF files. "
        "Ask questions — the agent routes to PDF RAG, data analysis, or both. "
        "Conversation history persisted in PostgreSQL."
    ),
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "db_checkpointer": db.checkpointer is not None,
        "db_store": db.store is not None,
        "agent": agent_setup.agent is not None,
    }