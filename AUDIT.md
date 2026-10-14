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

The initial release focused on LinkedIn. The 13 October expansion adds Indeed/Glassdoor and implements the public employer feeds documented by: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) provides public postings with optional description content, and [Lever Postings API](https://github.com/lever/postings-api) provides JSON job feeds. Greenhouse and Lever GET feeds are shipped integrations; application-submission endpoints are not called.

## Verification

- Automated checks: Python persistence/HTTP tests and syntax checks for all changed Python modules and browser JavaScript.
- Live LinkedIn smoke check: 3 postings retrieved for data analyst in Dublin; 3 titles and 2 descriptions present. Test data was not inserted into the owner's database.
- Browser QA uses a separate temporary database: add job; set stage, notes and follow-up; view pipeline; create/edit search; run collection; verify completed results and active cancellation; profile/skill coverage; filtering; reload persistence; mobile and desktop layout.
- Live Codex verification: a fictional candidate/job successfully produced a structured Luna rating through the existing ChatGPT login. A separate temporary browser QA database then collected three live LinkedIn postings and automatically rated all three against the supplied CV, with no per-job action. Two scored 7/10 and appeared in the default relevant-jobs view. No API key or API billing path was used.
- Automatic-rating regression checks cover cache reuse, changed profile/job evidence, invalid scores and mismatched IDs, missing descriptions, failed batches, cancellation and stale-result rejection. Main personal database remains free of QA job records.
- Legacy Selenium login, live selectors, and legacy Word generation were not run. Those optional workflows require their own credentials/dependencies.

## Remaining limits

LinkedIn collection can be blocked or incomplete. JobSpy can return an empty dataframe without raising the underlying provider error; the UI explicitly explains the ambiguity instead of claiming jobs are closed. Postings are combined when native IDs or individual employer posting links establish shared identity. Similar titles and reused descriptions are not proof of duplication; there is no semantic merge based on text alone. Collection timestamps track discovery; they are not asserted to be original posting dates. Application stages describe the user's records, not verified employer outcomes. The app is loopback-only and does not implement multi-user authentication, hosted deployment, automatic applications or background schedules.

## Automatic rating update

The original manual API-button flow has been replaced in the dashboard with automatic Luna ratings after each LinkedIn collection. Jobs commit before model work, and a failed scrape still attempts to rate any saved records. Validated reviews retain score, strengths/gaps, model, fingerprint and rating timestamp. Database migrations only add columns; existing jobs and application records are preserved. Profile or job edits mark scores as pending rather than displaying stale reviews as current. No scheduler or continuously active chat is introduced.

Codex model access, authentication, connectivity and subscription limits remain external dependencies. Missing descriptions cannot produce evidence-based scores. The model's assessment is advisory and can be wrong; the library retains all postings and provides an All jobs filter.

## Multi-platform expansion · 13 October 2026

Added Indeed and Glassdoor through the installed JobSpy integration, with country-aware configuration and correct provider provenance. Added direct Greenhouse and Lever adapters with complete description sections, original salary intervals where supplied, explicit missing posting dates, known-host board configuration, verified TLS and pagination. Runtime uses the existing trusted certificate bundle when available; certificate verification is never disabled.

Added an independently advancing queue for Run all searches, immutable run configurations, workflow cancellation and restart recovery. Model failures defer remaining queued model requests while collection continues; collection failures remain visible while other sources continue. New location labels separate skills ratings from Ireland/remote hiring evidence. The candidate’s stated work permission informs ratings; it is not verified by the app, and broader remote regions require employer confirmation. Personal permission details and CV-based search configuration remain only in ignored local data.

Live preflight checks retrieved two Ireland roles with descriptions from each of Indeed and Glassdoor, and verified Greenhouse feeds for Intercom/Fivetran and Lever feeds for Dun & Bradstreet/CarTrawler. End-to-end run counts are recorded in the local dashboard history.

The live expansion workflow ran all 27 saved searches (26 newly added plus the existing search) with a first-pass cap of three results per search. The workspace holds 58 unique jobs across all five platforms: 33 LinkedIn, 9 Indeed, 6 Glassdoor, 6 Greenhouse and 4 Lever. After refreshing earlier records against the updated profile, 45 have current ratings and 10 score at least 7/10 with Ireland/remote-region location evidence. Thirteen LinkedIn records lack descriptions and remain visibly unscored. These are local run observations, not a guarantee of future source availability or employer eligibility.

Live inspection also found that the installed JobSpy LinkedIn adapter ignores remote/type parameters. Added local metadata enforcement and a remote keyword, blocking-log reporting, and respectful spacing between subsequent LinkedIn searches. Forty-three regression tests cover the shipped workflow, including these guards and location-label backfill without changes to application tracking.

A final small LinkedIn remote-filter smoke request encountered provider denial/rate limiting. The new log guard surfaced this as a failed run instead of reporting a successful empty collection. Existing jobs and ratings remained intact; no bypass or repeated retries were attempted.


## Broad collection update · 13 October 2026

The first live workflow used a three-result smoke cap and the seeded searches retained five-result caps. Those were verification choices, not platform requirements. Removed the dashboard’s 100-result validation ceiling, default date cutoff, 100-search queue ceiling, 100-job rating retry cap and Lever’s 2,000-posting scan ceiling. New searches default to all accessible matches and any posting date. Optional positive result/date filters remain under user control. The local workspace’s saved searches were broadened without changing role/location criteria or application tracking.

Job-board collection now requests consecutive batches, saves each batch before requesting more, deduplicates overlaps and reports progress and stopping reasons. Empty/partial/repeated responses stop pagination without promising completeness. Explicit provider errors retain partial results. A denial pauses remaining queries on that provider while other platforms continue; a model failure defers further model requests without stopping ingestion. Valid cached ratings remain usable. Progress-based supervision replaces fixed total collection/rating deadlines, while stalled-worker and individual network/model request timeouts remain. Provider search windows, missing fields, availability and model allowance remain external constraints.


## One-button workflow and conservative duplicates · 13 October 2026

Added a persistent Find & rate jobs control across all pages. One action checks existing waiting jobs, snapshots every configured search, collects and automatically rates each result, and presents live progress in the library as well as the source view. Repeated start requests do not launch overlapping work.

Added source-link identity and marketing-parameter normalization for repeated and cross-platform postings. Confirmed duplicates retain one visible record and all discovered links; existing original records are archived in place rather than deleted. Tracking fields are preserved, and separately tracked applications with different notes/stages remain distinct. Similar titles/companies/locations and reused descriptions are not treated as proof of duplication. Library details expose both source links and separately retained similar listings. Backups preserve link provenance.

Sixty-four regression tests pass, including the complete single-action collection/rating sequence, cross-platform cache reuse, duplicate URL variants, distinct requisitions, generic careers links, tracked records and backup links. An isolated browser verification completed all configured searches and existing ratings with one click, resulting in two unique jobs, one confirmed duplicate grouped, both platforms linked and no waiting ratings. The live workflow was handed over to the updated dashboard with cached ratings and its existing queue preserved.

The broad live run exposed a model response with a mismatched database ID after hundreds of successful ratings. Collection still completed, leaving 2,609 unique visible jobs and 474 valid reviews. Replaced the free-form ID array with schema-required short job slots and explicit mapping back to database IDs, and added one bounded retry for malformed responses. Model access/allowance failures are not retried. Existing fingerprints and reviews remain valid because scoring criteria and evidence are unchanged. The single workflow button resumed waiting scores using the corrected format.


## Normalized location filters · 14 October 2026

Added derived city/country, display location, Dublin membership and remote-scope fields to the library response. Native employer/location text remains unchanged, preserving model fingerprints, scores and application records. Dublin districts and county/provider variants share one filter group, with County Dublin localities retained in display labels. The library defaults to Dublin + remote, with Dublin-only and remote-only options.

Explicit country restrictions override vague Europe/EMEA labels; foreign places named Dublin and Northern Ireland do not qualify as Dublin, Ireland. Unspecified remote locations remain visible with Ireland-hiring confirmation badges. Metadata/title-based remote detection does not relabel explicitly onsite/hybrid roles as fully remote. Seventy-one tests pass, including location variants, foreign city collisions, remote confirmation scopes and unchanged cached ratings/tracking. The running workflow was preserved across the local dashboard update.

## Workflow recovery and overall tracking · 14 October 2026

Live history showed blanket platform pauses, server-restart interruption, and rating timeouts propagated to every later query. Added persistent parent workflows, unique workflow/job membership, saved per-search job membership, pagination checkpoints, worker-attempt budgets and persisted cooldown times. Overall tracking replaces an expanded wall of individual history entries; technical search details remain available on demand.

Only temporary rating failures retry automatically; explicit subscription/login/model failures defer later model calls. Temporary rating errors no longer disable all subsequent ratings. Provider denials cool down only their platform and stop automatically after three attempts, preserving other source work. Retry/cancellation/idempotent start controls cover delayed as well as actively running steps. Server shutdown preserves recoverable queues, and a workspace lock prevents a second server from interrupting the first. Crash recovery terminates only a verified orphan worker whose arguments match the database and run.

Recovery tests cover platform cooldown/fairness/exhaustion, restart checkpoints, explicit stop/resume, unchanged rating cache, collection-complete rating retries, unique counts across repeated matches, legacy unfinished-source recovery and interrupted-page pagination. Missing descriptions and actual provider coverage remain data limitations rather than claimed successful ratings or complete collection.

Eighty-seven regression tests pass, including selected-history resume, delayed retry cancellation, shutdown recovery and bounded worker-launch failures. An isolated browser workflow recovered a simulated provider failure and completed all three sources with three unique rated jobs despite overlapping matches. The live upgrade retained all 3,000 job records and exactly preserved application tracking and cached rating fields against a pre-upgrade database backup.
