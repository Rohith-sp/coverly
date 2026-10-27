# Coverly

**Turn a syllabus into the shortest useful YouTube watch plan.**

Coverly is a study-planning tool concept: give it a syllabus and your target level, and it finds YouTube video segments that cover the topics you need to learn. Instead of watching several full courses or searching topic by topic, Coverly aims to give you an ordered plan of timestamped clips, show what each clip covers, and call out topics it could not cover.

> **Project status:** This checkout currently contains the product specification and project instructions, but not the `api/` or `web/` application code. The run and deployment instructions below describe the intended implementation; the app is not runnable from this checkout yet.
>
> The product requirements document was originally drafted under the working title **MinWatch** and has since been renamed to **Coverly**.

## The problem Coverly solves

A syllabus can contain many overlapping topics. Watching complete playlists often wastes time, while picking isolated search results can leave important topics out. Coverly is designed to treat the syllabus as a coverage problem:

1. Turn the study plan into an editable, ordered topic list.
2. Find candidate YouTube videos for those topics.
3. Inspect transcripts or available chapter information and divide videos into segments.
4. Estimate which topics each segment teaches, and at what depth.
5. Select and order a small set of useful segments.
6. Report uncovered topics instead of implying that every topic was found.

The goal is not to host or download videos. Recommendations link to YouTube at the relevant timestamp.

## Inputs and roadmap

Input support is planned in stages. **“Any input” is not a current capability or an unbounded promise**: each format needs its own extraction, validation, size limits, and review flow.

| Input | Planned availability | Notes |
|---|---|---|
| Pasted syllabus, chat message, or plain text | Phase 1 | The initial input. Paste a syllabus or study-topic list. |
| PDF or TXT file | Phase 2 | Extract text, then let the user review and correct the resulting topics. |
| Photo or screenshot of a schedule/syllabus | Phase 3 | A proposed extension using OCR or image understanding. Images are not in the current v0/v1 requirements. |
| Other formats | Not committed | Add formats individually only when there is a clear extraction and validation path. For example, audio transcription is currently out of scope. |

## How a study plan is produced

The intended workflow is:

1. **Submit a plan:** paste study-plan text and choose a learning level, language, and optional time budget.
2. **Review topics:** the parser proposes modules and topics; edit the list before starting.
3. **Find videos:** search YouTube for candidate videos and deduplicate results shared across topics.
4. **Inspect learning material:** fetch metadata and available transcripts. Prefer manual captions, then auto-generated captions; use description chapters or lower-confidence metadata when transcripts are unavailable.
5. **Tag segments:** split videos at description chapters where possible, otherwise use roughly four-minute windows. Tag each segment only against topics in the reviewed plan.
6. **Build the watch plan:** rank useful segments, remove redundant selections, merge adjacent ranges from the same video, and order by syllabus position.
7. **Show the result:** present timestamp links, topics and depth covered, total watch time, and topics that remain uncovered.

### Coverage and selection

Coverly is intended to use a greedy weighted set-cover algorithm, not an LLM directly choosing a final playlist. A segment counts as covering a topic only when its estimated depth meets the selected level and its confidence clears the configured threshold.

The planned levels map to required depth as follows:

| Level | Required depth |
|---|---:|
| Beginner | 1 — introduces or mentions the topic |
| Interview | 2 — explains it with an example |
| Deep | 3 — explores internals, edge cases, or advanced details |

Candidates are ranked by newly covered topics relative to estimated cost. Cost incorporates segment duration, a quality score, and a penalty for switching to a new video. The algorithm then prunes redundant segments, merges adjacent ranges from the same video, and restores syllabus order. A time budget can stop selection early; remaining topics are reported as uncovered.

This is an approximation, not a guarantee of the mathematically smallest or pedagogically perfect plan. Tagging quality and transcript availability affect results.

## Four-phase roadmap

The first two phases follow the current product requirements. The later phases include proposed work, especially image input and deployment; they are not features of the current MVP.

### Phase 1 — Text-based MVP

- Accept pasted text or a chat-style syllabus.
- Let the user choose level, language, and an optional watch-time budget.
- Parse the input into an editable module/topic tree.
- Search YouTube, inspect metadata and available transcripts, segment and tag content.
- Generate an ordered timestamped plan, including uncovered topics.
- Track job progress and cache video metadata and transcripts.

**Milestone:** a user can paste a study plan, correct its topics, run the analysis, and receive a usable watch plan.

### Phase 2 — Documents and saved plans

- Add PDF and TXT uploads with file-size/type limits and text-extraction error handling.
- Preview extracted content and require topic review before analysis.
- Improve candidate discovery with playlist search and curated sources.
- Add plan export and saved plans/authentication.
- Move to hosted PostgreSQL and a proper job queue as needed.

**Milestone:** users can bring in common syllabus documents and return to or export their plans.

### Phase 3 — Visual schedules and study workflow

- Explore OCR/image understanding for schedule photos, screenshots, and scanned syllabi.
- Show extracted text/topics for correction before creating a plan.
- Add a study tracker so users can mark segments complete and re-plan what remains.
- Build evaluation data to measure topic coverage, tagging accuracy, and watch-time savings.

**Milestone:** support selected visual inputs reliably, with user confirmation and clear extraction failures. This phase does not imply support for arbitrary files or audio.

### Phase 4 — Free-tier deployment and hardening

