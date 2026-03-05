"""
router.py — FastAPI routes for the hybrid RAG + Data Analysis Agent

Endpoints:
    POST /sessions                    → Create a new user session
    POST /sessions/{id}/upload/csv   → Upload CSV or Excel file
    POST /sessions/{id}/upload/pdf   → Upload PDF file(s) → index into Qdrant
    POST /sessions/{id}/ask          → Ask a question (streaming SSE)
    GET  /sessions/{id}              → Session metadata
    DELETE /sessions/{id}            → Delete session + cleanup
"""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import AsyncIterator

import chardet
import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from agent_setup import agent
from config import (
    ALLOWED_CSV_EXTENSIONS,
    ALLOWED_PDF_EXTENSIONS,
    MAX_FILE_SIZE_MB,
    MAX_FILES_PER_SESSION,
    MAX_PDFS_PER_SESSION,
    UPLOAD_DIR,
)
from rag_indexer import index_pdfs_for_session   # builds Qdrant collection
from schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    CsvUploadResponse,
    PdfUploadResponse,
    QuestionRequest,
    QuestionResponse,
    SessionInfo,
)
from session_store import store
# from logger import project_logger

router = APIRouter(prefix="/sessions", tags=["Hybrid Agent"])


# ─────────────────────────────────────────────────────────────────────────────
# POST /sessions — Create session
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(body: CreateSessionRequest) -> CreateSessionResponse:
    session = store.create(user_id=body.user_id)
    # project_logger.info(f"Session created: {session.session_id} for user {body.user_id}")
    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=body.user_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /sessions/{session_id}/upload/csv — Upload CSV or Excel
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{session_id}/upload/csv",
    response_model=CsvUploadResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_csv(
    session_id: str,
    file: UploadFile = File(...),
) -> CsvUploadResponse:
    session = _get_session_or_404(session_id)

    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_CSV_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {ALLOWED_CSV_EXTENSIONS}",
        )

    # Validate file count
    if len(session.dataframes) >= MAX_FILES_PER_SESSION:
        raise HTTPException(
            status_code=400,
            detail=f"Session already has {MAX_FILES_PER_SESSION} CSV/Excel files (limit reached).",
        )

    # Read + size check
    content = await file.read()
    _check_file_size(content, file.filename)

    # Detect encoding
    detected = chardet.detect(content)
    encoding = detected.get("encoding") or "utf-8"

    # Save to disk (Snekbox mounts UPLOAD_DIR as /data/)
    safe_stem = Path(file.filename).stem
    dest_path = UPLOAD_DIR / f"{session_id}_{uuid.uuid4().hex[:8]}_{file.filename}"
    dest_path.write_bytes(content)
    # project_logger.info(f"CSV saved: {dest_path}")

    # Parse into DataFrame
    try:
        if ext == ".csv":
            df = pd.read_csv(dest_path, encoding=encoding)
        else:
            df = pd.read_excel(dest_path)
    except Exception as e:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Failed to parse file: {e}")

    # Register in session
    var_name = session.add_dataframe(
        name=safe_stem,
        df=df,
        file_path=str(dest_path),
        encoding=encoding,
    )

    # project_logger.info(f"[{session_id}] CSV loaded as `{var_name}` {df.shape}")

    return CsvUploadResponse(
        filename=file.filename,
        variable_name=var_name,
        shape=list(df.shape),
        columns=list(df.columns),
        dtypes={col: str(dtype) for col, dtype in zip(df.columns, df.dtypes)},
        preview=df.head(5).to_string(index=False),
        encoding=encoding,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /sessions/{session_id}/upload/pdf — Upload PDF(s) → Qdrant
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{session_id}/upload/pdf",
    response_model=PdfUploadResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_pdf(
    session_id: str,
    files: list[UploadFile] = File(...),
) -> PdfUploadResponse:
    session = _get_session_or_404(session_id)

    # Validate counts
    existing_pdfs = len(session.pdf_files)
    if existing_pdfs + len(files) > MAX_PDFS_PER_SESSION:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Would exceed PDF limit: {existing_pdfs} uploaded + "
                f"{len(files)} new > {MAX_PDFS_PER_SESSION} max."
            ),
        )

    # Validate extensions + sizes
    saved_paths: list[str] = []
    filenames: list[str] = []

    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_PDF_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File '{f.filename}' is not a PDF.",
            )
        content = await f.read()
        _check_file_size(content, f.filename)

        dest = UPLOAD_DIR / f"{session_id}_{uuid.uuid4().hex[:8]}_{f.filename}"
        dest.write_bytes(content)
        saved_paths.append(str(dest))
        filenames.append(f.filename)
        # project_logger.info(f"PDF saved: {dest}")

    # Index PDFs into Qdrant (chunking + embedding + upsert)
    # This also updates session.pdf_collection_name and session.pdf_summaries
    try:
        summaries = await index_pdfs_for_session(
            session=session,
            pdf_paths=saved_paths,
            filenames=filenames,
        )
    except Exception as e:
        # Clean up saved files on failure
        for p in saved_paths:
            Path(p).unlink(missing_ok=True)
        # project_logger.error(f"[{session_id}] PDF indexing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF indexing failed: {e}")

    # project_logger.info(f"[{session_id}] PDFs indexed: {filenames}")

    return PdfUploadResponse(
        uploaded_files=filenames,
        collection_name=session.get_pdf_collection_name(),
        summaries=summaries,
        message=f"{len(filenames)} PDF(s) uploaded and indexed successfully.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /sessions/{session_id}/ask — Ask a question (streaming SSE)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{session_id}/ask")
