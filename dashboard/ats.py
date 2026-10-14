"""Public employer feeds on Greenhouse and Lever. Read-only GET requests."""
import html
import json
import re
import ssl
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from .locations import in_scope
from . import network


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        elif not self.skip:
            if tag in ('p', 'div', 'br', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'li'):
                self.parts.append('\n' + ('- ' if tag == 'li' else ''))

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip-1)
        elif not self.skip and tag in ('p', 'div', 'li'):
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)


def text_html(value):
    value = str(value or '')
    # Greenhouse content may contain an additional level of HTML encoding.
    for _ in range(2):
        if re.search(r'&(?:amp;)?lt;/?(?:p|div|br|li|ul|h\d)\b', value, re.I):
            value = html.unescape(value)
    parser = TextParser()
    parser.feed(value)
    return re.sub(r'\n\s*\n(?:\s*\n)+', '\n\n', ''.join(parser.parts)).strip()


def read_json(url):
    code,final,body=network.fetch(url)
    if code!=200 or final!=url:raise RuntimeError('Employer feed returned HTTP '+str(code)+'. Check the board name or try later.')
    return json.loads(body)


def greenhouse_job(row, config):
    location = (row.get('location') or {}).get('name', '')
    offices = [(o.get('location') or o.get('name') or '') for o in row.get('offices', [])]
    places = [p.strip() for p in location.split(';') if p.strip()]
    for office in offices:
        if office and not any(p.split(',')[0].casefold() == office.split(',')[0].casefold() for p in places):
            places.append(office)
    location = '; '.join(dict.fromkeys(places))
    return {'id': 'greenhouse:'+config['board']+':'+str(row['id']), 'title': row.get('title'),
            'company': row.get('company_name') or config['company'], 'location': location,
            'description': text_html(row.get('content')), 'url': row.get('absolute_url'),
            'direct_url': row.get('absolute_url'), 'posted_date': row.get('first_published') or '',
            'work_model': 'Remote' if re.search(r'\bremote\b', location, re.I) else ('Hybrid' if re.search(r'\bhybrid\b', location, re.I) else ''),
            'job_function': ', '.join(d['name'] for d in row.get('departments', []) if d.get('name')),
            'source': 'greenhouse', 'availability': 'ongoing',
            'raw_json': json.dumps({'board': config['board'], 'payload': row}, ensure_ascii=False)}


def lever_job(row, config):
    categories = row.get('categories') or {}
    locations = categories.get('allLocations') or [categories.get('location') or '']
    if not isinstance(locations, list):
        locations = [str(locations)]
    parts = [row.get('descriptionPlain') or text_html(row.get('description'))]
    for section in row.get('lists', []):
        parts.extend([section.get('text') or '', text_html(section.get('content'))])
    parts.append(row.get('additionalPlain') or text_html(row.get('additional')))
    salary = row.get('salaryRange') or {}
    work_model = {'remote': 'Remote', 'hybrid': 'Hybrid', 'on-site': 'On-site'}.get(row.get('workplaceType'), '')
    return {'id': 'lever:'+config['board']+':'+str(row['id']), 'title': row.get('text'), 'company': config['company'],
            'location': '; '.join(dict.fromkeys(locations)), 'description': '\n\n'.join(p for p in parts if p),
            'url': row.get('hostedUrl'), 'direct_url': row.get('applyUrl') or row.get('hostedUrl'),
            # Lever's documented feed does not guarantee an original posting date.
            'posted_date': '', 'work_model': work_model, 'job_type': categories.get('commitment') or '',
            'job_function': categories.get('team') or categories.get('department') or '',
            'salary_min': salary.get('min'), 'salary_max': salary.get('max'),
            'currency': salary.get('currency') or '', 'salary_interval': salary.get('interval') or '',
            'source': 'lever', 'availability': 'ongoing',
            'raw_json': json.dumps({'board': config['board'], 'payload': row}, ensure_ascii=False)}


def matches(job, config):
    terms = [s.strip().casefold() for s in config['keywords'].split('|') if s.strip()]
    if not any(term in str(job.get('title') or '').casefold() for term in terms):
        return False
    if not in_scope(job, config['scope']):
        return False
    if config['scope'] == 'all' and config['location'].casefold() not in str(job['location']).casefold():
        return False
    if config['remote'] and job['work_model'] != 'Remote':
        return False
    if config['job_type']:
        actual = re.sub(r'[^a-z]', '', str(job.get('job_type') or '').lower())
        if config['job_type'] not in actual:
            return False
    return True


def collect_ats(config, progress=None, checkpoint=None, save_checkpoint=None, page_budget=None):
    checkpoint=checkpoint or {}
    found,scanned,pages=(checkpoint.get(k,0) for k in ('found','scanned','pages'))
    seen=set(checkpoint.get('seen',[]))
    def save(offset,complete):
        if save_checkpoint:save_checkpoint(dict(offset=offset,found=found,scanned=scanned,pages=pages,seen=list(seen),complete=complete))
    def report(note):
        if progress:
            progress(pages, scanned, note)
    if config['provider'] == 'greenhouse':
        host = 'boards-api.greenhouse.io'
        report('Reading all published roles from the employer feed…')
        payload = read_json('https://'+host+'/v1/boards/'+quote(config['board'], safe='')+'/jobs?content=true')
        if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
            raise RuntimeError('Greenhouse returned an unexpected feed format.')
        rows = sorted(payload['jobs'], key=lambda r: str(r.get('updated_at') or ''), reverse=True)
        pages, scanned = 1, len(rows)
        report(f'Checking {scanned} published employer postings…')
        for row in rows:
            job = greenhouse_job(row, config)
            if job['id'] not in seen and matches(job, config):
                seen.add(job['id'])
                yield job
                found += 1
                if config['limit'] is not None and found >= config['limit']:
                    save(0,True)
                    report('Reached your optional result limit.')
                    return
        report('Checked the complete published employer feed.')
        save(0,True)
    else:
        host = 'api.eu.lever.co' if config['region'] == 'eu' else 'api.lever.co'
        skip = checkpoint.get('offset',0)
        starting_pages=pages
        while True:
            report(f'Reading employer postings from position {skip + 1}…')
            query = {'mode': 'json', 'limit': 100, 'skip': skip}
            if config.get('board_location'):
                query['location'] = config['board_location']
            rows = read_json('https://'+host+'/v0/postings/'+quote(config['board'], safe='')+'?'+urlencode(query))
            if not isinstance(rows, list):
                raise RuntimeError('Lever returned an unexpected feed format.')
            pages += 1
            scanned += len(rows)
            report(f'Checking employer batch {pages}; {scanned} postings scanned…')
            new = 0
            for row in rows:
                job = lever_job(row, config)
                if job['id'] in seen:
                    continue
                seen.add(job['id'])
                new += 1
                if matches(job, config):
                    yield job
                    found += 1
                    if config['limit'] is not None and found >= config['limit']:
                        save(skip+100,True)
                        report('Reached your optional result limit.')
                        return
            if len(rows) < 100:
                save(skip+100,True)
                report('Checked all postings exposed by the employer feed.')
                return
            if not new:
                raise RuntimeError('Employer feed repeated the same page. Stopped to avoid an endless loop; saved jobs are retained.')
            skip += 100
            save(skip,False)
            if page_budget and pages-starting_pages>=page_budget:
                report('Page saved. Other searches will get a turn before this feed continues.')
                return
