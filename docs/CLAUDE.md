# CLAUDE.md — Coverly

Coverly turns a study plan (text, later PDF/TXT) plus a target level into the **smallest set of YouTube videos and timestamp ranges that covers every topic**, in syllabus order. The core is a greedy weighted set cover over transcript segments.

- Full spec: `docs/prd.md`. Read only the section you need (section numbers below), never the whole file by default.
- Build plan (agents, waves, quality gate): `docs/PLAN.md`.
- Session state: `docs/PROGRESS.md`. Read it at the start of every session, update it at the end.

**Current phase: v0** (PRD §4.1). Anything not in §4.1 is out of scope unless I ask for it.

---

## 1. Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router) + TypeScript (strict) + Tailwind CSS |
| Backend | Python 3.12 + FastAPI + Uvicorn |
| DB | SQLite in v0 (`api/coverly.db`), Postgres + pgvector in v1 |
| Jobs | FastAPI `BackgroundTasks` + `jobs` table in v0; Redis worker in v1 |
| LLM | Groq API free tier: large model for plan parsing only, small model for segment tagging; result reasons are templated (no LLM) |
| External | YouTube Data API v3, `youtube-transcript-api` |
| Tests | pytest (api), Vitest (web, only where logic exists) |
| Lint/format | ruff (api), ESLint + Prettier (web) |

Exact versions live in `api/pyproject.toml` / `api/requirements.txt` and `web/package.json`. Check those instead of assuming.

## 2. Repo layout

```
coverly/
├── README.md
├── docs/
│   ├── CLAUDE.md         # project instructions and conventions
│   ├── prd.md            # full spec
│   └── PROGRESS.md      # short running log: done / in progress / next / known issues
├── api/
│   ├── main.py           # FastAPI app, CORS, router registration
│   ├── routes/           # plans.py, jobs.py, results.py, health.py
│   ├── pipeline/
│   │   ├── parse.py        # syllabus text -> topic tree (LLM)          PRD §6 F3
│   │   ├── search.py       # YouTube search + videos.list batching      PRD §6 F5, §10.1
│   │   ├── transcripts.py  # transcript fetch + chapter parsing         PRD §6 F6, §10.2
│   │   ├── segment.py      # chapters first, ~240 s windows fallback    PRD §6 F7
│   │   ├── tag.py          # batched LLM tagging, closed vocabulary     PRD §6 F7
│   │   ├── score.py        # quality score q(s)                         PRD §9.2
│   │   └── cover.py        # greedy set cover + prune/merge/order       PRD §9.3–9.5
│   ├── jobs.py           # job runner and status updates
│   ├── db.py             # SQLite connection + schema                   PRD §11
│   ├── llm.py            # Groq client, model routing, rate limit, retries, JSON validation
│   ├── schemas.py        # Pydantic models for API and LLM JSON
│   ├── config.py         # env loading
│   └── tests/
└── web/
    ├── app/
    │   ├── page.tsx                  # new plan
    │   └── plans/[id]/
    │       ├── review/page.tsx       # edit topic tree
    │       ├── run/page.tsx          # progress
    │       └── page.tsx              # result
    ├── components/
    └── lib/api.ts                    # typed API client (single place for fetch calls)
```

## 3. Commands (Fedora)

```bash
# one-time
sudo dnf install -y python3.12 nodejs sqlite
cd api && python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd web && npm install

# run
cd api && source .venv/bin/activate && uvicorn main:app --reload --port 8000
cd web && npm run dev                      # http://localhost:3000

# check before saying a task is done
cd api && ruff check . && pytest -q
cd web && npm run lint && npm run build
```

Ask before running `sudo`, deleting files, resetting the DB, or anything destructive.

## 4. Environment

`api/.env` (never commit; `.env.example` is committed):

