"""LinkedIn adapter using maintained JobSpy; no browser or account password required."""
import inspect
import json
import math
from datetime import date, datetime


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError('Search configuration must be an object.')
    config = {k: str(data.get(k) or '').strip() for k in ('keywords', 'location', 'job_type')}
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


def normalize(row):
    r = {k: clean(v) for k,v in row.items()}
    return dict(title=r.get('title'), company=r.get('company') or '', location=r.get('location') or '',
        url=r.get('job_url'), direct_url=r.get('job_url_direct') or '', description=r.get('description') or '',
        posted_date=r.get('date_posted') or '', job_type=r.get('job_type') or '',
        work_model='Remote' if r.get('is_remote') else (r.get('work_from_home_type') or ''),
        salary_min=r.get('min_amount'), salary_max=r.get('max_amount'), currency=r.get('currency') or '',
        salary_interval=r.get('interval') or '', industry=r.get('company_industry') or '',
        job_level=r.get('job_level') or '', job_function=r.get('job_function') or '',
        applicants=str(r.get('num_applicants') or ''), source='linkedin', availability='unknown',
        raw_json=json.dumps(r, default=str))


def collect(config):
    try:
        from jobspy import scrape_jobs
    except ImportError:
        raise RuntimeError('LinkedIn collection requires JobSpy. Install requirements-scraping.txt in your Python environment, then restart the dashboard.')
    args = dict(site_name=['linkedin'], search_term=config['keywords'], location=config['location'],
                results_wanted=config['limit'], hours_old=config['hours_old'], description_format='markdown', verbose=0)
    # JobSpy renamed this option; support both released signatures.
    key = 'fetch_description' if 'fetch_description' in inspect.signature(scrape_jobs).parameters else 'linkedin_fetch_description'
    args[key] = config['description']
    if config['remote']:
        args['is_remote'] = True
    if config['job_type']:
        args['job_type'] = config['job_type']
    frame = scrape_jobs(**args)
    for row in frame.to_dict(orient='records')[:config['limit']]:
        yield normalize(row)
