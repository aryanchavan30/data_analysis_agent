"""
schemas.py — Pydantic request/response models for the hybrid agent API
"""

from typing import Optional
from pydantic import BaseModel, Field


# ── Session ───────────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128, description="Unique user identifier")


class CreateSessionResponse(BaseModel):
    session_id: str
    user_id: str
    message: str = "Session created successfully."


class SessionInfo(BaseModel):
    session_id: str
    user_id: str
    csv_datasets: list[str]
    csv_count: int
    pdf_files: list[str]
    pdf_count: int
    pdf_collection: Optional[str]
    created_at: str


# ── CSV Upload ────────────────────────────────────────────────────────────────

class CsvUploadResponse(BaseModel):
    filename: str
    variable_name: str          # Sanitized Python variable name (e.g. "walmart_sales")
    shape: list[int]            # [rows, cols]
    columns: list[str]
    dtypes: dict[str, str]      # {column_name: dtype_string}
    preview: str                # First 5 rows as string
    encoding: str
    message: str = "CSV/Excel uploaded and loaded successfully."


# ── PDF Upload ────────────────────────────────────────────────────────────────

class PdfUploadResponse(BaseModel):
    uploaded_files: list[str]
    collection_name: Optional[str]
    summaries: dict[str, str]   # {filename: description}
    message: str


# ── Q&A ───────────────────────────────────────────────────────────────────────

class QuestionRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Natural language question about your uploaded data or documents.",
    )


class QuestionResponse(BaseModel):
    """Used for non-streaming responses (optional sync endpoint)."""
    answer: str
    session_id: str
    tools_used: list[str] = []