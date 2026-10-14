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

## Posting closure tracking · 14 October 2026

Added a dedicated resumable availability worker and automatic final step in the one-click workflow, including upgrades of active full-workflow queues. Bulk controls, posting filters/badges, evidence timestamps, last-confirmed state, manual overrides and change events keep vacancy status separate from application records and rating fingerprints. Search refreshes record sightings without erasing confirmed closures; a renewed employer-feed posting can reopen a role.

Employer-feed checks use complete, unfiltered published IDs with per-board caching and schema/pagination validation. Restricted public HTML checks require exact-role evidence and never use generic page reachability, age or search absence as proof. Blocked platforms are skipped after the first denial in a run. Unsupported employer URLs remain unverified, and the system retains previous confirmed evidence after failed rechecks.

102 tests pass. New cases cover employer-feed disappearance/reappearance, partial/error/repeated pagination, exact-role metadata, expired metadata, closure-panel context, auth redirects, private/unsupported destinations, platform denial suppression, persisted checks and stage/rating preservation. A live check of 17 employer-feed positions found nine open, one no longer published and seven unverified; the broader queued check covers the library after the active workflow. Browser verification confirmed the closed-role filter and evidence panel. The active queue and valid model cache were preserved during the upgrade.

## Review-only application preparation · 14 October 2026

Added per-position preparation controls, private CV PDF/profile snapshots, a separate subscription Luna queue and native Browser plugin connection. The worker has only the native browser JavaScript tools, no shell/apps/plugins, no API keys and no tracking/submission endpoint. Instructions treat job/CV/page content as untrusted reference data, restrict actions to the named employer/role and leave login, CAPTCHA, legal consent and final submission to the human. Agent-reported submission states are invalid. Only an explicit human confirmation from a ready-for-review task records Applied.

Preparation deduplicates by job and canonical employer URL, blocks application history and confirmed closure, and keeps interrupted/browser/login/missing-answer states for explicit retries. Retries refresh the saved profile while retaining supplied answers and instruct the worker to inspect existing form state. One application worker runs independently of the scraper, with process-tree stop/restart handling and a 20-minute timeout. Screenshots are served from a fixed local image path with type, symlink and size validation. Runtime CV, contact data, results and screenshots remain ignored.

A real CLI preflight using the new native MCP adapter and Luna succeeded and returned needs_browser: no connected Chrome browser. No CV was transmitted, no employer page was opened and no application was submitted. Live form filling is unverified until the Browser extension is connected in Chrome. Review mode is an instruction/agent boundary, not a deterministic browser-click firewall.

124 regression tests pass, including review-mode authorization, private PDF validation/snapshots, per-job and exact-posting duplicate guards, generic-careers separation, closed/history protection, answer/profile retries, subscription-only browser command, invalid submission claims, honest browser blockers, human-only tracking confirmation, worker serialization/timeouts and screenshot validation. An isolated UI fixture verified start → missing-answer pause → save answers → ready for human review, with no model or employer requests. The upgrade exactly preserved all real application tracking and cached ratings across 3,160 visible positions; zero real preparation tasks were started.

## Reversible quick library management · 14 October 2026

Added a migrated, separate interest disposition, atomic bulk writes, duplicate-owner propagation, events and backup support. Provider refreshes cannot overwrite it or invalidate cached ratings. Confirmed duplicate reconciliation retains the latest user choice. Not interested cancels queued/running application preparation and blocks a fresh start/retry until restored; it never changes application history.

Library controls include right-click/keyboard/⋯ menus, checkboxes, select-all matching, shift-range selection, sticky bulk actions, interest filters, session Undo and permanent Restore controls. The default library and strongest recommendations hide dismissed roles, while saved records and the application tracker remain accessible. Selection is retained through polling and cleared on filter changes; details actions preserve unsaved note/date fields.

137 regression tests pass. New cases cover migration defaults, atomic/invalid/idempotent bulk writes, unchanged application/rating fields, persistent dismissal through source refresh, restore/events, backup round trips, exact duplicate handling, agent cancellation and retained retrieval. Isolated browser verification covered right-click dismissal, Undo, multi-select dismissal, hidden-list retrieval, details restoration with unsaved notes, reload persistence and select-all restoration. The real upgrade exactly retained application tracking and ratings across 3,160 visible positions; no real positions were dismissed during QA.

## LinkedIn failure diagnosis and recovery · 14 October 2026

The displayed 19/21 issue count described workflow steps: one LinkedIn query exhausted retries, 17 matching queries were not attempted, and the availability step was incorrectly swept into the LinkedIn pause because its empty config defaulted to that provider. Existing ratings and the Indeed search had completed. An initial live one-position search returned HTTP 429; a later metadata-only probe returned one position, while a full-description probe returned one position with a separate HTTP 429 warning from its description page. These are distinct failures, not 19 failed job applications.

