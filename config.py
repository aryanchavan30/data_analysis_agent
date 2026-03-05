"""
config.py — Central configuration for the hybrid RAG + Data Analysis Agent
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── LLM ──────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

# Azure OpenAI (for embeddings + RAG synthesis)
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_LLM_DEPLOYMENT", "gpt-4o")

# ── File handling ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE_MB = 50
MAX_FILES_PER_SESSION = 5       # Max CSV/Excel files per session
MAX_PDFS_PER_SESSION = 10       # Max PDF files per session

ALLOWED_CSV_EXTENSIONS = {".csv", ".xlsx", ".xls"}
ALLOWED_PDF_EXTENSIONS = {".pdf", ".txt"}

# ── Agent limits ──────────────────────────────────────────────────────────────
MODEL_CALL_LIMIT = 25
TOOL_CALL_LIMIT = 20            # Increased: hybrid queries use 2 tool calls
TOOL_RETRY_MAX = 2

# ── Snekbox (Python code sandbox) ────────────────────────────────────────────
SNEKBOX_URL = os.getenv("SNEKBOX_URL", "http://localhost:8060/eval")
SNEKBOX_TIMEOUT = 30            # seconds

# ── Qdrant (vector DB for PDF RAG) ───────────────────────────────────────────
QDRANT_ENDPOINT = os.getenv("QDRANT_ENDPOINT", "http://localhost:6333")
RAG_TOP_K = 5                   # Number of chunks to retrieve per query


# ── PostgreSQL (short-term + long-term memory) ────────────────────────────────
# Format: postgresql://user:password@host:port/dbname?sslmode=...
# Used by:
#   AsyncPostgresSaver → stores LangGraph checkpoints (conversation history)
#   AsyncPostgresStore → stores long-term user memories (cross-session)
PG_URI = os.getenv(
    "PG_URI",
    "postgresql://postgres:postgres@localhost:5432/agent_db?sslmode=disable"
)

# ── RAG chunking ──────────────────────────────────────────────────────────────
CHUNK_SIZE = 800                # words per chunk
CHUNK_OVERLAP = 150             # words overlap between chunks

# ── Security: banned Python modules (AST pre-check) ──────────────────────────
BANNED_MODULES = frozenset({
    "os", "subprocess", "shutil", "sys", "socket", "http", "requests",
    "urllib", "ftplib", "smtplib", "telnetlib", "xmlrpc", "ctypes",
    "multiprocessing", "threading", "signal", "importlib", "code",
    "codeop", "compile", "compileall", "py_compile", "zipimport",
    "pkgutil", "pathlib", "tempfile", "glob", "fnmatch", "io",
    "pickle", "shelve", "marshal", "dbm", "sqlite3", "webbrowser",
})

# ── Security: banned Python functions (AST pre-check) ────────────────────────
BANNED_FUNCTIONS = frozenset({
    "exec", "eval", "open", "__import__", "compile",
    "execfile", "input", "breakpoint", "exit", "quit",
    "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "dir", "help", "type.__subclasses__",
})