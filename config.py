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

# --- Security: banned modules ---
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

# --- Whitelisted builtins (needed because globals={"__builtins__": {}} breaks pandas) ---
SAFE_BUILTINS = {
    "range": range,
    "len": len,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "print": print,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "any": any,
    "all": all,
    "hasattr": hasattr,
    "repr": repr,
    "format": format,
    "id": id,
    "hash": hash,
    "callable": callable,
    "iter": iter,
    "next": next,
    "slice": slice,
    "type": type,
    "object": object,
    "property": property,
    "staticmethod": staticmethod,
    "classmethod": classmethod,
    "super": super,
    "True": True,
    "False": False,
    "None": None,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "StopIteration": StopIteration,
    "Exception": Exception,
    "RuntimeError": RuntimeError,
    "ZeroDivisionError": ZeroDivisionError,
    "NotImplementedError": NotImplementedError,
}
