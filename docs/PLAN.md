# Coverly v0 — Build Plan (multi-agent)

Source of truth for scope: `docs/prd.md` §4.1. Conventions: `docs/CLAUDE.md`. This file covers only **who builds what, in which order, and the quality bar**.

---

## 1. Principles

1. **Contracts first, then parallel work.** One agent writes the shared contracts (schemas, DB, config, deps). Every later agent codes against them and owns a disjoint set of files, so parallel agents never edit the same file.
2. **Right model for the job.** Opus for correctness-critical or concurrency logic. Sonnet for standard feature code. Haiku for mechanical work.
3. **Minimal code.** No abstraction with one implementation, no unused config, no v1 code. Stdlib or an already-installed dependency before a new one.
4. **Every agent proves its work.** `ruff check`, `mypy --strict` and `pytest -q` on its own files (web: `npm run lint && npm run build`) before reporting done. Reports quote the command output.
5. **Independent review.** Every wave ends with a review agent that did not write the code.

## 2. Dependencies (fixed in Wave 0; no agent adds more without asking)

| Package | Reason |
|---|---|
| `fastapi`, `uvicorn[standard]` | API server |
| `httpx` | async HTTP for YouTube Data API and Groq (OpenAI-compatible endpoint); no SDKs needed |
| `pydantic-settings` | typed `.env` loading |
| `youtube-transcript-api` (pinned 1.x) | transcripts |
| dev: `pytest`, `ruff`, `mypy` | tests, lint, type checks. Async tests use the `anyio` plugin that ships with FastAPI; HTTP mocks use `httpx.MockTransport` (no extra deps) |
| web: `next`, `react`, `tailwindcss` | UI only. No UI kit, no data-fetching library |

Not used on purpose: `google-api-python-client` (heavy; 2 endpoints only), `groq` SDK (we own retries/rate limit anyway), ORM (9 tables, raw `sqlite3` is enough), `respx`, `pytest-asyncio`.

## 3. Performance and reliability rules

| Area | Rule |
|---|---|
| HTTP | One shared `httpx.AsyncClient` per process (connection pooling). Explicit timeouts on every call (connect 5 s, read 20 s). |
| Retries | Only on 429 / 5xx / timeouts. Exponential backoff with jitter, max 3 tries. Respect `Retry-After`. Never retry 4xx validation errors. |
| Concurrency | `asyncio.Semaphore` per external service: transcripts 8, YouTube metadata 4, Groq bounded by the token bucket in `llm.py`. The transcript library is sync: call it through `asyncio.to_thread`. |
| Stages | Run work inside each stage concurrently (`asyncio.gather` over videos/batches). Progress is written after each unit so the UI shows movement within 10 s. |
| SQLite | WAL mode, `foreign_keys=ON`, indexes on every FK and `search_cache(cached_at)`. Writes only from the job runner; parameterized queries only. |
| Caching | Search 7 days by `(query, language)`; metadata and transcripts permanent; tags per plan. A cached rerun of the same plan costs 0 quota units. |
| Algorithm | `cover.py` uses Python `set`s; plain greedy is O(picks × segments), about 40 × 2,000, under 50 ms. No heap/lazy greedy unless profiling says so. |
| Failure | No stage failure fails the job (CLAUDE.md §5.5). The job fails only on a bug, with the traceback logged and `status=failed` + message saved. |
| Limits | Input ≤ 20,000 chars and ≤ 40 topics, validated by Pydantic at the API boundary. CORS limited to `CORS_ORIGIN`. Keys never leave the server. |
| Frontend | Server components by default. Polling every 2 s only on the run page; it stops on `done`/`failed` and on unmount. |
| Logging | One structured line per event: `job_id`, `stage`, duration ms, quota units, LLM tokens. |

**Latency target** (PRD §7): a 20-topic cold run finishes in under 5 min; a cached rerun in under 30 s. Measured in Wave 4 and written to the README.

## 4. Waves and agents

Model key: **O** = `opus`, **S** = `sonnet`, **H** = `haiku`.

