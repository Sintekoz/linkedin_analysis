# LinkedIn Analysis · Next Role

A local HTML workspace for LinkedIn jobs and your application pipeline. The website is served by Python and stores your data in SQLite on your computer. No frontend build, Node runtime, LinkedIn password or OpenAI key is required to browse and manage jobs.

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

Create a saved search under **LinkedIn searches**, choose keywords, location, posting age, job type, remote-only filtering, result limit and whether to fetch descriptions, then click **Run search**. Begin with 25 results. Only one collection runs at a time. Run history displays state, result counts and failures; **Stop** terminates the collector process and retains committed jobs. Collections are capped at ten minutes. Search coverage and fields depend on LinkedIn and JobSpy; upstream blocking can produce zero results without a raised error.

## What you can manage

- **Overview:** jobs, application counts, interviews, follow-ups due, recent roles and collection activity.
- **Job library:** search descriptions, titles, companies and locations; filter by stage or remote work; sort by date, company, follow-up or AI score.
- **Applications:** saved → applied → interview → offer / rejected. Open a card to change stages. Discovered and withdrawn jobs remain in the library.
- **Job details:** edit metadata, inspect descriptions, store contact information, notes, application dates and follow-ups, and view activity. Opening a posting does not submit an application or change its stage.
- **LinkedIn searches:** create, edit, run and remove saved searches, with persistent run history.
- **Profile:** target role, skills, CV text and fit preferences. Skills are literal text coverage, not an AI suitability score.
- **Portability:** export applications as CSV or jobs as JSON, and import JSON from the Profile page. JSON backups include application tracking, AI results and cover letters. Importing matching jobs updates any tracking fields present in the import.

The UI starts empty. Missing salary, descriptions, applicant counts and work models are never fabricated. Collection refreshes deduplicate by canonical posting URL and preserve your application tracking. Posting availability is separate from application stage: missing search results never imply closure. You can set availability manually in Edit details; automated availability verification is not implemented.

## Optional AI

```sh
python -m pip install -r requirements-ai.txt
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env` and optionally `OPENAI_MODEL` (default `gpt-4o-mini`). Paste your CV text under **Profile**. A job's **Analyze fit** action stores a structured 1–10 review with strengths and gaps. **Draft cover letter** stores a text draft downloadable as `.txt`. These explicit actions send your CV and the chosen job description to OpenAI and can incur API usage charges. Review generated content before using it. Core features remain available without a key; no automatic AI batch runs occur.

The separate legacy `cl_generator.py JOB_ID` still generates a Word letter from a PDF CV when its legacy dependencies are installed. It requires an original database. The dashboard's AI workflow uses profile text, not the legacy PDF directory.

## Database & backups

Default database: `data/job_search.db`. Its location is anchored to this checkout. Override with `--db /absolute/path/workspace.db`.

For a complete backup of profile, searches, activity and jobs, **stop the dashboard and copy the SQLite database**. JSON export is a job backup, not a full workspace backup. Data files, `.env`, CVs and generated letters are ignored by Git.

An original database can be imported read-only and repeatedly:

```sh
python main.py --legacy-db /absolute/path/linkedin_jobs.db --port 8876
```

The original file is not modified; application tracking in the new database is retained on repeated legacy imports.

## Scope and architecture

`dashboard/static/` is buildless HTML/CSS/JavaScript. `dashboard/server.py` exposes a small local API. `dashboard/store.py` owns persistence, `dashboard/collectors.py` normalizes JobSpy LinkedIn data, and `dashboard/worker.py` runs collection in an isolated process. It binds only to `127.0.0.1`, rejects unexpected Host/Origin headers, checks a per-process token for writes and renders provider text without HTML execution. It is a personal local tool, not a public multi-user server.

Original Selenium functions remain in `page_scraper.py` and `check_cancelled.py` for users maintaining that pipeline, but the new entry point starts the dashboard. They require the dependencies in `environment.yaml` and a Chrome setup. The legacy selectors still depend on LinkedIn's DOM and were not live-tested. New collection uses JobSpy instead.

Only LinkedIn is enabled in this version. The collector boundary is ready for future adapters; Indeed, Greenhouse and Lever are not yet implemented. There is no unattended scheduling, automatic job application submission or CAPTCHA bypass.

## Validation

```sh
python -m unittest discover -s tests -v
python -m compileall -q dashboard main.py page_scraper.py chatgpt_analysis.py cl_generator.py
node --check dashboard/static/app.js  # optional developer check; Node not needed to run
```

Tests exercise canonical deduplication, tracking preservation, atomic import validation, backup round trips, read-only legacy migration, collection failure persistence, interrupted runs, filter preservation, HTTP writes and CSRF/Host/Origin checks, and CSV formula escaping. See [AUDIT.md](AUDIT.md) for findings and remaining limits.
