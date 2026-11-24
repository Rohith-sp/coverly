# Progress

## Done
- Docs: PRD spec fixes, build plan (`docs/PLAN.md`), root `CLAUDE.md` import.
- Wave 0: contracts (`config.py`, `schemas.py`, `db.py`, `/health`).
- Wave 1: optimizer (`score.py`, `cover.py`), Groq client + plan parsing (`llm.py`, `parse.py`), YouTube search/transcripts/segments, Next.js pages.
- Wave 2: tagging (`tag.py`: video cap, keyword pre-filter, truncation, batching, closed vocabulary), job runner (`jobs.py`) and all PRD §12 routes plus `GET /plans/{id}`.

## In progress
- Wave 3: code review, security review, simplify.

## Next
- Wave 4 (needs user): real `YOUTUBE_API_KEY` and Groq key(s) in `api/.env`, run the Backend syllabus, record coverage/minutes/run time/quota/tokens in README.

## Known issues
- Keyword pre-filter ignores tokens shorter than 3 chars and acronyms not spelled out in the topic name (e.g. "jwt" vs "JSON web tokens").
- Tags are recomputed on every run (LLM tokens spent on reruns); YouTube quota is fully cached.
- Tagging progress is per stage, not per batch.
- Image input is a proposed future feature, not part of the current product requirements.
