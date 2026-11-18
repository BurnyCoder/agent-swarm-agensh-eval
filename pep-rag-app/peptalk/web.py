"""FastAPI web app: JSON API plus the server-rendered search/ask page.

``create_app(retriever, llm=None)`` is built from injected components so tests
can pass fakes:

- ``retriever`` has ``search(query, mode, k) -> list[hit]`` where each hit is flat
  (``pep``, ``title``, ``section``, ``score``, ``snippet``, ``chunk_id``, ``start``,
  ``end``, ``text``). ``peptalk.service.RetrievalService`` provides this. An
  optional boolean ``dense_available`` attribute refuses dense/hybrid when false.
- ``POST /api/ask`` is always mounted via ``peptalk.ask_api.add_ask_routes``. With
  ``llm=None`` the model client is built from the environment on the first request
  (503 when the route is not configured). Pass ``llm`` to inject a client.

JSON contract (also in README):

- ``GET /api/search?q=&mode=&k=`` -> ``{"query", "mode", "k", "results": [hit]}``
- ``POST /api/ask`` ``{"question", "mode", "k"}`` ->
  ``{"question", "answer", "mode", "k", "grounded", "model", "chunks": [hit],
  "cited": [chunk_id], "dropped_citations": [str]}``. Returns 503 when no model
  key is configured and 400 for an unknown or disabled mode.
- ``GET /api/health`` -> ``{"ok": true, "dense": bool, "ask": true}``
- ``hit`` = ``{"rank", "pep", "title", "section", "score", "snippet",
  "chunk_id", "start", "end"}``; ``/api/ask`` chunks also carry ``"cited": bool``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from peptalk.ask_api import add_ask_routes

HERE = Path(__file__).parent
MODES = ("bm25", "dense", "hybrid")
DEFAULT_MODE = "hybrid"
DEFAULT_K = 5
MAX_K = 20
MAX_QUERY = 1000
MODE_LABELS = {"bm25": "BM25 (keyword)", "dense": "Dense (embeddings)", "hybrid": "Hybrid (RRF)"}

Mode = Literal["bm25", "dense", "hybrid"]


class Retriever(Protocol):
    def search(self, query: str, mode: str, k: int) -> list[Any]: ...


def _field(hit: Any, name: str) -> Any:
    if isinstance(hit, dict):
        return hit.get(name)
    return getattr(hit, name, None)


def hit_dict(hit: Any, rank: int) -> dict[str, Any]:
    """Normalise one retrieved chunk (Hit object or dict) to the public shape."""
    score = _field(hit, "score")
    start = _field(hit, "start")
    end = _field(hit, "end")
    return {
        "rank": rank,
        "pep": int(_field(hit, "pep")),
        "title": _field(hit, "title") or "",
        "section": _field(hit, "section") or "",
        "score": float(score) if score is not None else 0.0,
        "snippet": _field(hit, "snippet") or "",
        "chunk_id": _field(hit, "chunk_id"),
        "start": start,
        "end": end,
    }


def _dense_enabled(retriever: Any) -> bool:
    return bool(getattr(retriever, "dense_available", False))


def create_app(retriever: Retriever, llm: Any | None = None) -> FastAPI:
    app = FastAPI(title="PEPtalk", version="0.1.0")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    dense_ok = _dense_enabled(retriever)

    def check_mode(mode: str) -> None:
        if mode not in MODES:
            raise HTTPException(status_code=400, detail=f"mode must be one of {', '.join(MODES)}")
        if mode != "bm25" and not dense_ok:
            raise HTTPException(
                status_code=400,
                detail=f"mode '{mode}' needs dense embeddings, which are disabled on this server",
            )

    def guarded_search(query: str, mode: str, k: int) -> Any:
        check_mode(mode)
        return retriever.search(query, mode, k)

    add_ask_routes(app, guarded_search, llm)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = "") -> HTMLResponse:
        modes = [
            {"value": m, "label": MODE_LABELS[m], "disabled": m != "bm25" and not dense_ok}
            for m in MODES
        ]
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "initial_question": q[:MAX_QUERY],
                "modes": modes,
                "default_mode": DEFAULT_MODE if dense_ok else "bm25",
                "default_k": DEFAULT_K,
                "dense_enabled": dense_ok,
            },
        )

    @app.get("/api/search")
    def search(
        q: str = Query(min_length=1, max_length=MAX_QUERY),
        mode: str | None = None,
        k: int = Query(default=DEFAULT_K, ge=1, le=MAX_K),
    ) -> dict[str, Any]:
        if mode is None:
            mode = DEFAULT_MODE if dense_ok else "bm25"
        check_mode(mode)
        hits = retriever.search(q, mode, k)
        return {
            "query": q,
            "mode": mode,
            "k": k,
            "results": [hit_dict(h, i + 1) for i, h in enumerate(hits)],
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "dense": dense_ok, "ask": True}

    return app


def serve_address() -> tuple[str, int]:
    """Host and port for ``python3 -m peptalk serve``; ``PORT`` overrides 8000."""
    raw = os.environ.get("PORT", "8000")
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"PORT must be an integer, got {raw!r}") from exc
    return "0.0.0.0", port


def serve(retriever: Retriever, llm: Any | None = None) -> None:
    """Run the app with uvicorn on ``serve_address()``."""
    import uvicorn

    host, port = serve_address()
    uvicorn.run(create_app(retriever, llm=llm), host=host, port=port)


def serve_index(settings: Any, dense: bool = False) -> None:
    """``python3 -m peptalk serve``: load the persisted index and serve it.

    Dense/hybrid are only offered when ``dense`` is True (``--dense``).
    """
    from peptalk.service import RetrievalService

    serve(RetrievalService.from_index(settings, dense=dense))
