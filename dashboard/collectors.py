"""JobSpy job boards and public employer-feed adapters."""
import inspect
import json
import math
import re
import logging
import time
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime
from .ats import collect_ats
from urllib.parse import urlsplit, parse_qs
from . import network

PROVIDERS = {'linkedin': 'LinkedIn', 'indeed': 'Indeed', 'glassdoor': 'Glassdoor',
             'greenhouse': 'Greenhouse', 'lever': 'Lever'}


class ProviderBlocked(RuntimeError):
    """Stop this platform's workflow requests after an explicit denial."""


class ProviderMessages(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = deque(maxlen=100)

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            self.messages.append((record.levelno, record.getMessage()[:1000]))



@contextmanager
def description_guard(messages, enabled):
    """Pace optional detail requests and stop them on the first explicit denial.

    JobSpy 1.x performs these inside scrape_jobs. The worker is isolated and uses
    only one board, so the temporary adapter wrapper is confined to this call.
    """
    if not enabled:
        yield;return
    try:
        from jobspy.linkedin import LinkedIn
        original=LinkedIn._fetch_details
    except (ImportError,AttributeError):
        yield;return
    denied=False;last_request=None
    def fetch_details(adapter,job_id):
        nonlocal denied,last_request
        if denied:return {}
        if last_request is not None:
            pause=2-(time.monotonic()-last_request)
            if pause>0:time.sleep(pause)
        last_request=time.monotonic();before=len(messages.messages)
        value=original(adapter,job_id)
        recent=list(messages.messages)[before:]
        denied=any(re.search(r'\b(?:401|403|429|999)\b',m) for _,m in recent)
        return value
    LinkedIn._fetch_details=fetch_details
    try:yield
    finally:LinkedIn._fetch_details=original


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError('Search configuration must be an object.')
    config = {k: str(data.get(k) or '').strip() for k in ('keywords', 'location', 'job_type')}
    config['provider'] = str(data.get('provider') or 'linkedin').lower()
    if config['provider'] not in PROVIDERS:
        raise ValueError('Choose a supported job platform.')
    if not config['keywords'] or not config['location']:
        raise ValueError('Keywords and location are required.')
    if config['job_type'] not in ('', 'fulltime', 'parttime', 'contract', 'internship'):
        raise ValueError('Unsupported job type.')
    for field in ('limit', 'hours_old'):
        value = data.get(field)
        if value is None or value == '':
            config[field] = None  # No user-selected result cap or date filter.
        else:
            if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)) or int(value) < 1:
                raise ValueError('Optional result limit and posting age must be positive whole numbers, or blank for all.')
            config[field] = int(value)
    config['remote'] = bool(data.get('remote', False))
    config['description'] = bool(data.get('description', True))
    config['country'] = str(data.get('country') or 'Ireland').strip()[:60]
    config['scope'] = str(data.get('scope') or 'all')
    if config['scope'] not in ('all', 'ireland_remote'):
        raise ValueError('Unknown location scope.')
    config['rationale'] = str(data.get('rationale') or '').strip()[:800]
    if config['provider'] in ('greenhouse', 'lever'):
        config['board'] = str(data.get('board') or '').strip()
        config['company'] = str(data.get('company') or '').strip()[:150]
        config['region'] = str(data.get('region') or 'global')
        if '://' in config['board']:
            link = urlsplit(config['board'])
            valid_hosts = {'greenhouse': ('boards.greenhouse.io', 'job-boards.greenhouse.io'),
                           'lever': ('jobs.lever.co', 'jobs.eu.lever.co')}
            if link.scheme != 'https' or link.hostname not in valid_hosts[config['provider']] or link.username or link.password:
                raise ValueError('Use the employer’s official Greenhouse or Lever careers link, or its board name.')
            if link.hostname == 'jobs.eu.lever.co':
                config['region'] = 'eu'
            config['board'] = parse_qs(link.query).get('for', [link.path.strip('/').split('/')[0]])[0]
        config['board_location'] = str(data.get('board_location') or '').strip()[:200]
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', config['board']) or not config['company']:
            raise ValueError('Employer feeds need a valid board name and employer name.')
        if config['region'] not in ('global', 'eu'):
            raise ValueError('Choose the global or EU employer-feed region.')
        if config['provider'] == 'greenhouse' and config['region'] != 'global':
            raise ValueError('This version supports the global Greenhouse API; use a global board link.')
        config['description'] = True
    return config


