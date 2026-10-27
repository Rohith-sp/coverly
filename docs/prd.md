# Coverly — Product Requirements Document

> Formerly titled MinWatch. Turns a study plan into the smallest set of YouTube videos and segments that covers every topic at the chosen level.

| Field | Value |
|---|---|
| Owner | Rohith |
| Status | Draft v1 |
| Target | Day 1: working local MVP (v0). Then v1 and v2 over the following weeks |
| Repo | `minwatch` (monorepo: `web/` + `api/`) |

---

## 1. Problem

Learning a subject from YouTube means choosing between dozens of overlapping playlists. Most people either watch one long course end to end (wasting hours on topics they already know or don't need) or jump between random videos (leaving gaps). Nothing answers the actual question:

**"Given my syllabus and my level, what is the least I need to watch to cover everything, and in what order?"**

## 2. Goal and success definition

**Goal:** Given a study plan (PDF, TXT or chat text), a target level and a time budget, return an ordered watch plan of videos and timestamp ranges that covers every topic with the minimum total watch time.

"Best" is defined, not hand-waved:

> The minimum-cost set of video segments such that every topic in the plan is covered at or above the required depth, where cost = watch time adjusted by a quality score.

This is a **weighted set cover** problem, solved with the greedy approximation algorithm (section 9).

**Success for v0 (today):**
- Paste the Backend syllabus (11 modules) → get an ordered plan in under 5 minutes.
- Every topic in the plan is either covered by at least one segment or explicitly listed as "not covered".
- Every recommended item links to YouTube at the right timestamp.

**Success for v1+:** measured against the benchmarks in section 14.

## 3. Users

- **Primary:** me. A CS student who learns mainly from YouTube and wants minimum watch time with maximum understanding.
- **Secondary:** other students preparing for placements, who paste a syllabus or interview-prep list.

## 4. Scope

### 4.1 v0 — today (must ship)

| # | Feature |
|---|---|
| F1 | Input: plain text / chat message (paste a syllabus) |
| F2 | Settings: level (`beginner` / `interview` / `deep`), language (`en`, `hi`, `both`), time budget in hours (optional) |
| F3 | LLM parses input into a topic tree (module → topics) |
| F4 | User reviews and edits the topic list before running (add, delete, rename) |
| F5 | YouTube search per topic (YouTube Data API v3) |
| F6 | Fetch metadata + transcripts for candidate videos |
| F7 | Segment transcripts into windows and tag each window with topics from the plan + depth |
| F8 | Score candidates and run greedy set cover |
| F9 | Ordered result page: video title, channel, timestamp range, topics covered, reason |
| F10 | Progress indicator while the job runs (polling) |
| F11 | Cache: transcripts and video metadata stored locally, reused across runs |

### 4.2 v1 — next 1–2 weeks

- PDF and TXT upload
- Playlist-level search (not just individual videos) + playlist continuity bonus
- Web search for curated recommendation lists (Reddit, GitHub awesome lists) to seed candidates
- Postgres + pgvector instead of SQLite; embedding-based topic normalization
- Proper job queue (Redis + worker)
- Export plan as Markdown / PDF
- Auth + saved plans

### 4.3 v2 — later

- Benchmark suite and evaluation dashboard (section 14)
- Integration with a study tracker (mark segments done, re-plan the remainder)
- Shared global video-analysis cache across users
- Deployment, rate limiting, observability

### 4.4 Out of scope (all versions for now)

- Hosting or downloading video/audio
- Paid platforms (Udemy, Coursera)
- Speech-to-text for videos without captions
- Mobile app

## 5. User flow (v0)

```
[Paste syllabus + choose level/lang/budget]
        │ POST /plans
        ▼
[Review topic tree] ── edit ── PUT /plans/{id}/topics
        │ POST /plans/{id}/run
        ▼
[Progress: searching → fetching → tagging → optimizing]   (GET /jobs/{id} every 2s)
        ▼
[Result: ordered watch plan + uncovered topics + total watch time]
```

## 6. Functional requirements

### F3 — Plan parsing
- Input up to 20,000 characters.
- LLM returns JSON only:
  ```json
  {
    "title": "Backend Engineer",
    "modules": [
      { "name": "Authentication and security",
        "topics": ["password hashing", "sessions vs JWT", "refresh tokens", "RBAC", "OWASP Top 10"] }
    ]
  }
  ```
- Each topic gets a stable `topic_id` (slug + short hash).
- Hard cap for v0: **40 topics** (quota protection, see 10.1). Above that, the UI asks the user to trim or split the plan.

### F5 — Search
- For each topic, build **one** query: `"{topic} {module context} tutorial"`, plus `hindi` when language is `hi`.
- `search.list` with `type=video`, `maxResults=10`, `relevanceLanguage` set from the language setting, `videoCaption=closedCaption` preferred (fall back to any if fewer than 3 results).
- Deduplicate video IDs across topics. One video often matches several topics, and that overlap is exactly what set cover exploits.

### F6 — Metadata and transcripts
- `videos.list` in batches of 50 IDs: title, channel, duration, view count, like count, published date, description (chapters).
- Transcript via `youtube-transcript-api` (Python). Prefer manual captions, then auto-generated, in the requested language.
- If no transcript is available: build pseudo-segments from description chapters if present, else treat the whole video as one segment tagged from title + description only, with `confidence` capped at 0.5.

### F7 — Segmentation and tagging
- If the description has chapters (`00:00 Intro` lines), use them as segment boundaries.
- Otherwise split into windows of ~4 minutes, cut at sentence gaps.
- For each segment, the LLM receives the **closed list of plan topics** (never free-form) and returns:
  ```json
  { "segment_id": "abc123_03",
    "tags": [ { "topic_id": "sessions-vs-jwt-1f2a", "depth": 2, "confidence": 0.85 } ] }
  ```
  - `depth`: 1 = mentions/introduces, 2 = explains with example, 3 = deep / internals / edge cases
- Batch several segments per LLM call to save requests.
- Validate JSON against a schema; on failure retry once, then drop that batch and log it.

### F8 — Scoring and optimization
See section 9.

### F9 — Result
Each item:
- Video title, channel, thumbnail
- Timestamp range + link: `https://www.youtube.com/watch?v={id}&t={start}s`
- Topics covered (chips) and depth
- One-line reason ("Covers JWT + refresh tokens at interview depth in 11 min")
- Consecutive segments of the same video are merged into one range.

Page footer: total watch time, topics covered N/M, list of uncovered topics with the reason (no candidates found / below required depth).

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| Latency | v0 run for 15–20 topics completes in < 5 min; first partial progress within 10 s |
| Cost | Zero: YouTube Data API free quota, free-tier LLM (Groq), SQLite locally |
| Reliability | A failed transcript or LLM batch never fails the whole run; the result reports partial coverage |
| Security | API keys only on the server; uploads (v1) capped at 5 MB, PDF/TXT only; CORS limited to the frontend origin |
| Privacy | Plans stored locally; no account needed in v0 |
| Observability | Structured logs per job stage with timings and quota units used |

## 8. Architecture

```
web/ (Next.js App Router, Tailwind)
   │  REST (JSON)
   ▼
api/ (FastAPI)
   ├── routes/        plans, jobs, results
   ├── pipeline/
   │     parse.py       syllabus → topic tree (LLM)
   │     search.py      YouTube search + metadata (Data API v3)
   │     transcripts.py transcript fetch + chapter parsing
   │     segment.py     windowing
   │     tag.py         LLM tagging (batched, schema-validated)
   │     score.py       quality + level fit
   │     cover.py       greedy weighted set cover + ordering
   ├── jobs.py        v0: FastAPI BackgroundTasks + job table; v1: Redis/RQ worker
   ├── db.py          SQLite (v0) → Postgres + pgvector (v1)
   └── llm.py         Groq client, model routing, retries, JSON mode
```

**Why FastAPI for the backend:** `youtube-transcript-api` and PDF parsing are best in Python, and FastAPI is already familiar from other projects. Next.js stays as the UI only.

**LLM routing:**
- Plan parsing and final reasons → larger model (e.g. Llama 3.3 70B on Groq)
- Segment tagging (high volume) → small fast model (e.g. Llama 3.1 8B on Groq)
- Check current model names and free-tier limits on the Groq console before coding.

## 9. Core algorithm

### 9.1 Definitions
- **Universe U** = plan topics `t`, each with required depth `d_req(t)` from the level setting:
  - `beginner` → 1, `interview` → 2, `deep` → 3
- **Candidates S** = segments `s` with duration `dur(s)` in minutes and a tag set.
- Segment `s` **covers** topic `t` if it has a tag with `depth ≥ d_req(t)` and `confidence ≥ 0.6`.
- For `beginner`, also penalize depth-3 tags (too advanced): treat them as covering but add +20% cost.

### 9.2 Quality score q(s) ∈ [0.5, 1.5]
Computed per video, inherited by its segments:

```
engagement = like_count / max(view_count, 1)          → percentile-normalized within candidate pool
popularity = log10(view_count + 1)                    → normalized
recency    = 1.0 if published < 3 years, else 0.8     (only for fast-moving topics flagged by the parser)
captions   = 1.0 if real transcript, 0.7 if metadata-only

q = 0.5 + (0.4*engagement + 0.3*popularity + 0.2*recency + 0.1*captions)
```

### 9.3 Cost
```
cost(s) = dur(s) / q(s)
         + switch_penalty   (2 min if s is from a video not yet selected; 0 if same video as an already chosen segment)
```
The switch penalty encourages finishing coverage inside videos you're already watching instead of hopping channels.

### 9.4 Greedy set cover
```
uncovered = U
plan = []
while uncovered not empty:
    best = argmax over s in S of  |covers(s) ∩ uncovered| / cost(s)
    if best gains nothing: break                 # remaining topics have no candidate
    if budget and total_time + dur(best) > budget: break
    plan.append(best); uncovered -= covers(best)
    recompute switch penalties
return plan, uncovered
```
Greedy gives a known approximation bound of roughly ln(|U|) against the optimum, and it is fast enough for thousands of segments.

### 9.5 Post-processing and ordering
1. **Prune:** remove any chosen segment whose topics are all covered by other chosen segments (a reverse pass).
2. **Merge:** adjacent or overlapping segments from the same video become one range.
3. **Order:** sort by the earliest module/topic index each item covers (follows the syllabus order). Ties → keep a video's segments together.

## 10. External services

### 10.1 YouTube Data API v3 quota
- Default project quota is **10,000 units/day**; `search.list` costs **100 units**, `videos.list` costs **1 unit** per call (verify current numbers in the Google Cloud console before relying on them).
- v0 budget per run: 1 search per topic → 40 topics max ≈ 4,000 units + a few `videos.list` calls. That is about 2 large runs per day. Hence the 40-topic cap and caching.
- Cache search results by `(query, language)` for 7 days.

### 10.2 Transcripts
- `youtube-transcript-api` is unofficial and depends on YouTube's internal endpoints; it can break or be rate-limited, and its use is a terms-of-service grey area. Keep it behind `transcripts.py` with a clean interface so the source can be swapped, and keep the app personal/private until that is resolved.

### 10.3 LLM (Groq free tier)
- Respect rate limits with a token-bucket limiter in `llm.py` and exponential backoff on HTTP 429.
- Support two keys with rotation (same pattern as earlier projects).

## 11. Data model (SQLite v0, Postgres v1)

```sql
plans(id TEXT PK, title TEXT, raw_input TEXT, level TEXT, language TEXT,
      budget_minutes INT NULL, created_at TIMESTAMP)

topics(id TEXT PK, plan_id TEXT FK, module TEXT, name TEXT,
       position INT, required_depth INT)

jobs(id TEXT PK, plan_id TEXT FK, status TEXT,        -- queued|searching|fetching|tagging|optimizing|done|failed
     progress INT, message TEXT, quota_units INT, started_at, finished_at)

videos(id TEXT PK, title TEXT, channel_id TEXT, channel_title TEXT,
       duration_s INT, views INT, likes INT, published_at TIMESTAMP,
       description TEXT, has_transcript BOOL, fetched_at TIMESTAMP)

transcripts(video_id TEXT PK FK, language TEXT, source TEXT,   -- manual|auto|none
            text_json TEXT, fetched_at TIMESTAMP)

segments(id TEXT PK, video_id TEXT FK, start_s INT, end_s INT, text TEXT, source TEXT)  -- chapter|window|metadata

segment_tags(segment_id TEXT FK, plan_id TEXT FK, topic_id TEXT FK,
             depth INT, confidence REAL, PRIMARY KEY(segment_id, plan_id, topic_id))

search_cache(query TEXT, language TEXT, video_ids TEXT, cached_at TIMESTAMP,
             PRIMARY KEY(query, language))

results(plan_id TEXT PK FK, items_json TEXT, uncovered_json TEXT,
        total_minutes INT, created_at TIMESTAMP)
```

Note: tags are per plan in v0 because topic lists differ between plans. v1 normalizes topics with embeddings so tags can be shared across plans (the global cache in v2).

## 12. API

| Method | Path | Body / Query | Response |
|---|---|---|---|
| POST | `/plans` | `{ text, level, language, budget_hours? }` | `{ plan_id, title, modules[] }` |
| PUT | `/plans/{id}/topics` | `{ modules[] }` | `{ ok }` |
| POST | `/plans/{id}/run` | — | `{ job_id }` |
| GET | `/jobs/{id}` | — | `{ status, progress, message, quota_units }` |
| GET | `/plans/{id}/result` | — | `{ items[], uncovered[], total_minutes, coverage }` |
| GET | `/health` | — | `{ ok, quota_used_today }` |

Result item:
```json
{
  "video_id": "abc123",
  "title": "...",
  "channel": "Piyush Garg",
  "start_s": 840,
  "end_s": 1860,
  "url": "https://www.youtube.com/watch?v=abc123&t=840s",
  "topics": [{ "topic_id": "...", "name": "sessions vs JWT", "depth": 2 }],
  "reason": "Covers sessions vs JWT and refresh tokens at interview depth in 17 min"
}
```

## 13. Frontend (v0 pages)

1. **`/` New plan:** textarea, level selector, language selector, optional budget, "Parse plan" button.
2. **`/plans/[id]/review`:** editable topic tree grouped by module; topic counter with the 40 cap; "Find videos" button.
3. **`/plans/[id]/run`:** stage-by-stage progress bar, live log lines ("Tagged 120/310 segments").
4. **`/plans/[id]`:** result list in order, with total time and coverage at the top, uncovered topics at the bottom, "Copy as Markdown" button.

Include loading, empty ("no videos found for 3 topics") and error states on every page.

## 14. Evaluation (v1–v2)

| Metric | How |
|---|---|
| Topic coverage | % of plan topics covered at required depth |
| Watch-time efficiency | Total minutes vs. baseline "top search result per topic, full video" at equal coverage |
| Tagging accuracy | Hand-label ~100 segments (topic + depth), compute precision/recall of LLM tags |
| Course recovery | Feed the syllabus of a public course with an official lecture playlist (e.g. CMU 15-445) and check coverage and overlap with the official lectures |
| Personal check | Compare output for my Backend syllabus against the hand-picked minimum set in my syllabus PDF |

Report results in the README with numbers, not adjectives.

## 15. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| YouTube quota exhausted | Runs fail mid-way | 40-topic cap, search cache, partial results, show quota in `/health` |
| Transcript library breaks / blocked | Low-quality tags | Metadata/chapter fallback, confidence cap, swappable module |
| LLM tags topics wrongly | Wrong recommendations | Closed topic vocabulary, confidence threshold, schema validation, hand-labelled eval |
| Popular ≠ good | Weak picks | Engagement weighted above raw views; switch penalty favors coherent sources |
| Free-tier LLM rate limits | Slow runs | Small model for tagging, batching, backoff, two-key rotation |
| Scope creep today | Nothing ships | Strict v0 list (section 4.1); everything else goes to v1 |

## 16. Configuration (`api/.env`)

```
YOUTUBE_API_KEY=
GROQ_API_KEY_1=
GROQ_API_KEY_2=
LLM_MODEL_LARGE=llama-3.3-70b-versatile
LLM_MODEL_SMALL=llama-3.1-8b-instant
DB_URL=sqlite:///./minwatch.db
MAX_TOPICS=40
SEARCH_RESULTS_PER_TOPIC=10
SEGMENT_WINDOW_SECONDS=240
TAG_CONFIDENCE_MIN=0.6
CORS_ORIGIN=http://localhost:3000
```
`web/.env.local`: `NEXT_PUBLIC_API_URL=http://localhost:8000`

## 17. Day-1 build plan (realistic: ~10–12 focused hours)

| Block | Task | Done when |
|---|---|---|
| 1 (1 h) | Repo setup, FastAPI skeleton, SQLite schema, `.env`, Next.js skeleton | `/health` returns OK, frontend calls it |
| 2 (1.5 h) | `parse.py` + `POST /plans` + review page | Pasted syllabus becomes an editable topic tree |
| 3 (1.5 h) | `search.py` + metadata batching + search cache | Candidate videos stored for each topic, quota logged |
| 4 (1.5 h) | `transcripts.py` + `segment.py` (chapters first, windows fallback) | Segments stored for most candidates |
| 5 (2 h) | `tag.py` with batching, JSON validation, retries | Tags stored with depth + confidence |
| 6 (1.5 h) | `score.py` + `cover.py` + ordering/merging | Ordered plan + uncovered list produced in the terminal |
| 7 (1.5 h) | Job status + progress page + result page | Full flow works in the browser |
| 8 (0.5 h) | Run on own Backend syllabus, fix the worst issue, write README | Screenshot + first numbers in README |

If time runs short, cut in this order: progress page polish → chapter parsing (windows only) → language option (English only). Never cut the set cover step; it is the core of the project.

## 18. Open questions

- Should a whole playlist ever be recommended as one unit, or always broken into segments? (Decide in v1 with the continuity bonus.)
- How to handle topics that need hands-on practice rather than video (e.g. "SQL 50")? Possibly a "practice" item type in v1.
- Final name.