Fixed provider isolation by restricting platform cooldown/exhaustion propagation to collection steps. Added selected-step resume and explicit attempted/not-started issue grouping. Description warnings no longer fail a successful search batch. Optional detail reads are paced and stop after the first denial; later pages and retries use metadata without repeating denied detail requests. Search checkpoints retain saved IDs and the failed offset, successful pages advance before subsequent errors, and the most recent raw collection failure survives cooldown messages. Search pages now use a 30-second interval; limits and optional date filters remain user-selected.

The final suite has 153 tests including first-denial detail suppression, pacing/adapter restoration, persistent error diagnostics and progress-aware retry budgets. The JobSpy detail wrapper is confined to an isolated worker call, restores its original method and gracefully falls back when that optional adapter method is unavailable. Current [JobSpy documentation](https://github.com/speedyapply/JobSpy#parameters-for-scrape_jobs) confirms full-description fetching adds per-position requests.

Reran the previously skipped availability step successfully across all 3,160 stored positions: 98 open, three closed and 3,059 unverified. Blocked or ambiguous pages never establish closure. Tracking, interest choices and cached rating fields exactly matched the pre-upgrade backup after this check. Then resumed only unfinished LinkedIn queries, leaving successful ratings/Indeed work untouched and queuing a follow-up availability pass for newly collected positions. The replay saved a batch of ten listings and a next offset of ten; subsequent search rate limits triggered cooldown with description fetching deferred. Platform responses remain intermittent, so this is not a claim that all LinkedIn queries now complete or that access restrictions were removed.

The rerun exposed an additional retry-budget defect: the search advanced from offset 10 to 50 but exhausted the lifetime three-attempt budget. Recovery now records the starting checkpoint of each attempt, preserves its markers through collection, and renews the failure streak only when a saved batch advances. Three consecutive failures without progress still stop. The final suite has 153 tests covering progress renewal, non-progress exhaustion and marker persistence. The continued rerun resumes at offset 50; the first replay added 28 positions whose missing descriptions remain unscored while description requests are denied.

If a status pass finishes while a provider is cooling down, later collection results now queue a new-only status pass automatically. Already checked positions are skipped. Two additional tests cover late-arriving jobs and avoiding redundant passes. The full suite has 153 tests.


## Independent ingestion pipeline · 14 October 2026

The preceding recovery rerun ultimately stopped at LinkedIn offset 270; 17 queries did not start. The live library contained 3,401 visible positions: 281 LinkedIn positions with only 29 descriptions, versus complete descriptions for 1,717 Indeed, 1,386 Glassdoor and 17 employer-feed positions. This motivated an architectural change rather than a parser-library swap.

Added a durable per-posting enrichment queue, metadata-first page turns, fair scheduling across queries, separate automatic rating steps, and shared persistent public-response caching. Existing descriptions and verified posting identities prevent redundant detail reads; changed descriptions/profile fingerprints drive rerating. Supported employer feeds precede aggregator pages, and exact URL/main-title checks reject unrelated JobPosting recommendations. A status pass reuses fetched descriptions rather than fetching again. Application stages, notes, disposition and submission boundaries remain independent.

Durable endpoint health separates search and detail requests, shares platform cooldowns across workflows and restarts, respects exposed Retry-After headers, and does not spend request attempts on cooldown skips. Bounded failures stop only the affected endpoint/queue. Fair pagination rotates accessible queries without setting a total posting cap. Source/platform restrictions and unsupported employer hosts remain honest limitations; this change does not guarantee LinkedIn access or universal market coverage.

Added six verified Greenhouse employer boards (Stripe, Datadog, MongoDB, Elastic, Canonical, Toast) and idempotent catalog controls. Newly discovered exact Greenhouse/Lever employer links can extend the active collection workflow. Overall metrics distinguish found positions, descriptions, ratings, query coverage, network outcomes/cache reuse and recovery gaps; strong Ireland-advertised leads per hour are not an eligibility guarantee.


Live validation also exposed Glassdoor detail gaps when discovery explicitly disabled optional descriptions. Added an isolated optional JobSpy batch-detail adapter that reads only deduplicated exact posting IDs, caches successful descriptions and initializes the normal anonymous session without loading/persisting login cookies. Structured-detail denial has its own bounded cooldown and does not claim success or trigger a browser bypass. Retrieval counters distinguish opaque JobSpy source batches from direct public reads rather than pretending to count every internal library HTTP request. Rating progress preserves prior-pass counters during later model batches; enrichment prioritizes tracked roles and older pending work to avoid starving it behind newly discovered jobs.

Final validation: 181 regression tests pass without optional site packages, plus Python compilation and JavaScript syntax checks. The local upgrade preserved tracking and interest fields exactly across the original 3,401 records. A real workflow added 1,761 positions, recovered over 780 missing descriptions through the initialized batch adapter and saved over 660 fresh Luna reviews while reusing existing valid reviews. All 38 non-LinkedIn searches completed; LinkedIn search access still required attention, with attempted and unstarted queries distinguished accurately. Recovery and rating continued independently of this chat; no real application preparation/submission task was started.