```
YOUTUBE_API_KEY=
GROQ_API_KEY_1=
GROQ_API_KEY_2=
LLM_MODEL_LARGE=
LLM_MODEL_SMALL=
DB_URL=sqlite:///./coverly.db
MAX_TOPICS=40
SEARCH_RESULTS_PER_TOPIC=10
SEGMENT_WINDOW_SECONDS=240
SEGMENT_TEXT_MAX_CHARS=1500
TAG_VIDEOS_PER_TOPIC=5
TAG_BATCH_SIZE=8
TAG_CONFIDENCE_MIN=0.6
CORS_ORIGIN=http://localhost:3000
```

`web/.env.local`: `NEXT_PUBLIC_API_URL=http://localhost:8000`

Model names come from env only. Never hardcode a model string in code.

## 5. Architecture rules

1. **Pipeline stages are separate modules with one job each.** A stage takes plain data in and returns plain data out; DB writes happen in the job runner, not inside stage logic.
2. **`cover.py` is a pure function** (no I/O, no LLM, no DB). It must stay unit-testable with fixtures. It is the core of the project: never stub it out or simplify it away.
3. **Closed topic vocabulary.** The tagger only returns `topic_id`s from the plan. Reject any tag with an unknown id.
4. **All LLM output is schema-validated** with Pydantic. On failure: retry once, then skip that batch and log it. One bad batch never fails the whole job.
5. **Partial results over failure.** Missing transcripts, quota errors, or LLM failures reduce coverage and are reported in `uncovered`; the job still finishes.
6. **YouTube quota is scarce** (search ≈ 100 units, videos.list ≈ 1 unit, default ≈ 10,000/day; verify in the Cloud console):
   - exactly one `search.list` per topic (no caption filter, no retry search), max 40 topics per plan
   - `videos.list` always batched by 50
   - cache search results by `(query, language)` for 7 days; cache metadata and transcripts permanently
   - log quota units used per job
7. **Transcript access stays inside `transcripts.py`** behind one function, so the source can be swapped. Fallback order: manual captions → auto captions → description chapters → metadata-only (confidence capped at 0.5).
8. **Zero cost: stay inside the Groq free tier.** Before tagging: tag only the top `TAG_VIDEOS_PER_TOPIC` videos per topic by q(s), keyword pre-filter segments against topic/module names, truncate text to `SEGMENT_TEXT_MAX_CHARS`, batch `TAG_BATCH_SIZE` per call. Log tokens used per job. (PRD §6 F7)
9. **`youtube-transcript-api` is 1.x:** instance API (`YouTubeTranscriptApi().fetch(...)`), not the old static `get_transcript`. Pin the version.
10. **Secrets stay on the server.** The frontend only talks to our API through `web/lib/api.ts`.

## 6. Algorithm constants (PRD §9)

