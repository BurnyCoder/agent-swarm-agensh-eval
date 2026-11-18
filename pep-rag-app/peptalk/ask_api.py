"""``POST /api/ask``: retrieve, then answer with cited sources.

Mounted by the web app through :func:`add_ask_routes`. The retriever and the
LLM are injected so tests can pass fakes; by default the LLM is built from the
environment on the first request (see ``peptalk.generate.llm_config_from_env``).

Request:  {"question": "...", "mode": "hybrid", "k": 5}
Response: {"question", "answer", "mode", "k", "grounded", "model",
           "chunks": [hit...], "cited": [chunk_id...], "dropped_citations": [...]}

Each hit has rank, pep, title, section, score, snippet, chunk_id, start, end.
``chunks`` lists everything the model saw, in prompt order; ``cited`` lists the
chunk_ids the answer cites. ``chunks[i].cited`` is True for those.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Sequence

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from peptalk.generate import LLM, LLMConfigError, ask, llm_from_env

MODES = ("bm25", "dense", "hybrid")
HIT_FIELDS = ("pep", "title", "section", "score", "snippet", "chunk_id", "start", "end")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["bm25", "dense", "hybrid"] = "hybrid"
    k: int = Field(default=5, ge=1, le=20)


def hit_to_dict(hit: Any, rank: int) -> dict[str, Any]:
    """Serialise one retrieved chunk in the shared search-result shape."""
    out: dict[str, Any] = {"rank": rank}
    for name in HIT_FIELDS:
        out[name] = getattr(hit, name, None)
    return out


def add_ask_routes(
    app: FastAPI,
    retrieve: Callable[[str, str, int], Sequence[Any]],
    llm: LLM | None = None,
) -> None:
    """Add ``POST /api/ask`` to ``app``.

    ``retrieve(question, mode, k)`` returns hits (objects with the fields in
    ``HIT_FIELDS`` plus ``text``). ``llm`` defaults to the Anthropic client built
    from the environment, created on first use.
    """
    router = APIRouter(prefix="/api")
    state: dict[str, LLM] = {}

    def get_llm() -> LLM:
        if llm is not None:
            return llm
        if "llm" not in state:
            try:
                state["llm"] = llm_from_env()
            except LLMConfigError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return state["llm"]

    @router.post("/ask")
    def post_ask(body: AskRequest) -> dict[str, Any]:
        model = get_llm()
        try:
            result = ask(
                body.question,
                retrieve,
                model,
                mode=body.mode,
                k=body.k,
                model=getattr(model, "model", ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cited_ids = {getattr(p, "chunk_id", None) for p in result.cited}
        chunks = []
        for rank, passage in enumerate(result.passages, start=1):
            item = hit_to_dict(passage, rank)
            item["cited"] = getattr(passage, "chunk_id", None) in cited_ids
            chunks.append(item)
        return {
            "question": result.question,
            "answer": result.answer,
            "mode": result.mode,
            "k": body.k,
            "grounded": result.grounded,
            "model": result.model,
            "chunks": chunks,
            "cited": [c["chunk_id"] for c in chunks if c["cited"]],
            "dropped_citations": result.dropped_citations,
        }

    app.include_router(router)