### Wave 0 — Contracts (sequential, blocks everything)

| Agent | Model | Owns | Done when |
|---|---|---|---|
| A0 Contracts | O | `api/pyproject.toml`, `api/requirements*.txt`, `api/config.py`, `api/schemas.py` (all API + LLM + domain models), `api/db.py` (PRD §11 schema + indexes + WAL), `api/main.py`, `api/routes/health.py`, `api/.env.example`, `.gitignore`, ruff/mypy config | `uvicorn` starts, `GET /health` → `{ok: true}`, mypy strict clean, a test creates the schema in a temp DB |

### Wave 1 — Independent modules (4 agents in parallel)

| Agent | Model | Owns | Done when |
|---|---|---|---|
| A1 Optimizer | O | `pipeline/score.py`, `pipeline/cover.py`, `tests/test_score.py`, `tests/test_cover.py` | Pure functions; tests cover full coverage, impossible topic, budget skip, `over budget` reason, prune, merge, order, beginner +20%, switch penalty, empty input |
| A2 LLM + parse | O | `llm.py`, `pipeline/parse.py`, `tests/test_llm.py`, `tests/test_parse.py` | Token bucket, 2-key rotation, backoff, JSON-mode, Pydantic validate + 1 retry; `topic_id` stable (slug + hash); 40-cap; all tests use `MockTransport` |
| A3 YouTube | S | `pipeline/search.py`, `pipeline/transcripts.py`, `pipeline/segment.py`, tests + `tests/fixtures/*.json` | 1 search per topic, `videos.list` by 50, quota counting, chapter parsing, 240 s windows cut at sentence gaps, transcript fallback chain, ISO-8601 duration parsing |
| A4 Web | S | `web/**` (scaffold, `lib/api.ts`, all 4 pages, components) | Types mirror `schemas.py`; loading/empty/error state on every page; `npm run lint && npm run build` clean |

### Wave 2 — Integration (2 agents in parallel)

| Agent | Model | Owns | Done when |
|---|---|---|---|
| A5 Tagging | O | `pipeline/tag.py`, `tests/test_tag.py` | Video cap, keyword pre-filter, truncation, batching, closed vocabulary (unknown ids dropped), bad batch skipped and logged |
| A6 Jobs + routes | S | `jobs.py`, `routes/plans.py`, `routes/jobs.py`, `routes/results.py`, `tests/test_api.py` | Full pipeline wired with progress + quota + token logging; all PRD §12 endpoints; end-to-end API test with every external call mocked |

### Wave 3 — Review (parallel, read-only)

| Agent | Model | Task |
|---|---|---|
| R1 Code review | O (`caveman:cavecrew-reviewer`) | Correctness bugs, race conditions, missed error paths across the full diff |
| R2 Security review | O (`/security-review`) | Secrets, CORS, injection, input limits |
| R3 Simplify | S (`/simplify`) | Dead code, duplication, over-abstraction |

Findings are fixed by the owning agent's model tier (or by the main session if the fix is 1–2 lines; small mechanical fixes go to `caveman:cavecrew-builder` on H).

### Wave 4 — Real run (main session + you)

Needs your keys in `api/.env`. Run your Backend syllabus through the UI, record coverage N/M, total minutes, run time, quota units and tokens. Fix the worst issue. H agent updates README numbers and `docs/PROGRESS.md`.

## 5. Quality gate (every wave, before the next starts)

```bash
cd api && ruff check . && ruff format --check . && mypy --strict . && pytest -q
cd web && npm run lint && npm run build
```

All green, review findings at severity high or above fixed, then one conventional commit per wave.

## 6. Rules every agent prompt includes

- Read `docs/CLAUDE.md`, this file, and only the PRD section named in the task.
- Touch only the files you own. Need a contract change? Stop and report it; the main session changes `schemas.py`.
- No real network calls in tests. No `print`, no bare `except`, no `Any` without a comment explaining why.
- Report: files changed, gate output (last lines), open risks. No claims without command output.
