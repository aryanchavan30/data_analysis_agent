"""
rag_tool.py — Qdrant RAG search for hybrid agent

Responsibilities:
    - Embed user query via Azure OpenAI embeddings
    - Search the session's Qdrant collection (top-K chunks)
    - Format retrieved chunks with inline citations
    - Call Azure LLM to synthesize a grounded answer
    - Return plain text answer (with source attribution)

Called by:
    - rag_tool_router middleware   (pure PDF queries)
    - hybrid_tool_router middleware (PDF → CSV hybrid queries, Step 1)
"""

from langchain_core.messages import SystemMessage, HumanMessage
from qdrant_client import QdrantClient

from config import QDRANT_ENDPOINT, RAG_TOP_K
from llm import azure_llm, azure_embeddings
from session_store import store
# from logger import project_logger


# Module-level Qdrant client (shared, thread-safe)
qdrant_client = QdrantClient(url=QDRANT_ENDPOINT)

RAG_SYSTEM_PROMPT = """You are a precise document analyst.
Answer ONLY using the provided context chunks.
Cite sources inline using their number, e.g. [1], [2].
If the answer is not in the context, say: "Information not found in the provided documents."
Do NOT fabricate, infer, or add external knowledge.
Be concise and structured.
"""


async def rag_search(query: str, session_id: str) -> str:
    """
    Full RAG pipeline:
        embed query → Qdrant search → build context → LLM synthesis → answer

    Returns:
        Plain text answer with inline citations and a sources block.
        Falls back to an error string if anything fails.
    """
    # project_logger.info(f"[RAG] query='{query}' session='{session_id}'")

    # ── 1. Get collection name from session ──────────────────────────────────
    session = store.get(session_id)
    if not session:
        return f"Error: session '{session_id}' not found."

    collection_name = session.get_pdf_collection_name()
    if not collection_name:
        return "No PDF documents have been uploaded for this session. Please upload a PDF first."

    # ── 2. Embed the query ───────────────────────────────────────────────────
    try:
        query_vector = azure_embeddings.embed_query(query)
    except Exception as e:
        # project_logger.error(f"[RAG] Embedding error: {e}")
        return f"RAG embedding error: {e}"

    # ── 3. Qdrant vector search ──────────────────────────────────────────────
    try:
        search_result = qdrant_client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=RAG_TOP_K,
        )
    except Exception as e:
        # project_logger.error(f"[RAG] Qdrant search error: {e}")
        return f"RAG search error: {e}"

    if not search_result or not search_result.points:
        return "No relevant content found in the uploaded PDF documents for this query."

    # ── 4. Build deduplicated citation index ─────────────────────────────────
    #   Multiple chunks from same file → same citation number
    raw_sources = [
        point.payload.get("source_file", "Unknown")
        for point in search_result.points
        if "text" in point.payload
    ]

    file_to_index: dict[str, int] = {}
    counter = 1
    for src in raw_sources:
        if src not in file_to_index:
            file_to_index[src] = counter
            counter += 1

    # ── 5. Build context string ──────────────────────────────────────────────
    context_chunks = []
    citations_used: dict[int, str] = {}

    for point in search_result.points:
        payload = point.payload
        if "text" not in payload:
            continue
        source = payload.get("source_file", "Unknown")
        idx = file_to_index[source]
        score = round(point.score, 3) if hasattr(point, "score") else "?"
        context_chunks.append(f"[{idx}] (relevance={score})\n{payload['text']}")
        citations_used[idx] = source

    if not context_chunks:
        return "No usable content found in search results."

    context = "\n\n".join(context_chunks)

    # ── 6. LLM synthesis ─────────────────────────────────────────────────────
    messages = [
        SystemMessage(content=RAG_SYSTEM_PROMPT),
        HumanMessage(content=(
            f"Context (each chunk prefixed with its citation number):\n\n"
            f"{context}\n\n"
            f"Question: {query}\n\n"
            f"Answer with inline citations like [1], [2]. "
            f"If the question asks for a formula or calculation method, "
            f"present it clearly so it can be applied programmatically."
        )),
    ]

    try:
        response = await azure_llm.ainvoke(messages)
        answer = response.content.strip()
    except Exception as e:
        # project_logger.error(f"[RAG] LLM error: {e}")
        return f"RAG LLM synthesis error: {e}"

    # ── 7. Append sources block ───────────────────────────────────────────────
    citation_lines = [f"[{idx}] {src}" for idx, src in sorted(citations_used.items())]
    sources_block = "\n".join(citation_lines)
    final_answer = f"{answer}\n\n---\nSources:\n{sources_block}"

    # project_logger.info(f"[RAG] answer length={len(final_answer)}")
    return final_answer