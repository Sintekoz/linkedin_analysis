# Repository audit

Audited original commit `977624f` on 12 October 2026. Scope: every tracked source file in the repository. No existing database or credentials were present. The supplied personal CV is stored only as text in the local ignored runtime data and is not part of this repository or pull request.

| Priority | Original finding | Resolution |
|---|---|---|
| High | No application tracking or UI; the database status only describes posting availability. | Added local HTML dashboard, separate application stages, notes, contacts, applied dates, follow-ups and activity history. |
| High | AI clients are constructed at import time and direct environment indexing raises without a key. | Deferred client construction in legacy AI scripts; dashboard uses an isolated Codex ChatGPT subscription worker for automatic ratings and optional letters; legacy clients remain deferred. |
| High | `cl_generator.py` unconditionally calls `generate_cover_letter()` without its required argument. | Added explicit CLI argument and main guard; dashboard provides a separately persisted text-letter workflow. |
| High | Scraper marks jobs ongoing before successfully extracting details; an unrelated feedback message skips extraction; replacement writes can lose records. | Reworked legacy extraction to save status and full details in a single transaction only after required fields are collected. Default collection uses a normalized JobSpy adapter and does not infer closure. |
| High | No ignore rules for credentials, databases, CVs or generated letters. | Added `.gitignore`; no runtime/personal records are committed. |
| Medium | Search URL formatter discards filters such as experience, employment type and remote settings. | Preserves all non-transient query filters. New UI explicitly supports keyword, location, job-type, posting-age and remote filters supported by JobSpy. Experience-level, company-ID and Easy Apply filters are not currently exposed in the UI. |
| Medium | Selenium uses fixed sleeps, fragile DOM selectors, weak login confirmation, unbounded pagination and duplicate IDs. | Default collection no longer depends on Chrome or account passwords. Legacy path verifies login redirects, bounds pagination, deduplicates and waits for titles. Its remaining DOM assumptions are documented. |
| Medium | AI analysis uses integer IDs while source tables use text, and commits all jobs only after a full batch. | Text IDs for newly created legacy analysis tables and per-result commits. New dashboard uses canonical text IDs and commits each collected record. Read-only migration handles legacy numeric IDs. |
| Medium | PDF extraction crashes on pages returning no text. | Legacy extraction handles absent page text; dashboard accepts CV text. Image-based PDF upload/OCR is not a dashboard feature. The supplied CV was visually read and transcribed during this task and populated locally. |
| Medium | Relative database locations depend on current working directory. | New default database is anchored to the checkout with a CLI override. Legacy scripts retain their old relative database contract. |
| Medium | No collection state, cancellation, restart recovery or tests. | Persistent run snapshots, isolated subprocesses, one active run, Stop control that terminates the worker process group, separate collection/rating timeouts, interrupted state on restart and regression tests. |
| Low | README asserts a fixed API deposit and references a missing MIT license. | Replaced outdated billing statements and removed the unsupported license assertion. A license file still needs an explicit owner decision. |

## Collection choice

[JobSpy's upstream documentation](https://github.com/speedyapply/JobSpy) describes LinkedIn support, richer job metadata, optional description requests, country support and rate limiting. It avoids maintaining the project's own authenticated Selenium search implementation. This is a pragmatic replacement, not a guarantee of completeness or uninterrupted access. Missing fields are retained as unknown; sources can omit salary or descriptions. Salary intervals are stored as supplied rather than silently converting hourly wages to annual figures.

Only LinkedIn is implemented, following the owner's request. If broader coverage is added later, direct employer feeds are useful candidates: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) provides public postings with optional description content, and [Lever Postings API](https://github.com/lever/postings-api) provides JSON job feeds. These APIs are future options, not shipped integrations.

## Verification

- Automated checks: Python persistence/HTTP tests and syntax checks for all changed Python modules and browser JavaScript.
- Live LinkedIn smoke check: 3 postings retrieved for data analyst in Dublin; 3 titles and 2 descriptions present. Test data was not inserted into the owner's database.
- Browser QA uses a separate temporary database: add job; set stage, notes and follow-up; view pipeline; create/edit search; run collection; verify completed results and active cancellation; profile/skill coverage; filtering; reload persistence; mobile and desktop layout.
- Live Codex verification: a fictional candidate/job successfully produced a structured Luna rating through the existing ChatGPT login. A separate temporary browser QA database then collected three live LinkedIn postings and automatically rated all three against the supplied CV, with no per-job action. Two scored 7/10 and appeared in the default relevant-jobs view. No API key or API billing path was used.
- Automatic-rating regression checks cover cache reuse, changed profile/job evidence, invalid scores and mismatched IDs, missing descriptions, failed batches, cancellation and stale-result rejection. Main personal database remains free of QA job records.
- Legacy Selenium login, live selectors, and legacy Word generation were not run. Those optional workflows require their own credentials/dependencies.

## Remaining limits

LinkedIn collection can be blocked or incomplete. JobSpy can return an empty dataframe without raising the underlying provider error; the UI explicitly explains the ambiguity instead of claiming jobs are closed. Duplicate URLs within LinkedIn collapse, while postings with distinct URLs remain distinct. There is no semantic cross-posting merge. Collection timestamps track discovery; they are not asserted to be original posting dates. Application stages describe the user's records, not verified employer outcomes. The app is loopback-only and does not implement multi-user authentication, hosted deployment, automatic applications or background schedules.

## Automatic rating update

The original manual API-button flow has been replaced in the dashboard with automatic Luna ratings after each LinkedIn collection. Jobs commit before model work, and a failed scrape still attempts to rate any saved records. Validated reviews retain score, strengths/gaps, model, fingerprint and rating timestamp. Database migrations only add columns; existing jobs and application records are preserved. Profile or job edits mark scores as pending rather than displaying stale reviews as current. No scheduler or continuously active chat is introduced.

Codex model access, authentication, connectivity and subscription limits remain external dependencies. Missing descriptions cannot produce evidence-based scores. The model's assessment is advisory and can be wrong; the library retains all postings and provides an All jobs filter.
