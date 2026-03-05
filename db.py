from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from config import PG_URI
# from logger import project_loggere

# ── Module-level references (set during lifespan startup) ─────────────────────
# Imported by agent_setup.py and anywhere else that needs DB access.
checkpointer: AsyncPostgresSaver | None = None
store: AsyncPostgresStore | None = None

# Keep the context managers alive for the full app lifetime
_checkpointer_ctx = None
_store_ctx = None


async def init_db() -> None:
    """
    Open async Postgres connections and run schema migrations.
    Call once from FastAPI lifespan before the agent is created.

    Connection pooling is handled internally by psycopg — no need for
    a separate pool manager.
    """
    global checkpointer, store, _checkpointer_ctx, _store_ctx

    # project_logger.info("[DB] Connecting to Postgres...")

    # ── Checkpointer (short-term / per-thread memory) ─────────────────────────
    _checkpointer_ctx = AsyncPostgresSaver.from_conn_string(PG_URI)
    checkpointer = await _checkpointer_ctx.__aenter__()
    await checkpointer.setup()   # idempotent — creates tables if not exist
    # project_logger.info("[DB] Checkpointer ready")

    # ── Store (long-term / cross-session memory) ──────────────────────────────
    _store_ctx = AsyncPostgresStore.from_conn_string(PG_URI)
    store = await _store_ctx.__aenter__()
    await store.setup()          # idempotent — creates tables if not exist
    # project_logger.info("[DB] Store ready")


async def close_db() -> None:
    """
    Cleanly close Postgres connections.
    Call from FastAPI lifespan shutdown.
    """
    global _checkpointer_ctx, _store_ctx

    if _checkpointer_ctx:
        try:
            await _checkpointer_ctx.__aexit__(None, None, None)
            # project_logger.info("[DB] Checkpointer closed")
        except Exception as e:
            print(f"[DB] Checkpointer close error: {e}")

    if _store_ctx:
        try:
            await _store_ctx.__aexit__(None, None, None)
            # project_logger.info("[DB] Store closed")
        except Exception as e:
            print(f"[DB] Store close error: {e}")