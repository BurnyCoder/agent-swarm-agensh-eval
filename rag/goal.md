# PEPtalk: retrieval-augmented question answering over Python PEPs

Build a retrieval-augmented generation (RAG) system that answers questions
about Python Enhancement Proposals with cited evidence. The corpus is already in
the repository: `corpus/peps/pep-NNNN.rst` (69 PEPs, reStructuredText; source
and licenses in `corpus/SOURCE.md`). Do not modify the corpus.

## Stack

- Python 3.12 package `peptalk/` with pinned dependencies in `requirements.txt`.
- Retrieval: BM25 with `rank-bm25`, dense embeddings with `fastembed`
  (model `BAAI/bge-small-en-v1.5`, ONNX, CPU), and a hybrid that fuses the two
  rankings with reciprocal rank fusion (k = 60). Dense retrieval must be
  optional (a flag or config) so everything works without downloading a model.
- Generation: Claude Haiku 5.5 through the official `anthropic` Python SDK.
  - Base URL: `AGENSH_MODEL_BASE` when set (the team's metered model route),
    else `ANTHROPIC_BASE_URL`, else the SDK default.
  - API key from `ANTHROPIC_API_KEY` (any non-empty value works on the team route).
  - If `ANTHROPIC_WORKSPACE_ID` is set, send it as the `anthropic-workspace-id`
    header (SDK `default_headers`).
  - Model from `RAG_MODEL`, default `claude-haiku-5-5`.
- Web: FastAPI + server-rendered HTML (Jinja2) + a little vanilla JS/CSS.

## Features

1. **Ingest** (`python3 -m peptalk index`): parse each PEP's header fields
   (number, title, status, type, created) and split the body into chunks by
   section heading (long sections split further with overlap). Keep PEP number,
   title, section heading and character offsets with each chunk. Persist the
   index under `data/` (gitignored) so queries do not re-index.
2. **Search** (`python3 -m peptalk search "query" [--mode bm25|dense|hybrid] [-k 5]`
   and `GET /api/search?q=&mode=&k=`): ranked chunks with PEP number, title,
   section, score and a snippet.
3. **Ask** (`python3 -m peptalk ask "question"` and `POST /api/ask` with
   `{"question": "...", "mode": "hybrid", "k": 5}`): retrieve, then prompt Claude
   with the numbered chunks. The answer must cite its sources inline as
   `[PEP 572 §Rationale]`, and the response lists the cited chunks. If the
   retrieved evidence does not answer the question, say so instead of guessing.
   Never invent PEP numbers that are not in the retrieved context.
4. **Web UI** (`python3 -m peptalk serve`, `http://0.0.0.0:8000`, `PORT`
   overrides): question box, mode selector, the answer with clickable citations,
   and the retrieved snippets with scores.
5. **Evaluation**:
   - `eval/questions.jsonl` with at least 40 questions written from the corpus,
     each with `question`, `gold_peps` (list of PEP numbers that answer it) and
     a short `reference_answer`. Mix easy lookups ("Which PEP introduced the
     walrus operator?") and harder ones that need a specific section.
   - `python3 -m peptalk.eval` prints and writes `eval/results.md` with
     hit@1, hit@5 and MRR of the gold PEPs for bm25, dense and hybrid retrieval.
   - `python3 -m peptalk.eval --generate` also asks Claude each question and
     reports citation accuracy (cited PEPs ⊆ gold) and answer correctness
     (judged by Claude against the reference answer, using a short rubric).

## Quality

- pytest tests that run offline: chunking, BM25 ranking on a tiny fixture,
  fusion, prompt building, citation parsing, API endpoints with a fake
  retriever/embedder and a fake LLM client. Tests must not call the network
  or the model. `python3 -m pytest -q` must pass from the repository root.
- README: what it is, architecture (ingest → retrieve → generate), install,
  index, ask, serve, eval, configuration (environment variables), and the
  latest `eval/results.md` numbers.

Done means: `pip install -r requirements.txt && python3 -m peptalk index` works,
`ask` returns a cited answer, the web UI works in a browser, `python3 -m peptalk.eval`
reports retrieval metrics for all three modes, and the tests pass.
