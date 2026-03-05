import asyncio
import os
import uuid
from pathlib import Path

import json
from PyPDF2 import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    QDRANT_ENDPOINT,
    RAG_TOP_K,
)
from llm import azure_llm, azure_embeddings
from session_store import SessionData
# from logger import project_logger


qdrant_client = QdrantClient(url=QDRANT_ENDPOINT)


# ─────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────

def read_pdf(path: str) -> str:
    """Extract text from PDF via PyPDF2. Returns empty string on failure."""
    try:
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n".join(pages)
    except Exception as e:
        # project_logger.error(f"[Indexer] PDF read error {path}: {e}")
        return ""


def read_txt(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        # project_logger.error(f"[Indexer] TXT read error {path}: {e}")
        return ""


# ─────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────

def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Word-level sliding window chunker.
    size=800 words, overlap=150 words → ~600 word stride.
    Overlap preserves context at chunk boundaries.
    """
    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += size - overlap

    return chunks


# ─────────────────────────────────────────────
# Document description (for system prompt)
# ─────────────────────────────────────────────

async def generate_document_description(sample_text: str, filename: str) -> str:
    """
    Asks Azure OpenAI to summarize a document in 4-5 lines.
    Returns the description string (fallback-safe).

    Used to tell the LLM router what each PDF contains,
    enabling correct tool selection (RAG vs analysis vs hybrid).
    """
    messages = [
        (
            "system",
            "You are a strict JSON generator. Return ONLY valid JSON. No markdown, no explanation.",
        ),
        (
            "user",
            f"""Analyze the text below from file "{filename}".
Generate a concise 4-5 line description covering:
- The document's purpose and scope
- Key topics, formulas, or methodologies it contains
- Whether it contains any quantitative formulas or calculation methods

Return ONLY this JSON:
{{
  "description": "<4-5 line description>",
  "has_formulas": true/false,
  "key_topics": ["topic1", "topic2"]
}}

TEXT (first ~2000 chars):
{sample_text[:2000]}
""",
        ),
    ]

    try:
        response = await azure_llm.ainvoke(messages)
        content = response.content.strip()

        # Strip markdown fences if present (LLMs often ignore "no markdown")
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()

        parsed = json.loads(content)
        description = parsed.get("description", "")
        has_formulas = parsed.get("has_formulas", False)
        key_topics = parsed.get("key_topics", [])

        full_desc = description
        if has_formulas:
            full_desc += " (Contains quantitative formulas/calculations.)"
        if key_topics:
            full_desc += f" Key topics: {', '.join(key_topics)}."

        return full_desc or f"Document: {filename}"

    except Exception as e:
        # project_logger.warning(f"[Indexer] Description generation failed for {filename}: {e}")
        return f"Document: {filename} (description unavailable)"


# ─────────────────────────────────────────────
# Main indexing pipeline
# ─────────────────────────────────────────────

async def index_pdfs_for_session(
    session: SessionData,
    pdf_paths: list[str],
    filenames: list[str],
) -> dict[str, str]:
    """
    Full ingestion pipeline:
        Read → Chunk → Describe → Embed → Qdrant upsert → Update session

    Args:
        session:    The SessionData to update (sets collection name + summaries)
        pdf_paths:  Absolute paths to uploaded PDF files on disk
        filenames:  Original filenames (for display + citation)

    Returns:
        Dict of {filename: description} for all successfully processed PDFs

    Raises:
        Exception if no valid content could be extracted from any file
    """
    # project_logger.info(f"[Indexer] Starting for session {session.session_id}, files: {filenames}")

    COLLECTION_NAME = f"{session.session_id}_pdf_collection"

    all_chunks: list[str] = []
    chunk_meta: list[dict] = []
    summaries: dict[str, str] = {}

    # ── Step 1: Read + chunk + describe ──────────────────────────────────────
    for path, filename in zip(pdf_paths, filenames):
        ext = Path(filename).suffix.lower()

        if ext == ".pdf":
            full_text = read_pdf(path)
        elif ext == ".txt":
            full_text = read_txt(path)
        else:
            # project_logger.warning(f"[Indexer] Unsupported: {filename}")
            continue

        if not full_text.strip():
            # project_logger.warning(f"[Indexer] Empty file: {filename}")
            continue

        chunks = chunk_text(full_text)
        if not chunks:
            # project_logger.warning(f"[Indexer] No chunks from: {filename}")
            continue

        # Generate description from first 5 chunks (~4000 words)
        sample = "\n".join(chunks[:5])
        description = await generate_document_description(sample, filename)
        summaries[filename] = description

        # project_logger.info(f"[Indexer] {filename}: {len(chunks)} chunks, description ready")

        for chunk in chunks:
            all_chunks.append(chunk)
            chunk_meta.append({
                "source_file": filename,
                "document_summary": description,
            })

    if not all_chunks:
        raise Exception("No valid text content found in uploaded PDFs.")

    # project_logger.info(f"[Indexer] Total chunks: {len(all_chunks)}")

    # ── Step 2: Batch embedding (async, non-blocking) ─────────────────────────
    loop = asyncio.get_running_loop()
    all_vectors = await loop.run_in_executor(
        None,
        azure_embeddings.embed_documents,
        all_chunks,
    )
    # project_logger.info(f"[Indexer] Embedded {len(all_vectors)} chunks")

    # ── Step 3: Qdrant collection (create or rebuild) ─────────────────────────
    vector_size = len(all_vectors[0])

    existing_collections = [c.name for c in qdrant_client.get_collections().collections]

    if COLLECTION_NAME in existing_collections:
        # Rebuild: delete old vectors, re-index with new files
        # project_logger.info(f"[Indexer] Rebuilding collection: {COLLECTION_NAME}")
        qdrant_client.delete_collection(COLLECTION_NAME)

    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    # project_logger.info(f"[Indexer] Collection created: {COLLECTION_NAME}")

    # ── Step 4: Build PointStructs ────────────────────────────────────────────
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=all_vectors[i],
            payload={
                "text": all_chunks[i],
                "source_file": chunk_meta[i]["source_file"],
                "document_summary": chunk_meta[i]["document_summary"],
            },
        )
        for i in range(len(all_chunks))
    ]

    # ── Step 5: Async upsert ──────────────────────────────────────────────────
    await loop.run_in_executor(
        None,
        lambda: qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
        ),
    )
    # project_logger.info(f"[Indexer] Upserted {len(points)} points to {COLLECTION_NAME}")

    # ── Step 6: Update session state ──────────────────────────────────────────
    session.set_pdf_collection(COLLECTION_NAME)
    for filename, description in summaries.items():
        session.add_pdf_summary(filename, description)

    # project_logger.info(f"[Indexer] Session updated. Summaries: {list(summaries.keys())}")

    return summaries