async def ask(
    session_id: str,
    body: QuestionRequest,
) -> StreamingResponse:
    session = _get_session_or_404(session_id)

    # Guard: at least one data source must be loaded
    if not session.has_dataframes() and not session.has_pdfs():
        raise HTTPException(
            status_code=400,
            detail="No data loaded. Please upload at least one CSV/Excel or PDF file first.",
        )

    return StreamingResponse(
        _stream_agent_response(session_id=session_id, question=body.question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Important for nginx proxying
        },
    )


async def _stream_agent_response(
    session_id: str,
    question: str,
) -> AsyncIterator[str]:
    """
    Streams agent reasoning steps as Server-Sent Events (SSE).

    Event types emitted:
        thought     → LLM reasoning text
        tool_call   → Tool name + args
        observation → Tool execution result
        final       → Final answer
        error       → Any error
        done        → Stream complete
    """
    config = {"configurable": {"thread_id": session_id}}
    context = {"session_id": session_id}

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    try:
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
            context=context,
            stream_mode="updates",
        ):
            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                for msg in update.get("messages", []):
                    msg_type = getattr(msg, "type", "unknown")

                    if msg_type == "ai":
                        # LLM reasoning
                        if msg.content:
                            yield _sse("thought", {
                                "node": node_name,
                                "content": msg.content,
                            })

                        # Tool invocations
                        for tc in getattr(msg, "tool_calls", []):
                            yield _sse("tool_call", {
                                "tool": tc["name"],
                                "args": tc["args"],
                            })

                    elif msg_type == "tool":
                        # Tool result
                        yield _sse("observation", {
                            "tool": getattr(msg, "name", "?"),
                            "content": msg.content or "(no output)",
                        })

        # Final answer = last AI message content
        yield _sse("done", {"message": "Stream complete."})

    except Exception as e:
        # project_logger.error(f"[{session_id}] Stream error: {e}", exc_info=True)
        yield _sse("error", {"message": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# GET /sessions/{session_id} — Session info
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str) -> SessionInfo:
    session = _get_session_or_404(session_id)
    return SessionInfo(
        session_id=session.session_id,
        user_id=session.user_id,
        csv_datasets=session.get_variable_names(),
        csv_count=len(session.dataframes),
        pdf_files=session.pdf_files,
        pdf_count=len(session.pdf_files),
        pdf_collection=session.get_pdf_collection_name(),
        created_at=session.created_at.isoformat(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /sessions/{session_id} — Delete session + cleanup files
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str) -> None:
    session = _get_session_or_404(session_id)

    # Delete uploaded files from disk
    for fpath in session.file_paths.values():
        Path(fpath).unlink(missing_ok=True)

    # Note: Qdrant collection cleanup is separate (reused across re-uploads)
    # Optionally delete: qdrant_client.delete_collection(session.pdf_collection_name)

    store.delete(session_id)
    # project_logger.info(f"Session deleted: {session_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_session_or_404(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found.",
        )
    return session


def _check_file_size(content: bytes, filename: str) -> None:
    size_mb = len(content) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File '{filename}' is {size_mb:.1f} MB, exceeds {MAX_FILE_SIZE_MB} MB limit.",
        )