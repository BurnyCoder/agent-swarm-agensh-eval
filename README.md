# Agensh × Claude Code (Claude Haiku 5.5): two team runs

A hands-on test of [microsoft/Agensh](https://github.com/microsoft/Agensh), a decentralized multi-agent harness
([paper](https://arxiv.org/abs/2609.26781)). In Agensh, a team of coding agents coordinates through
[Gitea](https://about.gitea.com/) (repository, issues, pull requests, CI), [Mattermost](https://mattermost.com/)
(chat) and a shared board, with no central orchestrator. Here every worker is
[Claude Code](https://github.com/anthropics/claude-code) 2.1.327 running **Claude Haiku 5.5** (`claude-haiku-5-5`),
using Agensh's built-in Claude Code adapter (`--agent-harness claude`).

Two projects, run in parallel on one WSL2 machine, each with 4 workers and a 1.5-hour budget:

| Run | Task | Seed | Coordination |
|---|---|---|---|
| `plane-tickets` | Build **SkyBook**, a flight booking web app: search, seats, passengers, mock payment, booking lookup and cancel ([goal](plane-tickets/goal.md)) | empty repository | planning phase on (worker 1 plans first) |
| `pep-rag` | Build **PEPtalk**, retrieval-augmented question answering over 69 Python PEPs: BM25, dense and hybrid retrieval, Claude answers with citations, web UI, retrieval eval ([goal](rag/goal.md)) | the PEP corpus ([seed script](rag/make_seed.py)) | self-organized (no planning phase) |

**Status:** both runs are in progress. Results (delivered code, CI, tokens and cost, RAG eval numbers,
screenshots) will be added to `reports/` when the runs finish.

## Findings so far

- **Install works on WSL2 Ubuntu 24.04** with Docker Engine, systemd and cgroup v2. Agensh's scripts call `sudo docker` / `sudo -n`.
  Instead of adding a passwordless-sudo rule, everything runs as root (`wsl -u root`). 81/81 unit tests pass.
  Four tests first failed because `tests/smoke/fake_upstream.py` treats port `0` as unset and falls back to 8080,
  which another container was using here; `PORT=0` works around it.
- **Workspace-scoped API keys need a relay.** This API key is not tied to a workspace, so the Anthropic API requires an
  `anthropic-workspace-id` header on every request. Agensh's egress proxy (`scripts/strip_metadata_proxy.py`) can only
  inject `Authorization: Bearer`. [`relay/anthropic_relay.py`](relay/anthropic_relay.py) is a ~100-line stdlib relay on the
  Docker host gateway (the "host-loopback" pattern in Agensh's REPRODUCE.md) that adds the header and passes streams
  through. Agensh itself is unmodified.
- **Planning gate vs. Haiku (plane-tickets).** With `[coordination] planning = true`, worker 1 is told to open a PR titled
  `[plan] …`, and the other workers wake only when such a PR merges (`agensh/team_run.py`) or after 45 minutes.
  Haiku created an issue breakdown and merged feature PRs, but never used the `[plan]` title. Workers 2–4 therefore slept
  until the timeout: 2,809 s of the 5,400 s budget went to "waking", leaving the full team ~43 minutes.
  The RAG run uses `planning = false`, and all four workers woke within 101 s.
- **CI command pitfall (mine).** Gitea Actions runs `bash -e`, so `pytest; rc=$?; …` exits on pytest's
  "no tests collected" code 5 before the check runs. The empty first `main` failed CI, and Agensh's main-health gate allowed only
  `[fix-main]` PRs until worker 1 added a smoke test itself. The RAG seed repository ships a passing corpus test to avoid this.
- **Self-organization (RAG run).** Without a planning phase, the four Haiku workers split the work by issue within minutes:
  ingest + BM25, dense + hybrid retrieval, generation with citations, web UI/API, and the eval set.
  After 15 minutes they had 6 PRs merged through the CI gate.

## How it is set up

```
Claude Code workers (4 per team, Docker)  ──►  Agensh egress proxy (injects Bearer key, meters usage)
          │  Gitea / Mattermost / board                    │
          ▼                                                ▼
   team repo, issues, PRs, CI              relay/anthropic_relay.py on 172.17.0.1:8788
                                            (Bearer → x-api-key, + anthropic-workspace-id)
                                                           │
                                                           ▼
                                                 api.anthropic.com (claude-haiku-5-5)
```

| File | Purpose |
|---|---|
| [`haiku.json`](haiku.json) | Agensh harness override: Claude Code 2.1.327, `claude-haiku-5-5`, effort `high`, 100K context window, compaction at 80% (keeps prompts under Haiku 5.5's 100K-token price tier) |
| [`plane-tickets/project.toml`](plane-tickets/project.toml), [`goal.md`](plane-tickets/goal.md) | Agensh `software-project` file and goal for SkyBook |
| [`rag/project.toml`](rag/project.toml), [`goal.md`](rag/goal.md), [`make_seed.py`](rag/make_seed.py) | PEPtalk project, goal, and the script that builds the seed repo: PEPs from [python/peps](https://github.com/python/peps) at a pinned commit, kept only if their Copyright section is public domain or CC0 |
| [`relay/anthropic_relay.py`](relay/anthropic_relay.py) | Header relay for workspace-scoped API keys, plus a **hard dollar cap** across all teams (tests: [`relay/test_anthropic_relay.py`](relay/test_anthropic_relay.py)) |

**Spend cap.** Agensh's `[budget] max_model_tokens` counts only uncached input + output tokens (`scripts/usage_meter.py`), not
cache reads/writes or dollars. Here, most traffic is cache reads, so that cap would not bound cost. Every model request passes through the relay, so it enforces the cap instead:
- It forwards only `claude-haiku-5-5`.
- Before forwarding, it reserves an upper bound of the request's cost: body bytes / 2 tokens, all priced as 1-hour cache writes, plus `max_tokens` of output.
- It refuses with 403 if spent + reserved would exceed `RELAY_SPEND_CAP_USD`. After the response, it settles the actual cost from the response's `usage`.
- Haiku 5.5 prices: $0.10 / $0.50 per MTok in / out (≤100K-token prompts; $0.50 / $2.50 above), cache reads 0.1×, 5-minute writes 1.25×, 1-hour writes 2×.
- For these runs the cap is **$9.00**. The ledger was seeded with the $2.83 spent before the cap existed, priced the same conservative way plus a $0.10 margin.

## Reproduce

Prerequisites: Linux (or WSL2) with Docker Engine + Compose v2, systemd, cgroup v2, Python 3.11+, git, curl, jq, envsubst,
flock, and root or passwordless sudo. Commands below run as root in WSL.

```bash
# 1. Agensh at the tested commit, plus its host units (REPRODUCE.md "Prerequisites")
git clone https://github.com/microsoft/Agensh.git && cd Agensh && git checkout 2dae4e14a653
cp docker/agensh.slice /etc/systemd/system/
sed "s|/opt/agensh|$PWD|g" docker/agensh_reboot_guard.service > /etc/systemd/system/agensh-reboot-guard.service
systemctl daemon-reload && systemctl enable agensh-reboot-guard.service
PORT=0 python3 -m unittest discover -s tests/unit

# 2. Relay (only for keys that need anthropic-workspace-id) and credentials
systemd-run --unit=agensh-anthropic-relay --setenv=ANTHROPIC_WORKSPACE_ID=wrkspc_... \
  python3 -u /path/to/relay/anthropic_relay.py
printf 'LLM_UPSTREAM=http://172.17.0.1:8788\nLLM_UPSTREAM_TOKEN=%s\n' "$ANTHROPIC_API_KEY" > ~/.agensh/upstream.env
chmod 600 ~/.agensh/upstream.env      # without the relay: LLM_UPSTREAM=https://api.anthropic.com

# 3. Build, profile, run (repeat for rag/project.toml after python3 -I rag/make_seed.py <seed-dir>)
H="--agent-harness claude --harness-version 2.1.327 --harness-config /path/to/haiku.json"
python3 -m agensh.project build   /path/to/plane-tickets/project.toml $H
WORKER_MEMORY=4g python3 -m agensh.project profile /path/to/plane-tickets/project.toml $H
python3 -m agensh.project run     /path/to/plane-tickets/project.toml
```

To watch a run: Gitea is at `http://localhost:<EDGE_HTTP_PORT>` and Mattermost at `http://localhost:<EDGE_MM_PORT>`
(ports are in the generated `.env.software-project-<name>`). For live token use per worker, run
`python3 scripts/team_status.py software-project-<name>`.

## To do

- [ ] Collect results when the runs finish: `delivery.json`, `acceptance.json`, PR/issue timeline, tokens and cost per team
- [ ] Check the delivered apps: run their tests, click through SkyBook, run the PEPtalk retrieval eval and ask questions
- [ ] Write `reports/run-report.md` with screenshots
- [ ] Report the planning-gate title brittleness and the `fake_upstream` port-0 quirk upstream to microsoft/Agensh