- Deploy the Next.js frontend and FastAPI API as separate services.
- Use a persistent database suitable for hosted operation; do not rely on an ephemeral container filesystem for SQLite data.
- Keep API keys on the backend, restrict CORS, validate uploads, and add rate limits and operational logging.
- Test cold starts, free-tier sleep/quotas, long-running jobs, backup/export options, and provider limits.

**Milestone:** a public demo can be operated within the selected providers’ free allowances, with documented limitations and no surprise paid resources enabled.

## Free deployment approach

Free deployment is a goal, not a guarantee. Provider plans, quotas, and availability change, and external APIs can have independent limits. Check current terms and billing settings immediately before deployment.

A possible starting arrangement is:

- **Frontend:** a provider with a free static/Next.js hosting tier (for example, Vercel).
- **API:** a provider with a free container/web-service tier (for example, Render, if its current plan fits the workload).
- **Database:** a managed PostgreSQL free tier (for example, Neon), after the planned SQLite-to-PostgreSQL migration.

These are candidates, not required vendors or assurances that the whole service will remain free. Free API services may sleep, impose request/runtime limits, or lack persistent local storage. The current v0 design uses SQLite; that is suitable for local development, but hosted SQLite requires a persistent volume. If the chosen free API host does not provide one, use the planned PostgreSQL migration rather than storing user data in an ephemeral filesystem.

The application also depends on YouTube Data API quota and a third-party LLM provider’s rate limits. Image extraction may require a separate OCR service or model and could exceed free allowances. Set usage alerts, limit plan size, and never enable billing or paid resources without reviewing their costs.

## Planned technology

| Area | Planned choice |
|---|---|
| Web app | Next.js App Router, TypeScript (strict), Tailwind CSS |
| API | Python 3.12, FastAPI, Uvicorn |
| Local database | SQLite |
| Later hosted database | PostgreSQL, with pgvector considered for topic normalization |
| Background work | FastAPI background tasks initially; a queue/worker in a later phase |
| Language model | Groq API, with model identifiers supplied through environment variables |
| Video discovery | YouTube Data API v3 |
| Transcript access | `youtube-transcript-api`, isolated behind the transcript module |
| Tests | pytest for API; Vitest where web logic warrants it |

The architecture separates input parsing, search, transcript retrieval, segmentation, tagging, scoring, and coverage selection. Coverage selection is intended to remain a pure, testable function. API keys and model calls stay on the server; the web app talks to the API through one typed client.

## Repository status and intended layout

The current checkout does not yet include these application directories. This is the intended structure described by the project specification:

```text
api/
  main.py
  routes/
  pipeline/
    parse.py
    search.py
    transcripts.py
    segment.py
    tag.py
    score.py
    cover.py
  jobs.py
  db.py
  llm.py
  schemas.py
  config.py
  tests/
web/
  app/
  components/
  lib/api.ts
```

## Local development (once the application is implemented)

The exact dependencies and supported commands should be taken from `api/pyproject.toml` or `api/requirements.txt` and `web/package.json` when those files exist. The intended development services are:

- API: `http://localhost:8000`
- Web app: `http://localhost:3000`

Create local environment files and provide your own credentials; do not commit them:

```dotenv
# api/.env
YOUTUBE_API_KEY=
GROQ_API_KEY_1=
GROQ_API_KEY_2=
LLM_MODEL_LARGE=
LLM_MODEL_SMALL=
DB_URL=sqlite:///./coverly.db
MAX_TOPICS=40
SEARCH_RESULTS_PER_TOPIC=10
SEGMENT_WINDOW_SECONDS=240
TAG_CONFIDENCE_MIN=0.6
CORS_ORIGIN=http://localhost:3000
```

```dotenv
# web/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Do not put secrets in `NEXT_PUBLIC_*` variables. Those values are exposed to the browser. The app code and dependency manifests are not present in this checkout, so setup/run commands are intentionally omitted rather than presented as working commands.

## Limits and safety considerations

- A plan is capped at 40 topics in the initial design to control YouTube quota usage.
- YouTube search quota is scarce; the intended design searches once per topic, batches metadata lookups, caches search results for seven days, and reuses stored metadata/transcripts.
- Some videos have no accessible transcript. Chapter/metadata fallbacks are less reliable and should be labeled accordingly.
- Transcript retrieval depends on an unofficial library that may break or be rate-limited. Keep the service private until its terms and operational risks are understood.
- LLM tagging can be wrong. Use a closed topic vocabulary, schema validation, confidence thresholds, and user-reviewed topics; report incomplete coverage.
- Never store provider credentials in the frontend or commit `.env` files, API keys, or a local database.

## Validation goals

Before calling the MVP complete, validate:

- A representative syllabus becomes a reviewable topic tree.
- Every topic is either covered by a recommended segment at the requested depth or explicitly listed as uncovered.
- Each recommendation opens the correct YouTube video at the correct timestamp.
- Partial transcript, quota, and LLM failures do not silently appear as complete coverage.
- The optimizer handles full coverage, impossible topics, time-budget cutoffs, pruning, merging, and syllabus ordering.
- Automated tests use mocked/recorded responses; they must not call live YouTube or LLM APIs.

The product specification defines further evaluation metrics, including topic coverage, tagging precision/recall, and watch-time savings against a baseline.

## Project documents

- [`CLAUDE.md`](./docs/CLAUDE.md) — project conventions, architecture constraints, and development workflow.
- [`prd.md`](./docs/prd.md) — detailed product requirements and algorithm specification.
- [`PROGRESS.md`](./docs/PROGRESS.md) — short implementation status and next steps.
