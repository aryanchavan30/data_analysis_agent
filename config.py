import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

# --- File handling ---
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE_MB = 50
MAX_FILES_PER_SESSION = 5
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# --- Agent limits ---
MODEL_CALL_LIMIT = 25
TOOL_CALL_LIMIT = 15
TOOL_RETRY_MAX = 2

# --- Snekbox sandbox ---
SNEKBOX_URL = os.getenv("SNEKBOX_URL", "http://localhost:8060/eval")
SNEKBOX_TIMEOUT = 30  # seconds per execution

# --- Security: banned modules (AST pre-check before sending to Snekbox) ---
BANNED_MODULES = frozenset({
    "os", "subprocess", "shutil", "sys", "socket", "http", "requests",
    "urllib", "ftplib", "smtplib", "telnetlib", "xmlrpc", "ctypes",
    "multiprocessing", "threading", "signal", "importlib", "code",
    "codeop", "compile", "compileall", "py_compile", "zipimport",
    "pkgutil", "pathlib", "tempfile", "glob", "fnmatch", "io",
    "pickle", "shelve", "marshal", "dbm", "sqlite3", "webbrowser",
})

# --- Security: banned functions ---
BANNED_FUNCTIONS = frozenset({
    "exec", "eval", "open", "__import__", "compile",
    "execfile", "input", "breakpoint", "exit", "quit",
    "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "dir", "help", "type.__subclasses__",
})
