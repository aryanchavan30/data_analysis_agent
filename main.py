"""
main.py — FastAPI application entrypoint
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from router import router
# from logger import project_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # project_logger.info("Hybrid RAG + Data Analysis Agent starting up...")
    yield
    # project_logger.info("Shutting down.")


app = FastAPI(
    title="Hybrid RAG + Data Analysis Agent",
    description=(
        "Upload CSV/Excel and PDF files. "
        "Ask questions — the agent routes to PDF RAG, data analysis, or both."
    ),
    version="2.0.0",
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
    return {"status": "ok"}