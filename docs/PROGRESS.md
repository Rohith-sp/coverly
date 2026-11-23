# Progress

## Done
- Restored the detailed README at the project root.
- Moved the project instructions and product requirements into `docs/`, leaving `README.md` as the only Markdown file at the root.
- Clarified that this checkout currently contains no API or web application source, so the app is not runnable yet.
- Spec fixes (2026-11-24): zero-cost tagging limits (video cap, keyword pre-filter, truncation, batching), one search per topic with no retry, recency constant in v0, budget-skip greedy, templated reasons, transcript lib 1.x note, `coverly` naming. Root `CLAUDE.md` imports `docs/CLAUDE.md`.

## In progress
- Application implementation has not started in this checkout.

## Next
- Scaffold the v0 API and web app, then implement the paste-text-to-watch-plan flow.
- Revisit free-hosting provider limits and image-input extraction choices before implementing those phases.

## Known issues
- Image input is a proposed future feature, not part of the current product requirements.
