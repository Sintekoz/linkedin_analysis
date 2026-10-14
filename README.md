# LinkedIn Analysis · Next Role

A local HTML workspace for job-board and employer-feed jobs and your application pipeline. The website is served by Python and stores your data in SQLite on your computer. No frontend build, Node runtime, LinkedIn password or OpenAI key is required. Automatic fit ratings use a Codex ChatGPT login and the Luna model.

## Start

Python 3.11+ is recommended. On macOS, double-click **start.command** or run:

```sh
python3 main.py --port 8876
```

Open **http://127.0.0.1:8876**. The server opens your browser automatically unless `--no-browser` is passed. Keep the terminal open; press Ctrl+C to stop it. If that port is occupied, choose another with `--port`.

### Enable job-board collection

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-scraping.txt
python main.py --port 8876
```

The checked-in `requirements-lock.txt` records the full environment used for validation (including optional AI dependencies). To reproduce it, install that file instead.

Create a saved search under **Job searches**, select LinkedIn, Indeed, Glassdoor, Greenhouse or Lever, and configure keywords and location. **All available results** and **Any posting date** are the defaults: there is no dashboard quantity cap or age cutoff. Leave **Optional result limit** blank to collect all accessible matches, or set a positive number when you want a smaller run. Job boards also support posting age, job type and remote filters. Indeed and Glassdoor use an explicit country (Ireland by default). Greenhouse and Lever take the employer’s board name/careers link and employer name; title alternatives can be separated with `|`. Employer feeds search published roles and do not invent posting dates.

Click **Find & rate jobs** in the top bar on any page. One click groups confirmed duplicates, handles existing jobs waiting for ratings, then snapshots and runs every saved search with automatic ratings. It advances while the local dashboard runs; this chat and the browser may be closed. The control is disabled during an active workflow and repeat requests return the existing run instead of enqueuing duplicates. Per-search Run controls remain available for targeted collection. Batches commit immediately, duplicate postings are skipped, and new or changed jobs are automatically rated after collection. The queue has no 100-search ceiling. JobSpy requests use offsets (ten returned jobs per LinkedIn batch, 100 per Indeed/Glassdoor batch), with five seconds between batches and at least 30 seconds between LinkedIn searches. Greenhouse reads the complete published feed; Lever paginates without the former 2,000-posting scan ceiling. History reports batches, scanned results, matches, additions, refreshes and the stopping reason.

**All available** means everything exposed by the configured source/query, not a guarantee of all vacancies on that platform. Collection ends when the source returns a partial/empty batch or repeats results. [JobSpy documents upstream search windows of roughly 1,000 postings on many job-board endpoints](https://github.com/speedyapply/JobSpy#notes); the installed LinkedIn adapter itself stops at offset 1,000. Changing the dashboard cannot remove these upstream constraints. Explicit denials and errors appear in history, with partial results retained. A 403/429 pauses remaining searches on that platform for this workflow; other platforms continue. Retry the paused searches after a break. Remote/type filters are enforced against returned LinkedIn metadata because the tested JobSpy version ignores those parameters.

A rating/authentication/subscription failure pauses later model requests while collection continues. Jobs remain visible and the next **Find & rate jobs** workflow handles waiting ratings once the issue clears; unchanged valid ratings are still reused. **Stop workflow** cancels waiting work and terminates the active worker and its Codex children, preserving committed data. A restart marks unfinished work interrupted rather than silently resuming it. Healthy collection/rating runs have no total elapsed deadline; ten minutes without reported progress terminates a stalled worker. Each model batch retains a three-minute request timeout.

## What you can manage

- **Overview:** strongest matches (7/10 or higher), application counts, interviews, follow-ups due and collection/rating activity.
- **Job library:** relevant jobs (7/10 or higher) first, sorted by fit. Choose All jobs or Waiting / needs attention to inspect every collected posting. Search descriptions, titles, companies and locations; filter by stage, fit or remote work; sort by date, company, follow-up or AI score.
- **Applications:** saved → applied → interview → offer / rejected. Open a card to change stages. Discovered and withdrawn jobs remain in the library.
- **Job details:** edit metadata, inspect descriptions, store contact information, notes, application dates and follow-ups, and view activity. Opening a posting does not submit an application or change its stage.
- **Job searches:** create, edit, run and remove saved searches across five platforms, with CV-based rationale, the Find & rate jobs workflow and persistent run history.
- **Profile:** target role, skills, CV text and fit preferences. Skills are literal text coverage, not an AI suitability score.
- **Portability:** export applications as CSV or jobs as JSON, and import JSON from the Profile page. JSON backups include application tracking, AI results and cover letters. Importing matching jobs updates any tracking fields present in the import. Imported AI reviews are retained as reference data and marked pending until rated against your current profile.

The UI starts empty. Missing salary, descriptions, applicant counts and work models are never fabricated. Collection refreshes deduplicate by posting identity and preserve your application tracking. Posting availability is separate from application stage: missing search results never imply closure. You can set availability manually in Edit details; automated availability verification is not implemented.

## Automatic ratings through your Codex subscription

Each search saves collected jobs first, then automatically rates every new or changed job against your Profile CV and preferences using **gpt-6-luna**, with low reasoning effort. No per-job rating button is needed. Jobs without descriptions, a configured CV, or successful model access remain visible with an explanation under **Waiting / needs attention**; scores are never fabricated.

Install a current [Codex CLI](https://learn.chatgpt.com/docs/cli), then run `codex login` and choose **ChatGPT**. The dashboard also discovers the CLI bundled with the macOS ChatGPT app. Profile displays login readiness. An explicit executable can be supplied through `JOB_SEARCH_CODEX_BINARY`. The tested bundled CLI version is `0.162.0-alpha.17.2`; older CLI versions may need updating for the flags used by this integration. No credentials are copied or stored by this project.

Ratings run in a separate, short-lived process, in batches of up to five jobs. **This chat does not need to stay open.** Keep the local Python dashboard running while collecting and rating. Model execution is hosted by OpenAI; the website and database remain local. The CV, target role, skills, preferences and job metadata/description are supplied to OpenAI for each uncached batch.

The CLI runs with an explicit Luna model override, ChatGPT-only authentication, isolated temporary working directory, read-only sandbox, ephemeral session, no user configuration, no shell tools, no app/plugin integrations, no multi-agent tools, and no web search. API key environment variables are removed; there is no API or more expensive model fallback. [Codex authentication](https://learn.chatgpt.com/docs/auth), [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) document the underlying capabilities.

Cached ratings are reused when job evidence, your profile, model and scoring rubric match. Tracking notes and collection timestamps do not trigger another rating. A changed CV/preferences or job description invalidates the displayed score; the next collection of that posting refreshes it. **Find & rate jobs** also checks all existing waiting records after setup, description or request issues are fixed. Ordinary collection rates jobs automatically. The waiting-jobs view is for inspection; no per-job rating action is required. Profile updates do not silently start a full-library rating batch.

Ratings and development use the same [Codex subscription allowance](https://learn.chatgpt.com/docs/pricing). The smaller model and cached/batched requests help control usage; they do not create a separate token pool. Model access and subscription limits may interrupt ratings. Malformed responses receive one automatic retry. Each batch uses short, schema-required job slots rather than asking the model to copy database IDs. A second formatting failure or a model/authentication/subscription failure stops further model requests for that run and leaves remaining jobs inspectable for a later workflow. Run history reports rated, reused and attention-needed counts.

**Draft cover letter** remains an optional action for a selected job. It also uses Luna through the ChatGPT login and saves a downloadable text draft. Review ratings and letters before relying on them. The separate legacy `chatgpt_analysis.py` and `cl_generator.py JOB_ID` workflows still use API keys and their legacy dependencies (`requirements-ai.txt` / `environment.yaml`); the new dashboard does not call those scripts.

## Database & backups

Default database: `data/job_search.db`. Its location is anchored to this checkout. Override with `--db /absolute/path/workspace.db`.

For a complete backup of profile, searches, activity and jobs, **stop the dashboard and copy the SQLite database**. JSON export is a job backup, not a full workspace backup. Data files, `.env`, CVs and generated letters are ignored by Git.

An original database can be imported read-only and repeatedly:

```sh
python main.py --legacy-db /absolute/path/linkedin_jobs.db --port 8876
```

The original file is not modified; application tracking in the new database is retained on repeated legacy imports.

## Ireland and remote-location checks

Profile includes the country you will work from and your stated work permission. These are supplied to the rating worker as candidate context, not verified by the app. Nationality is not a suitability penalty. Work-permission details are excluded from cover-letter prompts.

Every collected role receives a separate location-evidence label: Ireland advertised, remote region requiring Ireland hiring confirmation, unclear location, or outside the target. Northern Ireland is treated as a UK location. A Europe/EMEA/global remote label is a lead requiring employer confirmation, not a guarantee of Irish payroll or work authorization. The library defaults to Ireland/remote leads; **All locations** retains access to all stored records. Employer feeds can filter before storage to Ireland and plausible remote regions.

## Scope and architecture

`dashboard/static/` is buildless HTML/CSS/JavaScript. `dashboard/server.py` exposes a small local API. `dashboard/store.py` owns persistence, `dashboard/collectors.py` normalizes JobSpy job-board data; `dashboard/ats.py` reads public Greenhouse and Lever feeds; `dashboard/locations.py` labels location evidence, and `dashboard/worker.py` runs collection and ratings in an isolated process. `dashboard/rating.py` handles Codex execution, structured result validation, caching and rating provenance. It binds only to `127.0.0.1`, rejects unexpected Host/Origin headers, checks a per-process token for writes and renders provider text without HTML execution. It is a personal local tool, not a public multi-user server.

Original Selenium functions remain in `page_scraper.py` and `check_cancelled.py` for users maintaining that pipeline, but the new entry point starts the dashboard. They require the dependencies in `environment.yaml` and a Chrome setup. The legacy selectors still depend on LinkedIn's DOM and were not live-tested. New collection uses JobSpy instead.

LinkedIn, Indeed, Glassdoor, Greenhouse and Lever are enabled. Greenhouse uses its global public board API; Lever supports global and EU board endpoints. Public employer feeds use verified TLS, bounded responses/pagination and GET requests only. Google Jobs is not enabled because JobSpy currently documents it as unavailable. ZipRecruiter is not enabled for this Ireland-focused workflow because its documented coverage is US/Canada. There is no unattended scheduling, automatic job application submission or CAPTCHA bypass.

## Validation

```sh
python -m unittest discover -s tests -v
python -m compileall -q dashboard main.py page_scraper.py chatgpt_analysis.py cl_generator.py
node --check dashboard/static/app.js  # optional developer check; Node not needed to run
```

Tests exercise canonical deduplication, tracking preservation, atomic import validation, backup round trips, read-only legacy migration, collection failure persistence, interrupted runs, filter preservation, HTTP writes and CSRF/Host/Origin checks, CSV formula escaping, automatic collection-to-rating execution, cached ratings, profile/job invalidation, provider/model failures, cancellation and API-login rejection. See [AUDIT.md](AUDIT.md) for findings and remaining limits.


## Duplicate handling

The workflow identifies repeated native posting IDs, normalizes known board URLs and strips marketing parameters. An individual employer posting URL can link the same vacancy across platforms. It keeps one visible entry, retains the discovered source links, preserves application tracking and reuses a current rating. Existing confirmed duplicates are grouped without deleting their original database rows. JSON backups include source-link provenance; CSV exports retain the underlying application records.

A shared company/title/location or identical description alone is not enough to merge jobs: employers reuse titles and templates for different requisitions. Similar listings stay separate and are noted in the library/details. Generic careers and search URLs cannot establish cross-platform identity. Separately tracked applications with different stages or notes are retained independently.


## Dublin and remote filters

The job library defaults to **Dublin + remote**. Dublin postal districts, repeated county labels and known provider codes normalize to Dublin, Ireland; County Dublin locations such as Swords and Sandyford are included. Use **Dublin only** or **Remote · Ireland / to confirm** for narrower views.

Remote results include advertised Ireland locations, broad Europe/EMEA/global regions and unspecified remote locations requiring confirmation. Explicit foreign-country restrictions take precedence over vague regional labels. Hybrid roles elsewhere in Ireland are not treated as remote. Original location text remains in job details and saved source data. Normalization is derived for display/filtering and does not overwrite job evidence, application tracking or cached fit ratings.

## Overall tracking and recovery

**Find & rate jobs** now creates one durable workflow. Its overview shows processed search/rating steps, unique jobs, newly added jobs, refreshed existing jobs, ready ratings, reused reviews and waiting ratings. Job counts are deduplicated across searches; they are not a claim of complete market coverage. Expand workflow history to inspect individual search messages. Earlier run history stays available in a collapsed section, with a single **Recover unfinished work** action.

Temporary connection/worker failures resume automatically from saved progress, up to three worker attempts. Public job-board pagination checkpoints advance only after a batch is saved; an interrupted batch can safely repeat without duplicate records or inflated totals. Employer feeds can refetch their published snapshot and reuse saved results. A completed collection is not scraped again when retrying its ratings.

Explicit platform denials trigger a two-minute cooldown, then an eight-minute cooldown, while other platforms continue. After the third denial that platform's remaining searches need attention. A rating timeout retries its batch once, then uses the bounded workflow recovery; login/model/subscription access problems defer model requests while collection continues. **Resume unfinished** retries unsuccessful steps without rerunning successful ones or consuming tokens for unchanged reviews. Missing descriptions are reported separately.

Closing the dashboard server preserves its queue. The next launch resumes unfinished work; closing only the browser never stops the background monitor. **Stop workflow** is an explicit stop and requires **Resume unfinished** to continue. On macOS/Linux, a workspace lock prevents overlapping dashboard servers. Real source access restrictions and subscription allowance still cannot be removed by retry handling.

## Open and closed positions

The job library now tracks **Open**, **Closed** and **Unverified** separately from application stages and fit ratings. **Find & rate jobs** automatically queues a posting-availability step after collection; **Check posting status** checks the saved library separately without model calls. The active full workflow receives this step when upgrading. Checks save per-job progress so interrupted checks can resume.

The library defaults to **Open + unverified**, hiding confirmed closures while retaining all records in **Closed** or **All posting statuses**. Details show the latest check, last confirmed status, closure reason and last-seen collection time. Edit details can manually mark a position open/closed/unverified. Closure never deletes applications or changes stages, notes or cached ratings.

Greenhouse and Lever check exact posting IDs against complete, unfiltered public employer feeds, shared across the run. Partial/error/repeated feeds cannot establish closure. Job-board pages require exact posting metadata, LinkedIn's explicit closure panel, or a non-redirected missing/gone response for a native posting. Employer evidence takes priority over aggregator links. Search absence, posting age, expired metadata, login redirects, blocked requests and unsupported employer platforms remain unverified. A failed recheck retains the last confirmed status; previously closed jobs stay out of the default active view until reopening is confirmed.

Public requests use verified TLS, restricted HTTPS destinations/redirects, bounded response sizes and timeouts, and platform-level denial suppression. No sign-in, CAPTCHA bypass, API billing or model rating is involved. Third-party markup and unsupported employer platforms can limit verification; unverified does not mean closed or confirmed open.

## Application agents · prepare, review, submit yourself

Each library row and job detail has **Apply with agent**. Review the named position and start preparation to authorize using your locally attached CV PDF and saved profile for that employer. Upload a PDF under 5 MB in Profile; add your contact details and any explicit answers there. Missing details are never inferred from nationality or salary-market estimates.

A separate, short-lived **gpt-6-luna** Codex CLI worker uses ChatGPT subscription authentication, with no API-key fallback. It operates through the installed native Browser plugin in a connected Chrome browser; the standalone worker cannot use the in-app browser. Keep the dashboard server and Chrome running; this development chat can close. Connect the ChatGPT Browser extension in Chrome before starting. An actual connection preflight on this host returned **No connected Chrome browser is available**; live form filling has therefore not yet been verified here.

Preparation pauses for missing answers, login/CAPTCHA, legal consents or an uncertain application destination. Its review panel shows progress, filled fields, requested answers and a screenshot when available. Retry explicitly after resolving a blocker; retries inspect the existing form and refresh the CV/profile snapshot. Only one preparation worker runs at a time, independently of collection. Repeated starts and matching employer URLs reuse an existing task; previously applied or confirmed-closed positions cannot start again. Interrupted preparation stays paused after restart, with no blind automatic application retry. Stop controls terminate the worker tree, and a 20-minute limit prevents unattended loops.

The agent is instructed to stop before final submission and has no submission or tracking tool. Review the prepared **existing Chrome tab**, handle consents, then submit yourself. An application link opened in another browser might not retain the form. After submitting, **I submitted it myself** records Applied and the date; an agent result alone never changes the application stage. This mode does not offer automatic submission.

CV copies, profile snapshots, preparation results and screenshots are local, ignored runtime files under `data/`. A complete backup needs both the SQLite database and its adjacent runtime files, not just the jobs JSON export.

## Quick library management

Right-click a position or use its **⋯** menu for **Not interested**, **Restore to library**, details and **Apply with agent**. Check several rows, shift-click a range, or select all positions matching the current filters; the sticky selection bar applies Not interested or Restore to the selection. Changing filters clears the selection so unseen positions cannot be changed by accident. Selection survives progress refreshes.

Not interested positions disappear from the default active library and strongest-match recommendations. Use **View not interested**, the interest filter, or **All interests** to retrieve them. **Undo last change** reverses the most recent change during this page session; Restore remains available after restarting. Job details provide the same Not interested/Restore control without discarding unsaved tracking notes. Application stages, dates, notes, posting status and rating cache are retained. Collection refreshes do not restore dismissed roles; matching duplicates follow the saved choice. Jobs JSON backups retain interest state. Marking a role Not interested cancels its queued/running application preparation; restoring it never automatically restarts the agent.
