# PEPtalk architecture

PEPtalk answers questions about the Python PEPs in `corpus/peps/` with citations.
The pipeline is **ingest → retrieve → generate**, and every stage is a plain
Python module so the CLI, the web API and the evaluation share one code path.

```
corpus/peps/*.rst
   │  corpus.load_corpus          parse headers, split sections, chunk
   ▼
data/chunks.json + manifest.json  index.build_index (python3 -m peptalk index)
   │  index.load_chunks
   ▼
retrieval.build_retriever(mode)   bm25 | dense | hybrid (RRF, k=60)
   │  Retriever.search(query, k) -> list[Hit]
   ▼
service.RetrievalService          flat ServiceHit, lazy per-mode retrievers, dense flag
   │  search(query, mode, k) -> list[ServiceHit]
   ├──► generate.ask (prompt, Claude, citations)   ask CLI, POST /api/ask
   └──► web (FastAPI + Jinja2)                     serve, GET /api/search, GET /
eval.py: retrieval metrics (hit@1, hit@5, MRR) and, with --generate, citation accuracy
         and answer correctness; writes eval/results.md
```

## Modules and interfaces

| Module | Responsibility | Public interface |
|---|---|---|
| `peptalk/config.py` | Settings from environment | `Settings`, `Settings.from_env()`, `get_settings()` |
| `peptalk/corpus.py` | Parse PEP headers, split bodies into chunks | `PEP`, `Chunk`, `chunk_pep(text, source, max_chars, overlap)`, `load_corpus(corpus_dir, max_chars, overlap)` |
| `peptalk/index.py` | Persist and reload the chunks under `data/` | `build_index(settings) -> Manifest`, `load_chunks(settings)`, `IndexMissing` |
| `peptalk/retrieval/__init__.py` | Hit type, retriever protocol, factory | `Hit(chunk, score, rank)`, `Retriever`, `build_retriever(mode, chunks, **options)`, `MODES` |
| `peptalk/retrieval/bm25.py` | Okapi BM25 over `Chunk.search_text` | `BM25Retriever(chunks)`, `tokenize(text)` |
| `peptalk/retrieval/dense.py` | fastembed vectors and the `.npz` cache | `DenseRetriever(chunks, settings, embedder=...)` |
| `peptalk/retrieval/hybrid.py` | Reciprocal rank fusion of BM25 and dense | `HybridRetriever(...)`, `rrf_fuse(rankings, k=60)` |
| `peptalk/service.py` | Adapter the CLI and web app use: flat hits, lazy retrievers | `RetrievalService(chunks, settings, dense, builder)`, `.search(query, mode, k)`, `.dense_available`, `ServiceHit`, `get_retriever(dense=True)` |
| `peptalk/generate.py` | Prompt, Claude call, citation parsing and resolution | `build_prompt`, `parse_citations`, `resolve_citations`, `ask(question, retrieve, llm, *, mode, k, model)`, `AskResult`, `LLM` protocol, `AnthropicLLM`, `llm_config_from_env`, `llm_from_env`, `LLMConfigError` |
| `peptalk/ask_cli.py` | The `ask` subcommand | `add_ask_parser(sub)`, `run_ask(...)`, `format_result(result)`, `cmd_ask(args)` |
| `peptalk/ask_api.py` | `POST /api/ask` route | `AskRequest`, `add_ask_routes(app, retrieve, llm)` |
| `peptalk/web.py` | FastAPI app, HTML page, `/api/search`, `/api/health`, `serve` | `create_app(retriever, llm=None)`, `serve(retriever, llm)`, `serve_index(settings, dense)`, `serve_address()`, `hit_dict(...)` |
| `peptalk/eval.py` | Evaluation runner and `eval/results.md` writer | `python3 -m peptalk.eval [--questions] [--out] [--modes] [--generate] [--gen-mode] [--limit]` |
| `peptalk/__main__.py` | CLI | `index`, `search [--mode] [-k]`, `ask`, `serve [--dense]` |

