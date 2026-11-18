"""Offline tests for POST /api/ask with a fake retriever and a fake LLM."""

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from peptalk.ask_api import add_ask_routes


@dataclass
class Hit:
    pep: int
    title: str
    section: str | None
    score: float
    snippet: str
    chunk_id: str
    start: int
    end: int
    text: str


HITS = [
    Hit(572, "Assignment Expressions", "Rationale", 0.91, "bind names...", "572-rationale-0", 100, 220, "Assignment expressions let you bind names."),
    Hit(8, "Style Guide", None, 0.55, "four spaces...", "8-0", 0, 90, "Use four spaces per indentation level."),
]


class FakeLLM:
    model = "fake-model"

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        return self.reply


def make_client(reply, retrieve=None):
    calls = []

    def default_retrieve(question, mode, k):
        calls.append((question, mode, k))
        return HITS[:k]

    app = FastAPI()
    add_ask_routes(app, retrieve or default_retrieve, FakeLLM(reply))
    return TestClient(app), calls


def test_ask_returns_answer_chunks_and_cited_ids():
    client, calls = make_client("Walrus binds names [PEP 572 §Rationale].")
    resp = client.post("/api/ask", json={"question": "What does := do?", "mode": "bm25", "k": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert calls == [("What does := do?", "bm25", 2)]
    assert body["answer"] == "Walrus binds names [PEP 572 §Rationale]."
    assert body["mode"] == "bm25"
    assert body["k"] == 2
    assert body["grounded"] is True
    assert body["model"] == "fake-model"
    assert body["cited"] == ["572-rationale-0"]
    assert body["dropped_citations"] == []
    assert [c["rank"] for c in body["chunks"]] == [1, 2]
    assert body["chunks"][0]["cited"] is True
    assert body["chunks"][1]["cited"] is False
    assert set(body["chunks"][0]) >= {"rank", "pep", "title", "section", "score", "snippet", "chunk_id", "start", "end"}
    assert "text" not in body["chunks"][0]


def test_ask_drops_invented_pep_numbers():
    client, _ = make_client("Yes [PEP 572 §Rationale] and [PEP 4242].")
    body = client.post("/api/ask", json={"question": "q"}).json()
    assert "4242" not in body["answer"]
    assert body["dropped_citations"] == ["[PEP 4242]"]
    assert body["cited"] == ["572-rationale-0"]


def test_ask_defaults_to_hybrid_and_k_5():
    client, calls = make_client("No evidence.")
    body = client.post("/api/ask", json={"question": "q"}).json()
    assert calls == [("q", "hybrid", 5)]
    assert body["mode"] == "hybrid"
    assert body["k"] == 5
    assert body["grounded"] is False
    assert body["cited"] == []


def test_ask_validates_request():
    client, _ = make_client("x")
    assert client.post("/api/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/ask", json={"question": "q", "mode": "sparse"}).status_code == 422
    assert client.post("/api/ask", json={"question": "q", "k": 0}).status_code == 422
    assert client.post("/api/ask", json={"question": "q", "k": 21}).status_code == 422


def test_ask_maps_value_error_from_retriever_to_400():
    def retrieve(question, mode, k):
        raise ValueError("dense retrieval is disabled")

    client, _ = make_client("x", retrieve=retrieve)
    resp = client.post("/api/ask", json={"question": "q", "mode": "dense"})
    assert resp.status_code == 400
    assert "dense" in resp.json()["detail"]


def test_ask_without_key_and_without_injected_llm_is_503(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGENSH_MODEL_BASE", raising=False)
    app = FastAPI()
    add_ask_routes(app, lambda q, m, k: HITS[:k])
    resp = TestClient(app).post("/api/ask", json={"question": "q"})
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]
