import json
from pathlib import Path

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB, SNEKBOX_URL, UPLOAD_DIR
from schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    QuestionRequest,
    QuestionResponse,
    SessionInfo,
    UploadResponse,
)
from session_store import store
from agent_setup import agent

app = FastAPI(title="Data Analysis Agent API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Health ----------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/snekbox")
async def health_snekbox():
    """Ping Snekbox to verify it's running."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SNEKBOX_URL,
                json={"input": "print('ok')"},
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()
            stdout = result.get("stdout", "").strip()
            if stdout == "ok":
                return {"status": "ok", "snekbox": "connected"}
            return {"status": "degraded", "snekbox": "unexpected response", "stdout": stdout}
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Snekbox is not reachable.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Snekbox health check failed: {e}")


# ---------- Sessions ----------

@app.post("/sessions", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest):
    session = store.create(user_id=req.user_id)
    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
    )


@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return SessionInfo(
        session_id=session.session_id,
        user_id=session.user_id,
        datasets=list(session.dataframes.keys()),
        dataset_count=len(session.dataframes),
    )


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    if not store.delete(session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"message": "Session deleted."}


# ---------- Upload ----------

@app.post("/sessions/{session_id}/upload", response_model=UploadResponse)
async def upload_file(session_id: str, file: UploadFile = File(...)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    # Validate extension
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read and validate size
    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f} MB). Max: {MAX_FILE_SIZE_MB} MB.",
        )

    # Save to disk
    save_path = UPLOAD_DIR / f"{session_id}_{filename}"
    save_path.write_bytes(content)

    # Parse into DataFrame (try multiple encodings for CSV)
    df = None
    used_encoding = "utf-8"

    if ext == ".csv":
        CSV_ENCODINGS = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"]
        errors: list[str] = []
        for enc in CSV_ENCODINGS:
            try:
                df = pd.read_csv(save_path, encoding=enc)
                used_encoding = enc
                break
            except UnicodeDecodeError:
                errors.append(f"{enc}: UnicodeDecodeError")
            except Exception as e:
                save_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {e}")

        if df is None:
            save_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=f"Failed to parse CSV with any encoding. Tried: {', '.join(CSV_ENCODINGS)}",
            )
    else:
        try:
            df = pd.read_excel(save_path)
        except Exception as e:
            save_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {e}")

    # Register in session (with file path + encoding for Snekbox mounting)
    stem = Path(filename).stem
    try:
        var_name = session.add_dataframe(
            stem, df, file_path=str(save_path), encoding=used_encoding,
        )
    except ValueError as e:
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))

    preview = df.head(5).to_string(index=False)
    return UploadResponse(
        filename=filename,
        variable_name=var_name,
        shape=list(df.shape),
        columns=list(df.columns),
        preview=preview,
    )


# ---------- Ask (non-streaming) ----------

@app.post("/sessions/{session_id}/ask", response_model=QuestionResponse)
async def ask_question(session_id: str, req: QuestionRequest):
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    config = {"configurable": {"thread_id": session_id}}
    context = {"session_id": session_id}

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": req.question}]},
        config=config,
        context=context,
    )

    # Extract the last AI message
    messages = result.get("messages", [])
    answer = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
            answer = msg.content
            break
        elif isinstance(msg, dict) and msg.get("role") == "assistant":
            answer = msg.get("content", "")
            break

    if not answer:
        answer = "I wasn't able to generate a response. Please try rephrasing your question."

    return QuestionResponse(answer=answer, session_id=session_id)


# ---------- Ask (SSE streaming) ----------

@app.post("/sessions/{session_id}/ask/stream")
async def ask_question_stream(session_id: str, req: QuestionRequest):
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    config = {"configurable": {"thread_id": session_id}}
    context = {"session_id": session_id}

    async def event_generator():
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": req.question}]},
            config=config,
            context=context,
            stream_mode="updates",
        ):
            for node_name, update in chunk.items():
                if "messages" in update:
                    for msg in update["messages"]:
                        event_data = {
                            "node": node_name,
                            "type": getattr(msg, "type", "unknown"),
                            "content": getattr(msg, "content", str(msg)),
                        }
                        yield f"data: {json.dumps(event_data)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
