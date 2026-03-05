import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from config import MAX_FILES_PER_SESSION, MAX_PDFS_PER_SESSION


@dataclass
class SessionData:
    """
    Per-user session state.

    Separates two types of uploaded files:
        CSV/Excel → stored as DataFrames in memory + file path for Snekbox
        PDF       → indexed into Qdrant; only collection name stored here
    """

    session_id: str
    user_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)

    # ── CSV/Excel state ───────────────────────────────────────────────────────
    dataframes: dict[str, pd.DataFrame] = field(default_factory=dict)
    file_paths: dict[str, str] = field(default_factory=dict)
    file_encodings: dict[str, str] = field(default_factory=dict)

    # ── PDF state ─────────────────────────────────────────────────────────────
    # Collection name in Qdrant for this session's PDFs
    pdf_collection_name: Optional[str] = field(default=None)
    # {filename: description} for system prompt injection
    pdf_summaries: dict[str, str] = field(default_factory=dict)
    # Track PDF filenames for display
    pdf_files: list[str] = field(default_factory=list)

    # ── CSV/Excel methods ─────────────────────────────────────────────────────

    def add_dataframe(
        self,
        name: str,
        df: pd.DataFrame,
        file_path: str = "",
        encoding: str = "utf-8",
    ) -> str:
        """
        Sanitize variable name, store DataFrame + metadata.
        Returns the sanitized variable name used in Python code.
        Raises ValueError if session file limit is reached.
        """
        if len(self.dataframes) >= MAX_FILES_PER_SESSION:
            raise ValueError(
                f"CSV/Excel limit reached: max {MAX_FILES_PER_SESSION} files per session."
            )

        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        if not safe_name or not safe_name[0].isalpha():
            safe_name = "df_" + safe_name
        safe_name = safe_name or "df"

        self.dataframes[safe_name] = df
        if file_path:
            self.file_paths[safe_name] = file_path
            self.file_encodings[safe_name] = encoding

        return safe_name

    def get_setup_code(self) -> str:
        """
        Generate Python bootstrap code for Snekbox execution.

        Since Snekbox is stateless (each call is a fresh process),
        we prepend import statements and DataFrame reload code so the
        agent's code always has access to all variables.

        Files are mounted at /data/ inside the Snekbox Docker container.
        """
        import os

        lines = [
            "import pandas as pd",
            "import numpy as np",
            "import warnings",
            "warnings.filterwarnings('ignore')",
        ]

        for var_name, fpath in self.file_paths.items():
            container_path = "/data/" + os.path.basename(fpath)
            ext = os.path.splitext(fpath)[1].lower()
            enc = self.file_encodings.get(var_name, "utf-8")

            if ext == ".csv":
                lines.append(
                    f'{var_name} = pd.read_csv("{container_path}", encoding="{enc}")'
                )
            elif ext in (".xlsx", ".xls"):
                lines.append(
                    f'{var_name} = pd.read_excel("{container_path}")'
                )

        return "\n".join(lines)

    def get_data_summary(self) -> str:
        """
        Markdown summary of all loaded DataFrames injected into system prompt.
        Shows shape, column types, and 3-row preview.
        """
        if not self.dataframes:
            return "No CSV/Excel datasets loaded yet."

        parts: list[str] = []
        for name, df in self.dataframes.items():
            cols_info = ", ".join(
                f"`{col}` ({dtype})" for col, dtype in zip(df.columns, df.dtypes)
            )
            preview = df.head(3).to_string(index=False)
            parts.append(
                f"### `{name}` ({df.shape[0]} rows × {df.shape[1]} cols)\n"
                f"**Columns**: {cols_info}\n"
                f"**Preview**:\n```\n{preview}\n```"
            )

        return "\n\n".join(parts)

    def get_variable_names(self) -> list[str]:
        """Return list of DataFrame variable names available in Python env."""
        return list(self.dataframes.keys())

    # ── PDF methods ───────────────────────────────────────────────────────────

    def set_pdf_collection(self, collection_name: str) -> None:
        """Set the Qdrant collection name for this session's PDFs."""
        self.pdf_collection_name = collection_name

    def get_pdf_collection_name(self) -> Optional[str]:
        """Return Qdrant collection name, or None if no PDFs uploaded."""
        return self.pdf_collection_name

    def add_pdf_summary(self, filename: str, description: str) -> None:
        """Store per-PDF description for system prompt injection."""
        if filename not in self.pdf_files:
            self.pdf_files.append(filename)
        self.pdf_summaries[filename] = description

    def get_pdf_summary(self) -> str:
        """
        Markdown summary of all uploaded PDFs injected into system prompt.
        Tells the LLM what each PDF contains so it can route correctly.
        """
        if not self.pdf_summaries:
            return "No PDF documents loaded yet."

        parts: list[str] = []
        for filename, description in self.pdf_summaries.items():
            parts.append(f"**{filename}**\n{description}")

        return "\n\n".join(parts)

    def has_pdfs(self) -> bool:
        return bool(self.pdf_collection_name and self.pdf_files)

    def has_dataframes(self) -> bool:
        return bool(self.dataframes)


class SessionStore:
    """Thread-safe in-memory registry of user sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str) -> SessionData:
        session_id = str(uuid.uuid4())
        session = SessionData(session_id=session_id, user_id=user_id)
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[SessionData]:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self, user_id: Optional[str] = None) -> list[SessionData]:
        with self._lock:
            sessions = list(self._sessions.values())
        if user_id:
            sessions = [s for s in sessions if s.user_id == user_id]
        return sessions


# Module-level singleton used across the app
store = SessionStore()