def clean(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, 'item'):
        return clean(value.item())
    if isinstance(value, (list, tuple)):
        return [clean(x) for x in value]
    if isinstance(value, dict):
        return {str(k): clean(v) for k,v in value.items()}
    return value


def normalize(row, provider='linkedin'):
    r = {k: clean(v) for k,v in row.items()}
    return dict(title=r.get('title'), company=r.get('company') or '', location=r.get('location') or '',
        url=r.get('job_url'), direct_url=r.get('job_url_direct') or '', description=r.get('description') or '',
        posted_date=r.get('date_posted') or '', job_type=r.get('job_type') or '',
        work_model='Remote' if r.get('is_remote') else (r.get('work_from_home_type') or ''),
        salary_min=r.get('min_amount'), salary_max=r.get('max_amount'), currency=r.get('currency') or '',
        salary_interval=r.get('interval') or '', industry=r.get('company_industry') or '',
        job_level=r.get('job_level') or '', job_function=r.get('job_function') or '',
        applicants=str(r.get('num_applicants') or ''), source=str(r.get('site') or provider), availability='unknown',
        raw_json=json.dumps(r, default=str))


def collect(config, progress=None, checkpoint=None, save_checkpoint=None, page_budget=None):
    config = validate_config(config)
    if config['provider'] in ('greenhouse', 'lever'):
        yield from collect_ats(config, progress, checkpoint, save_checkpoint, page_budget)
        return
    try:
        from jobspy import scrape_jobs
    except ImportError:
        raise RuntimeError('Job-board collection requires JobSpy. Install requirements-scraping.txt in your Python environment, then restart the dashboard.')
    query = config['keywords']
    # Current JobSpy LinkedIn ignores its remote/type parameters. Search the term
    # and enforce the explicit returned metadata locally rather than promising a filter.
    if config['provider'] == 'linkedin' and config['remote'] and 'remote' not in query.lower():
        query += ' remote'
    args = dict(site_name=[config['provider']], search_term=query, location=config['location'],
                hours_old=config['hours_old'], description_format='markdown', verbose=1)
    # JobSpy renamed this option; support both released signatures.
    key = 'fetch_description' if 'fetch_description' in inspect.signature(scrape_jobs).parameters else 'linkedin_fetch_description'
    args[key] = config['description'] and not page_budget
    if config['provider'] in ('indeed', 'glassdoor'):
        args['country_indeed'] = config['country']
    if config['remote']:
        args['is_remote'] = True
    if config['job_type']:
        args['job_type'] = config['job_type']
    logger = logging.getLogger('JobSpy:'+PROVIDERS[config['provider']])
    # LinkedIn's installed adapter advances by ten native cards per page. Larger
    # requests hide intermediate saves and may skip cards when stopped mid-page.
    page_size = 10 if config['provider'] == 'linkedin' else 100
    checkpoint = checkpoint or {}
    seen = set(checkpoint.get('seen', []))
    description_failures=checkpoint.get('description_failures',0)
    description_deferred=bool(checkpoint.get('description_deferred',False))
    if description_deferred:args[key]=False
    offset, found, scanned, pages = (checkpoint.get(k, 0) for k in ('offset','found','scanned','pages'))
    starting_pages=pages
    def report(note):
        if progress:
            suffix=f' {description_failures} description pages unavailable; existing descriptions are retained and listings continue.' if description_failures else ''
            progress(pages, scanned, note+suffix)
    while True:
        report(f"Requesting {PROVIDERS[config['provider']]} results from position {offset + 1}…")
        messages = ProviderMessages()
        logger.addHandler(messages)
        network.event('search_request',config['provider']+':search')
        try:
            with description_guard(messages,config['provider']=='linkedin' and args[key]):
                frame = scrape_jobs(**args, results_wanted=page_size, offset=offset)
        except Exception:
            network.event('search_failed',config['provider']+':search')
            raise
        finally:
            logger.removeHandler(messages)
        rows = frame.to_dict(orient='records')
        pages += 1
        scanned += len(rows)
        report(f'Received batch {pages}; checking and saving {len(rows)} returned jobs…')
        new = 0
        for row in rows:
            identity = str(row.get('job_url') or row.get('id') or json.dumps(clean(row), sort_keys=True, default=str))
            if identity in seen:
                continue
            seen.add(identity)
            new += 1
            job = normalize(row, config['provider'])
            if config['provider'] == 'linkedin' and not page_budget:
                if config['remote']:
                    if not re.search(r'\bremote\b', job['work_model']+' '+str(job['title']), re.I):
                        continue
                    if not job['work_model']:
                        job['work_model'] = 'Remote'
                if config['job_type'] and config['job_type'] not in re.sub(r'[^a-z]', '', job['job_type'].lower()):
                    continue
            if config['limit'] is None or found < config['limit']:
                yield job
                found += 1
        # A description endpoint failure does not invalidate returned search cards.
        details=[m for _,m in messages.messages if config['provider']=='linkedin' and
                 re.search(r'(?:for job \d+|LinkedIn: job \d+:)',m,re.I)]
        search_messages=[(level,m) for level,m in messages.messages if m not in details]
        errors=[m for level,m in search_messages if level>=logging.ERROR]
        blocked=[m for _,m in search_messages if
                 (re.search(r'\b(?:401|403|429|999)\b',m) and 'status' in m.lower()) or 'blocked' in m.lower()]
        if details:
            description_failures+=len(details)
            report(f'{len(details)} description requests could not finish. Search results were saved; missing descriptions remain visible for later enrichment.')
            if any(re.search(r'\b(?:401|403|429|999)\b',m) for m in details):
                args[key]=False  # Respect the denial; do not keep hitting detail pages.
                description_deferred=True
        # All yielded rows are now saved. Failed searches retry this same offset;
        # successful pages advance even when description fetching was denied.
        if save_checkpoint:
            complete=not errors and not blocked and (not rows or not new or len(rows)<page_size or config['limit'] is not None and found>=config['limit'])
            save_checkpoint({'offset':offset if errors or blocked else offset+page_size,
                             'found':found,'scanned':scanned,'pages':pages,'seen':list(seen),
                             'description_failures':description_failures,'description_deferred':description_deferred,'complete':complete})
        if blocked:
            network.event('search_blocked',config['provider']+':search')
            raise ProviderBlocked(PROVIDERS[config['provider']]+' search denied or rate-limited: '+blocked[0]+'. Saved jobs are retained; other platforms and posting checks can continue.')
        if errors:
            network.event('search_failed',config['provider']+':search')
            raise RuntimeError(PROVIDERS[config['provider']]+' could not finish search collection: '+errors[0])
        network.event('search_success',config['provider']+':search')
        if config['limit'] is not None and found >= config['limit']:
            report('Reached your optional result limit.')
            return
        if not rows:
            report('No further results returned. This can mean the end of accessible results or provider throttling; coverage is not guaranteed.')
            return
        if not new:
            report('The source repeated previously returned results. Stopped to avoid an endless loop; coverage is not guaranteed.')
            return
        if len(rows) < page_size:
            report('The source returned a final partial batch. No further results were exposed; coverage is not guaranteed.')
            return
        offset += page_size
        if page_budget and pages-starting_pages>=page_budget:
            report('Page saved. Other searches will get a turn before this search continues.')
            return
        report('Saved this batch; waiting briefly before requesting more results…')
        time.sleep(30 if config['provider']=='linkedin' else 5)
