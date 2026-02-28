import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from langchain_experimental.tools import PythonAstREPLTool

from config import SAFE_BUILTINS, MAX_FILES_PER_SESSION


@dataclass
class SessionData:
    """Holds per-user session state including isolated DataFrames and REPL tool."""

    session_id: str
    user_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    dataframes: dict[str, pd.DataFrame] = field(default_factory=dict)
    repl_tool: PythonAstREPLTool = field(init=False)

    def __post_init__(self) -> None:
        self.repl_tool = PythonAstREPLTool(
            locals={"pd": pd, "np": np},
            globals={"__builtins__": SAFE_BUILTINS},
        )

    def add_dataframe(self, name: str, df: pd.DataFrame) -> str:
        """Sanitize name, store DF, inject into REPL locals. Returns sanitized name."""
        if len(self.dataframes) >= MAX_FILES_PER_SESSION:
            raise ValueError(
                f"Session limit reached: max {MAX_FILES_PER_SESSION} files per session."
            )
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        safe_name = re.sub(r"^[^a-zA-Z]", "df_", safe_name)
        if not safe_name:
            safe_name = "df"

        self.dataframes[safe_name] = df
        self.repl_tool.locals[safe_name] = df
        self.repl_tool.locals["pd"] = pd
        self.repl_tool.locals["np"] = np
        return safe_name

    def get_data_summary(self) -> str:
        """Generate markdown summary of all loaded DataFrames."""
        if not self.dataframes:
            return "No datasets loaded yet."

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

    def get_variable_names(self) -> list[str]:
        """Return list of DataFrame variable names available in REPL."""
        return list(self.dataframes.keys())


class SessionStore:
    """Thread-safe registry of user sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str) -> SessionData:
        session_id = str(uuid.uuid4())
        session = SessionData(session_id=session_id, user_id=user_id)
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> SessionData | None:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self, user_id: str | None = None) -> list[SessionData]:
        with self._lock:
            sessions = list(self._sessions.values())
        if user_id:
            sessions = [s for s in sessions if s.user_id == user_id]
        return sessions


# Module-level singleton
store = SessionStore()
