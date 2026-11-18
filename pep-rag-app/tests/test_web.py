"""Offline tests for the web app: fake retriever, no network, no model."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from peptalk import web


@dataclass
class FakeHit:
    pep: int
    title: str
    section: str
    score: float
    snippet: str
    chunk_id: int
    start: int = 0
    end: int = 10
    text: str = "full chunk text"


class FakeRetriever:
    def __init__(self, dense: bool = False):
        self.dense_available = dense
        self.calls: list[tuple[str, str, int]] = []

    def search(self, query, mode, k):
        self.calls.append((query, mode, k))
        hits = [
            FakeHit(572, "Assignment Expressions", "Rationale", 0.9, "The walrus operator", 7, 100, 180),
            FakeHit(498, "Adding a formatted string literal", "Abstract", 0.5, "f-strings", 3, 0, 40),
        ]
        return hits[:k]


@pytest.fixture
def client():
    return TestClient(web.create_app(FakeRetriever()))


@pytest.fixture
def dense_client():
    return TestClient(web.create_app(FakeRetriever(dense=True)))


def test_search_returns_ranked_hits_with_public_shape(client):
    r = client.get("/api/search", params={"q": "walrus", "mode": "bm25", "k": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "walrus"
    assert body["mode"] == "bm25"
    assert body["k"] == 2
    first = body["results"][0]
    assert first == {
        "rank": 1,
        "pep": 572,
        "title": "Assignment Expressions",
        "section": "Rationale",
        "score": 0.9,
        "snippet": "The walrus operator",
        "chunk_id": 7,
        "start": 100,
        "end": 180,
    }
    assert [h["rank"] for h in body["results"]] == [1, 2]


def test_search_defaults_to_hybrid_and_k5(dense_client):
    r = dense_client.get("/api/search", params={"q": "walrus"})
    assert r.status_code == 200
    assert r.json()["mode"] == "hybrid"
    assert r.json()["k"] == 5


def test_search_without_mode_defaults_to_bm25_when_dense_disabled(client):
    r = client.get("/api/search", params={"q": "x"})
    assert r.status_code == 200
    assert r.json()["mode"] == "bm25"


def test_search_passes_arguments_to_retriever():
    fake = FakeRetriever()
    client = TestClient(web.create_app(fake))
    client.get("/api/search", params={"q": "decorators", "mode": "bm25", "k": 1})
    assert fake.calls == [("decorators", "bm25", 1)]


def test_search_rejects_unknown_mode(client):
    r = client.get("/api/search", params={"q": "x", "mode": "fuzzy"})
    assert r.status_code == 400
    assert "mode must be one of" in r.json()["detail"]


def test_search_rejects_dense_when_disabled(client):
    r = client.get("/api/search", params={"q": "x", "mode": "dense"})
    assert r.status_code == 400
    assert "dense embeddings" in r.json()["detail"]


def test_search_allows_dense_when_enabled(dense_client):
    r = dense_client.get("/api/search", params={"q": "x", "mode": "dense"})
    assert r.status_code == 200


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "x", "k": 0}, {"q": "x", "k": 21}])
def test_search_validates_parameters(client, params):
    assert client.get("/api/search", params=params).status_code == 422


def test_health_reports_capabilities(client):
    assert client.get("/api/health").json() == {"ok": True, "dense": False, "ask": True}


def test_health_reports_dense_when_enabled(dense_client):
    assert dense_client.get("/api/health").json()["dense"] is True


def test_index_renders_form_with_bm25_only(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'id="ask-form"' in html
    assert 'id="question"' in html
    assert '/static/app.js"' in html
    assert '/static/style.css"' in html
    assert 'value="bm25" selected' in html
    assert 'value="hybrid" disabled' in html


def test_index_prefills_question_from_query_string(client):
    r = client.get("/", params={"q": "What is <script>?"})
    assert r.status_code == 200
    assert "What is &lt;script&gt;?" in r.text
    assert "<script>?" not in r.text


def test_index_defaults_to_hybrid_when_dense_enabled(dense_client):
    html = dense_client.get("/").text
    assert 'value="hybrid" selected' in html


def test_static_assets_are_served(client):
    js = client.get("/static/app.js")
    css = client.get("/static/style.css")
    assert js.status_code == 200 and "fetch(" in js.text
    assert css.status_code == 200 and "citation" in css.text


class FakeLLM:
    model = "fake-model"

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        return self.reply


def test_ask_route_is_always_mounted(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGENSH_MODEL_BASE", raising=False)
    client = TestClient(web.create_app(FakeRetriever()))
    r = client.post("/api/ask", json={"question": "q"})
    assert r.status_code == 503


def test_ask_returns_cited_answer_with_injected_llm():
    retriever = FakeRetriever()
    llm = FakeLLM("The walrus operator arrived in PEP 572 [PEP 572 §Rationale].")
    client = TestClient(web.create_app(retriever, llm=llm))
    r = client.post("/api/ask", json={"question": "Which PEP added the walrus operator?", "mode": "bm25", "k": 2})
    assert r.status_code == 200
    body = r.json()
    assert "[PEP 572 §Rationale]" in body["answer"]
    assert [c["pep"] for c in body["chunks"]] == [572, 498]
    assert 7 in body["cited"]
    assert len(llm.prompts) == 1
    assert retriever.calls == [("Which PEP added the walrus operator?", "bm25", 2)]


def test_ask_rejects_dense_when_disabled():
    client = TestClient(web.create_app(FakeRetriever(), llm=FakeLLM("x")))
    r = client.post("/api/ask", json={"question": "q", "mode": "dense", "k": 1})
    assert r.status_code == 400


def test_serve_address_defaults_and_port_override(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert web.serve_address() == ("0.0.0.0", 8000)
    monkeypatch.setenv("PORT", "9123")
    assert web.serve_address() == ("0.0.0.0", 9123)
    monkeypatch.setenv("PORT", "nope")
    with pytest.raises(ValueError):
        web.serve_address()


def test_hit_dict_accepts_plain_dicts():
    out = web.hit_dict({"pep": "18", "title": "T", "score": 1, "chunk_id": 2}, rank=3)
    assert out["pep"] == 18
    assert out["rank"] == 3
    assert out["score"] == 1.0
    assert out["section"] == ""
    assert out["snippet"] == ""
