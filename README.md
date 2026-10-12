# LinkedIn Analysis · Next Role

A local HTML workspace for LinkedIn jobs and your application pipeline. The website is served by Python and stores your data in SQLite on your computer. No frontend build, Node runtime, LinkedIn password or OpenAI key is required. Automatic fit ratings use a Codex ChatGPT login and the Luna model.

## Start

Python 3.11+ is recommended. On macOS, double-click **start.command** or run:

```sh
python3 main.py --port 8876
```

Open **http://127.0.0.1:8876**. The server opens your browser automatically unless `--no-browser` is passed. Keep the terminal open; press Ctrl+C to stop it. If that port is occupied, choose another with `--port`.

### Enable LinkedIn collection

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-scraping.txt
python main.py --port 8876
```

The checked-in `requirements-lock.txt` records the full environment used for validation (including optional AI dependencies). To reproduce it, install that file instead.

Create a saved search under **LinkedIn searches**, choose keywords, location, posting age, job type, remote-only filtering, result limit and whether to fetch descriptions, then click **Run search**. Begin with 25 results. Only one collection runs at a time. Run history displays state, result counts and failures; **Stop** terminates the collection worker and its Codex child processes and retains committed jobs and ratings. Collection is capped at ten minutes; rating is capped at thirty minutes, with a three-minute timeout per model batch. Search coverage and fields depend on LinkedIn and JobSpy; upstream blocking can produce zero results without a raised error.

## What you can manage

- **Overview:** strongest matches (7/10 or higher), application counts, interviews, follow-ups due and collection/rating activity.
- **Job library:** relevant jobs (7/10 or higher) first, sorted by fit. Choose All jobs or Waiting / needs attention to inspect every collected posting. Search descriptions, titles, companies and locations; filter by stage, fit or remote work; sort by date, company, follow-up or AI score.
- **Applications:** saved → applied → interview → offer / rejected. Open a card to change stages. Discovered and withdrawn jobs remain in the library.
- **Job details:** edit metadata, inspect descriptions, store contact information, notes, application dates and follow-ups, and view activity. Opening a posting does not submit an application or change its stage.
- **LinkedIn searches:** create, edit, run and remove saved searches, with persistent run history.
- **Profile:** target role, skills, CV text and fit preferences. Skills are literal text coverage, not an AI suitability score.
- **Portability:** export applications as CSV or jobs as JSON, and import JSON from the Profile page. JSON backups include application tracking, AI results and cover letters. Importing matching jobs updates any tracking fields present in the import. Imported AI reviews are retained as reference data and marked pending until rated against your current profile.

The UI starts empty. Missing salary, descriptions, applicant counts and work models are never fabricated. Collection refreshes deduplicate by canonical posting URL and preserve your application tracking. Posting availability is separate from application stage: missing search results never imply closure. You can set availability manually in Edit details; automated availability verification is not implemented.

## Automatic ratings through your Codex subscription

Each search saves LinkedIn jobs first, then automatically rates every new or changed job against your Profile CV and preferences using **gpt-6-luna**, with low reasoning effort. No per-job rating button is needed. Jobs without descriptions, a configured CV, or successful model access remain visible with an explanation under **Waiting / needs attention**; scores are never fabricated.

Install a current [Codex CLI](https://learn.chatgpt.com/docs/cli), then run `codex login` and choose **ChatGPT**. The dashboard also discovers the CLI bundled with the macOS ChatGPT app. Profile displays login readiness. An explicit executable can be supplied through `JOB_SEARCH_CODEX_BINARY`. The tested bundled CLI version is `0.162.0-alpha.17.2`; older CLI versions may need updating for the flags used by this integration. No credentials are copied or stored by this project.

Ratings run in a separate, short-lived process, in batches of up to five jobs. **This chat does not need to stay open.** Keep the local Python dashboard running while collecting and rating. Model execution is hosted by OpenAI; the website and database remain local. The CV, target role, skills, preferences and job metadata/description are supplied to OpenAI for each uncached batch.

The CLI runs with an explicit Luna model override, ChatGPT-only authentication, isolated temporary working directory, read-only sandbox, ephemeral session, no user configuration, no shell tools, no app/plugin integrations, no multi-agent tools, and no web search. API key environment variables are removed; there is no API or more expensive model fallback. [Codex authentication](https://learn.chatgpt.com/docs/auth), [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) document the underlying capabilities.

Cached ratings are reused when job evidence, your profile, model and scoring rubric match. Tracking notes and collection timestamps do not trigger another rating. A changed CV/preferences or job description invalidates the displayed score; the next collection of that posting refreshes it. **Retry waiting jobs** handles up to 100 waiting records in one background run after fixing setup, missing descriptions or a failed request. It is a recovery control; ordinary searches rate jobs automatically. Profile updates do not silently start a full-library rating batch.

Ratings and development use the same [Codex subscription allowance](https://learn.chatgpt.com/docs/pricing). The smaller model and cached/batched requests help control usage; they do not create a separate token pool. Model access and subscription limits may interrupt ratings. One failed batch stops further model requests for that run and leaves remaining jobs inspectable for a later retry. Run history reports rated, reused and attention-needed counts.

**Draft cover letter** remains an optional action for a selected job. It also uses Luna through the ChatGPT login and saves a downloadable text draft. Review ratings and letters before relying on them. The separate legacy `chatgpt_analysis.py` and `cl_generator.py JOB_ID` workflows still use API keys and their legacy dependencies (`requirements-ai.txt` / `environment.yaml`); the new dashboard does not call those scripts.

## Database & backups

Default database: `data/job_search.db`. Its location is anchored to this checkout. Override with `--db /absolute/path/workspace.db`.

For a complete backup of profile, searches, activity and jobs, **stop the dashboard and copy the SQLite database**. JSON export is a job backup, not a full workspace backup. Data files, `.env`, CVs and generated letters are ignored by Git.

An original database can be imported read-only and repeatedly:

```sh
python main.py --legacy-db /absolute/path/linkedin_jobs.db --port 8876
```

The original file is not modified; application tracking in the new database is retained on repeated legacy imports.

## Scope and architecture

`dashboard/static/` is buildless HTML/CSS/JavaScript. `dashboard/server.py` exposes a small local API. `dashboard/store.py` owns persistence, `dashboard/collectors.py` normalizes JobSpy LinkedIn data, and `dashboard/worker.py` runs collection and ratings in an isolated process. `dashboard/rating.py` handles Codex execution, structured result validation, caching and rating provenance. It binds only to `127.0.0.1`, rejects unexpected Host/Origin headers, checks a per-process token for writes and renders provider text without HTML execution. It is a personal local tool, not a public multi-user server.

Original Selenium functions remain in `page_scraper.py` and `check_cancelled.py` for users maintaining that pipeline, but the new entry point starts the dashboard. They require the dependencies in `environment.yaml` and a Chrome setup. The legacy selectors still depend on LinkedIn's DOM and were not live-tested. New collection uses JobSpy instead.

Only LinkedIn is enabled in this version. The collector boundary is ready for future adapters; Indeed, Greenhouse and Lever are not yet implemented. There is no unattended scheduling, automatic job application submission or CAPTCHA bypass.

## Validation

```sh
python -m unittest discover -s tests -v
python -m compileall -q dashboard main.py page_scraper.py chatgpt_analysis.py cl_generator.py
node --check dashboard/static/app.js  # optional developer check; Node not needed to run
```

Tests exercise canonical deduplication, tracking preservation, atomic import validation, backup round trips, read-only legacy migration, collection failure persistence, interrupted runs, filter preservation, HTTP writes and CSRF/Host/Origin checks, CSV formula escaping, automatic collection-to-rating execution, cached ratings, profile/job invalidation, provider/model failures, cancellation and API-login rejection. See [AUDIT.md](AUDIT.md) for findings and remaining limits.