### Data model (`corpus.py`)

- `PEP(number, title, status, type, created, headers, source)`.
- `Chunk(chunk_id, pep, title, section, text, start, end, status, type, created)`.
  - `chunk_id` is `pep-NNNN#III`, unique within the index.
  - `start`/`end` are character offsets of `text` in the source file, so
    `corpus_file[start:end] == text` always holds.
  - `search_text` = title + section + text (what BM25 and dense embeddings index).
  - `citation` = `PEP 572 §Rationale`, the label the answer must use.

### Chunking rules

1. The header block (`Key: value`, indented continuations) is parsed up to the
   first blank line.
2. The body is split at RST headings: a title line followed by an adornment at
   least as long as the title, or the over/underlined form. Text before the
   first heading becomes section `Preamble`. Repeated headings give separate
   chunks (for example two `Rationale` sections).
3. Sections longer than `max_chunk_chars` (default 1200) are split at a blank
   line, else a newline, else a space, with `chunk_overlap_chars` (default 200)
   repeated between neighbours. Pieces start on a word boundary.

Result on the seed corpus: 69 PEPs, 2832 chunks.

### Index (`index.py`)

`data/` is gitignored and holds:

- `chunks.json`: all chunks as JSON objects.
- `manifest.json`: format version, corpus SHA-256, counts, chunking parameters, build time.

Queries never re-index. Commands that need chunks fail with `IndexMissing` and a
hint to run `python3 -m peptalk index`.

### Retrieval (`retrieval/`)

- `Retriever.search(query, k) -> list[Hit]` returns hits best first, with
  `rank` starting at 1. Only chunks with a positive score are returned.
- `build_retriever(mode, chunks, **options)` is the only place that picks an
  implementation. `dense` and `hybrid` import their modules lazily, so BM25-only
  runs never load `fastembed` or download a model.
- BM25: `rank_bm25.BM25Okapi` over lowercase alphanumeric tokens minus a small
  stopword list. Title and section are part of the indexed text.
- Dense (`retrieval/dense.py`): `fastembed.TextEmbedding` (ONNX, CPU) with
  `BAAI/bge-small-en-v1.5` (`RAG_EMBED_MODEL`). Chunk vectors are embedded from
  `search_text` once and cached in `data/embeddings-<model-slug>.npz` together with
  the chunk ids they were built from; the cache is rebuilt when those ids change or
  the file is unreadable. Queries use `query_embed` (bge query prefix). Vectors are
  L2-normalised, so the score is cosine similarity. The model is downloaded on first
  dense use only. The embedder is injectable, so tests use a fake.
- Hybrid (`retrieval/hybrid.py`): reciprocal rank fusion of the BM25 and dense
  rankings, `score = Σ 1/(60 + rank)` with 1-based ranks (`RRF_K = 60`). Each side
  contributes its top `max(50, k)` chunks; chunks found by only one side still count.
  The fused score is returned as `Hit.score`, so it is not comparable with the raw
  BM25 or cosine scores.

### Generation (`generate.py`, `ask_cli.py`, `ask_api.py`)

- Flow: `retrieve(question, mode, k)` → `build_prompt` → one model call →
  `resolve_citations` → `AskResult`. The retriever is injected, so the core is
  testable without an index, network or model. `search` is `RetrievalService.search`,
  so `ask` runs in any mode the service allows.
- Numbered chunks are placed in the prompt as `[1] PEP 572 §Rationale — title`.
- The model must cite inline as `[PEP 572 §Rationale]`; several sources may share
  one bracket, separated by `;`. `parse_citations` reads the `[PEP N §Section]`
  spans. `resolve_citations` keeps citations whose PEP is among the retrieved
  passages. Any other citation is removed from the answer text and its raw form is
  returned in `dropped_citations`. The remaining citations map to `cited` chunk ids.
- If the evidence does not answer the question, the answer says so and `grounded`
  is false. The system prompt forbids outside knowledge and guessing.