- Level → required depth: `beginner=1`, `interview=2`, `deep=3`
- A segment covers a topic if `depth ≥ required` and `confidence ≥ TAG_CONFIDENCE_MIN`
- Beginner plans: depth-3 tags cost +20%
- `q(s) = 0.5 + 0.4*engagement + 0.3*popularity + 0.2*recency + 0.1*captions` (each term normalized to [0, 1]); `recency = 1.0` constant in v0
- `cost(s) = duration_min / q(s) + switch_penalty` (2 min if the video isn't already selected, else 0)
- Greedy: pick max `new_covered / cost` among candidates that still fit the budget; stop when everything is covered or nothing that fits gains anything. Budget-blocked topics are reported as `over budget`
- Then: prune redundant picks → merge adjacent ranges of the same video → order by earliest syllabus position

If any of these change, update this section and `docs/prd.md` §9 together.

## 7. Code conventions

**Python**
- Type hints on every function; Pydantic models for anything crossing a boundary (API, LLM, DB row → domain).
- `async` for network I/O (`httpx`), sync is fine for pure computation.
- No bare `except`. Catch specific errors, log with context (`job_id`, `stage`, `video_id`).
- Logging via the standard `logging` module with one line per event; no `print` in committed code.

**TypeScript / Next.js**
- Strict mode, no `any`. Response types mirror `api/schemas.py`.
- Server components by default; `"use client"` only where state or effects are needed.
- Every page has loading, empty and error states.
- Tailwind only. No new UI library without asking.

**General**
- No new dependency without a one-line reason in the PR/commit message.
- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Small commits, one concern each.

## 8. Testing

- `cover.py` and `score.py`: unit tests with hand-built fixtures (topics, segments, tags) covering: full coverage, impossible topic, budget cut-off, pruning, merging, ordering.
- `parse.py` / `tag.py`: test JSON validation and retry logic with mocked LLM responses.
- **Never call the real YouTube or Groq APIs in tests.** Use recorded JSON fixtures in `api/tests/fixtures/`.
- A bug fix comes with a test that fails before the fix.

## 9. Workflow for every task

1. Read `docs/PROGRESS.md`, then only the files and PRD section the task touches.
2. For changes touching more than two files, state a short plan (files + what changes) before editing.
3. Make the smallest change that fully solves the task. Don't refactor unrelated code.
4. Run the checks in §3. Report results honestly: say what was run and what passed, and never claim something works without running it.
5. Update `docs/PROGRESS.md` (a few lines: what changed, what's next, known issues).

**Done means:** feature works through the UI or the relevant endpoint, lint and tests pass, PROGRESS.md updated.

## 10. Token efficiency (important)

- **Don't read whole directories or large files "for context".** Use `grep`/`rg` and glob to find the exact place, then read only that range.
- **Don't re-read files you already read in this session** unless they changed.
- **Read `docs/prd.md` by section**, using the section numbers referenced in this file.
- **Edit with targeted replacements.** Never rewrite a whole file to change a few lines, and never paste back unchanged code.
- **Keep command output small:** pipe through `tail -n 50`, `head`, or `grep`. Use `pytest -q`, never verbose by default. Don't dump DB rows, transcripts, or API responses; print counts and one sample.
- **Batch independent tool calls** (several reads or greps at once) instead of one per turn.
- **Use subagents for broad searches** ("where is X used across the repo") so the main context stays clean.
- **Replies stay short:** what changed, files touched, check results, next step. No restating the task, no long explanations unless I ask.
- **Don't generate code speculatively** for future versions (v1/v2) unless asked.
- When context gets long, summarize the state into `docs/PROGRESS.md`, then use `/compact`.
- Keep this file under ~250 lines. Details belong in `docs/prd.md`, not here.

## 11. Skills

Before starting a task, check which skills are available (`.claude/skills/`, `~/.claude/skills/`, and installed plugins) and use one when it matches the task. Read the skill's `SKILL.md` first; it overrides general habits for that task. Only use skills that actually exist in this environment; if a name below isn't installed, skip it.

| Task | Skill to look for |
|---|---|
| Building or restyling any page/component | `frontend-design` |
| An error, stack trace, or "it worked before" | `debug` |
| Reviewing a diff before commit | `code-review` |
| Planning tests for a module | `testing-strategy` |
| Architecture choices (queue, DB, caching) | `system-design` / `architecture` |
| Keeping a change minimal | `ponytail` |
| README, landing page copy, or other public text | `humanize` |
| Dockerfile / deploy steps (v1+) | `deploy-checklist` |
| A repeated workflow worth automating | `skill-creator` |

Good candidates for **project skills** in `.claude/skills/` once the pattern repeats:
- `add-pipeline-stage`: module + schema + test fixture + job-runner wiring
- `add-api-endpoint`: route + Pydantic schema + typed client in `web/lib/api.ts` + test

## 12. Don'ts

- Don't edit `docs/prd.md` without asking; propose the change instead.
- Don't commit `.env`, `coverly.db`, or fixtures containing API keys.
- Don't add scraping of YouTube pages; only the Data API and the transcript module.
- Don't put API keys or model calls in the frontend.
- Don't bypass `cover.py` with LLM-picked recommendations.
- Don't invent library APIs. If unsure how a library behaves, check its installed version's docs or source.
