"""JobSpy job boards and public employer-feed adapters."""
import inspect
import json
import math
import re
import logging
from datetime import date, datetime
from .ats import collect_ats
from urllib.parse import urlsplit, parse_qs

PROVIDERS = {'linkedin': 'LinkedIn', 'indeed': 'Indeed', 'glassdoor': 'Glassdoor',
             'greenhouse': 'Greenhouse', 'lever': 'Lever'}


class ProviderMessages(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        if len(self.messages) < 100:
            self.messages.append(record.getMessage()[:1000])


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
    try:
        config['limit'] = int(data.get('limit', 25))
        config['hours_old'] = int(data.get('hours_old', 168))
    except (ValueError, TypeError):
        raise ValueError('Result limit and age must be numbers.')
    if not 1 <= config['limit'] <= 100 or not 1 <= config['hours_old'] <= 2160:
        raise ValueError('Use 1–100 results and a posting age of 1–2160 hours.')
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


def collect(config):
    config = validate_config(config)
    if config['provider'] in ('greenhouse', 'lever'):
        yield from collect_ats(config)
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
                results_wanted=config['limit'], hours_old=config['hours_old'], description_format='markdown', verbose=1)
    # JobSpy renamed this option; support both released signatures.
    key = 'fetch_description' if 'fetch_description' in inspect.signature(scrape_jobs).parameters else 'linkedin_fetch_description'
    args[key] = config['description']
    if config['provider'] in ('indeed', 'glassdoor'):
        args['country_indeed'] = config['country']
    if config['remote']:
        args['is_remote'] = True
    if config['job_type']:
        args['job_type'] = config['job_type']
    logger = logging.getLogger('JobSpy:'+PROVIDERS[config['provider']])
    messages = ProviderMessages()
    logger.addHandler(messages)
    try:
        frame = scrape_jobs(**args)
    finally:
        logger.removeHandler(messages)
    for row in frame.to_dict(orient='records')[:config['limit']]:
        job = normalize(row, config['provider'])
        if config['provider'] == 'linkedin':
            if config['remote']:
                if not re.search(r'\bremote\b', job['work_model']+' '+str(job['title']), re.I):
                    continue
                if not job['work_model']:
                    job['work_model'] = 'Remote'
            if config['job_type'] and config['job_type'] not in re.sub(r'[^a-z]', '', job['job_type'].lower()):
                continue
        yield job
    if any(re.search(r'\b(?:403|429)\b', m) and 'status' in m.lower() or 'blocked' in m.lower() for m in messages.messages):
        raise RuntimeError(PROVIDERS[config['provider']]+' denied or rate-limited one or more requests. Any collected jobs are retained; retry after a break.')