- `AnthropicLLM` wraps the `anthropic` SDK `messages.create`. `llm_config_from_env()` reads:
  - base URL: `AGENSH_MODEL_BASE`, else `ANTHROPIC_BASE_URL`, else the SDK default;
  - key: `ANTHROPIC_API_KEY`. If it is empty and `AGENSH_MODEL_BASE` is set, the
    placeholder `agensh-proxy` is used (see the team-route section below);
  - `ANTHROPIC_WORKSPACE_ID`, sent as the `anthropic-workspace-id` header when set;
  - model: `RAG_MODEL` (default `claude-haiku-5-5`).
- With neither `ANTHROPIC_API_KEY` nor `AGENSH_MODEL_BASE` set, `llm_config_from_env`
  raises `LLMConfigError`. The web API maps it to HTTP 503, and the CLI exits with
  status 2.
- `POST /api/ask` returns `question`, `answer`, `mode`, `k`, `grounded`, `model`,
  `chunks` (ranked hits with a `cited` flag), `cited` (chunk ids) and
  `dropped_citations`. `ask` prints the answer and a numbered Sources list.

### Key handling on the team route

The team model route (`AGENSH_MODEL_BASE`) forwards requests unchanged and adds
the real credential itself. The project does not hold that credential. The SDK
still requires an `api_key` argument and refuses requests without an auth header.
So when `AGENSH_MODEL_BASE` is set and `ANTHROPIC_API_KEY` is empty,
`generate.py` sends the fixed, non-secret placeholder `PROXY_PLACEHOLDER_KEY`
(`agensh-proxy`), and the proxy replaces it. Setting `ANTHROPIC_API_KEY` to any
non-empty value also works and is used as given. No credential is committed, and
nothing in the repository reads the key except the SDK constructor. Outside the
team route (no `AGENSH_MODEL_BASE`), set a real key.

### Web and service (`service.py`, `web.py`, `ask_api.py`)

- `RetrievalService` owns the chunks and one lazily built retriever per mode,
  behind a lock. `dense=False` (the default for `serve`) refuses dense and hybrid
  with `ValueError` and never imports `peptalk.retrieval`'s embedding code, so
  BM25-only servers do not load fastembed.
- `ServiceHit` flattens a core `Hit` into the fields the UI and API expose.
  `snippet` is a whitespace-collapsed prefix of about 240 characters, cut at a
  word boundary. `text` is kept for the answer prompt but is not serialised.
- `web.create_app(retriever, llm=None)` mounts `/`, `/api/search`, `/api/health`
  and `/api/ask`. `/api/ask` is always mounted, so a missing key returns 503
  rather than 404.
- `ask_api.add_ask_routes` validates `AskRequest` (`question`, `mode`, `k`) and
  maps `ValueError` to 400 and `LLMConfigError` to 503.
- `web.serve_index(settings, dense)` loads the index and starts uvicorn.
  `serve_address()` binds `0.0.0.0`, and `PORT` (default 8000) must be an integer.
- The page is server-rendered Jinja2 with a small `static/app.js` that calls
  `/api/ask` and renders the citations as links to the cited snippets.

## Decisions

- **Chunk by section, not by fixed window.** PEP sections (Abstract, Rationale,
  Specification) are what users cite, and the citation label needs a section name.
- **Keep offsets on every chunk.** Lets reviewers and the UI show exact source text.
- **BM25 over title + section + text.** Titles carry strong signal for queries such
  as "Which PEP introduced the walrus operator?".
- **Dense retrieval is optional and lazy.** Tests and the default path run offline.
- **Index is a JSON snapshot in `data/`.** The corpus is small (2832 chunks), so a
  vector database is not needed.
- **Filter zero scores.** BM25 scores of zero mean no shared term, so they are not hits.
- **Tests are offline.** No test calls the network or a model; fakes replace the
  retriever, embedder and LLM client.
