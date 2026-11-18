# PEPtalk

PEPtalk answers questions about Python Enhancement Proposals (PEPs) with cited
evidence. It retrieves the most relevant sections of the 69 PEPs in
`corpus/peps/` and asks Claude Haiku 5.5 to answer from those sections only,
citing them inline as `[PEP 572 §Rationale]`. If the retrieved text does not
answer the question, it says so.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and the module interfaces.

## Architecture

```
ingest  →  retrieve  →  generate
```

1. **Ingest**: parse each PEP's header and split its body by section heading,
   with overlap for long sections. Chunks keep the PEP number, title, section and
   character offsets. The index is saved under `data/`.
2. **Retrieve**: BM25 (`rank-bm25`), dense embeddings (`fastembed`,
   `BAAI/bge-small-en-v1.5`), or a hybrid that fuses both with reciprocal rank
   fusion (k = 60).
3. **Generate**: numbered chunks go into a prompt for Claude, which answers with
   inline citations. The response lists the cited chunks.

## Install

Python 3.12.

```sh
python3 -m pip install -r requirements.txt
```

## Index

```sh
python3 -m peptalk index
```

This writes `data/chunks.json` and `data/manifest.json` (`data/` is gitignored).
Queries read the saved index and do not re-index.

## Search

```sh
python3 -m peptalk search "walrus operator" --mode bm25 -k 5
```

`--mode` is `bm25`, `dense` or `hybrid`. Dense and hybrid need the `fastembed`
model (`RAG_EMBED_MODEL`), which is downloaded on first use. BM25 works offline.
The first dense or hybrid query embeds all 2832 chunks on the CPU, which takes about
15–20 minutes, and caches the vectors in `data/embeddings-<model>.npz`; later queries
reuse them. Plan for that wait on a fresh checkout. Hybrid
fuses the two rankings with reciprocal rank fusion (k = 60).

Web: `GET /api/search?q=walrus&mode=bm25&k=5`.

## Ask

```sh
export ANTHROPIC_API_KEY=...   # optional on the team route (AGENSH_MODEL_BASE set): a placeholder is sent
python3 -m peptalk ask "Which PEP introduced the walrus operator?" --mode hybrid -k 5
```

Web: `POST /api/ask` with `{"question": "...", "mode": "hybrid", "k": 5}`.

## Serve

```sh
python3 -m peptalk serve            # BM25 only, http://0.0.0.0:8000
python3 -m peptalk serve --dense    # also dense and hybrid retrieval
PORT=9000 python3 -m peptalk serve
```

The server binds `0.0.0.0`; `PORT` (default `8000`) changes the port and must be
an integer; a non-integer value exits with status 2 and an error message, before the
index is loaded. Without `--dense` the server loads no embedding code, the mode
selector offers only BM25, and dense or hybrid requests get HTTP 400. With
`--dense` the embedding model loads on the first dense or hybrid request.

The page at `/` has a question box, a mode selector, the answer with clickable
citations, and the retrieved snippets with their scores. Questions can be
prefilled with `/?q=...`.

### Web API

| Method and path | Request | Response |
|---|---|---|
| `GET /api/search` | `q` (1–1000 chars, required), `mode` (`bm25`, `dense`, `hybrid`; default `hybrid` when `--dense`, else `bm25`), `k` (1–20, default 5) | `{"query", "mode", "k", "results": [hit]}` |
| `POST /api/ask` | JSON `{"question", "mode" (default `hybrid`), "k" (default 5)}` | `{"question", "answer", "mode", "k", "grounded", "model", "chunks": [hit], "cited": [chunk_id], "dropped_citations": [...]}` |
| `GET /api/health` | none | `{"ok": true, "dense": bool, "ask": true}` |

A `hit` is `{"rank", "pep", "title", "section", "score", "snippet", "chunk_id",
"start", "end", "cited"}` (`cited` appears on `ask` chunks only). `start` and `end`
are character offsets in the PEP's `.rst` file. `score` is comparable only within
one mode. Invalid parameters return 422; an unknown mode or a disabled dense mode
returns 400.

`POST /api/ask` returns 503 when the model route is not configured (neither
`ANTHROPIC_API_KEY` nor `AGENSH_MODEL_BASE` is set). A citation to a PEP that is not among the retrieved chunks is
removed from the answer text and its raw form is listed in `dropped_citations`.

```sh
curl -s 'http://localhost:8000/api/search?q=walrus&mode=bm25&k=2'
curl -s -X POST http://localhost:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "Which PEP introduced the walrus operator?", "mode": "bm25"}'
```

## Evaluate

```sh
python3 -m peptalk.eval             # retrieval metrics: hit@1, hit@5, MRR
python3 -m peptalk.eval --generate  # also citation accuracy and answer correctness
```

Questions are in `eval/questions.jsonl`. Results are printed and written to
`eval/results.md`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AGENSH_MODEL_BASE` | unset | Base URL for model calls (team route). Takes precedence over `ANTHROPIC_BASE_URL`. |
| `ANTHROPIC_BASE_URL` | SDK default | Base URL when `AGENSH_MODEL_BASE` is unset. |
| `ANTHROPIC_API_KEY` | unset | API key; required for `ask` and `--generate` unless `AGENSH_MODEL_BASE` is set. On the team route an empty key is replaced by a non-secret placeholder, and the proxy adds the credential. |
| `ANTHROPIC_WORKSPACE_ID` | unset | Sent as the `anthropic-workspace-id` header when set. |
| `RAG_MODEL` | `claude-haiku-5-5` | Model used for answers and grading. |
| `RAG_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | fastembed model for dense retrieval. |
| `PORT` | `8000` | Port for `serve`. |

## Tests

```sh
python3 -m pytest -q
```

The tests run offline. They use a tiny fixture for BM25 and fakes for the
embedder and the LLM client; no test calls the network or a model.

## Results

Latest retrieval numbers (from `python3 -m peptalk.eval`, 83 answerable questions of
86; full tables and misses are in [eval/results.md](eval/results.md)):

| mode | hit@1 | hit@5 | MRR |
|---|---:|---:|---:|
| bm25 | 0.867 | 0.988 | 0.918 |
| dense | 0.904 | 0.964 | 0.938 |
| hybrid | 0.940 | 1.000 | 0.963 |

Generation numbers (from `python3 -m peptalk.eval --generate`, hybrid mode, k = 5, all
86 questions; judged by Claude against each reference answer):

| metric | value |
|---|---:|
| citation accuracy (cited PEPs within the gold PEPs) | 0.849 |
| answer correct | 0.814 |
| answer partial | 0.093 |
| judge replies unparsed | 5 |

Citation accuracy is strict: a citation to a PEP outside the gold list counts as wrong,
even when the answer is correct. Per-question verdicts are in `eval/results.md`.

## Corpus

`corpus/peps/` holds 69 PEPs in reStructuredText. Source and licences are in
[corpus/SOURCE.md](corpus/SOURCE.md). The corpus is not modified by this project.
