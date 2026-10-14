"""Normalize advertised geography without deciding legal work eligibility."""
import re

IRELAND = re.compile(r'\b(ireland|dublin|cork|galway|limerick|waterford|kilkenny|sligo|athlone|naas|kildare|wexford|drogheda|dundalk|bray|donegal|letterkenny|portlaoise|tralee)\b', re.I)
REGIONS = re.compile(r'\b(europe|european|emea|eu|worldwide|global|anywhere)\b', re.I)
REMOTE = re.compile(r'\b(remote|wfh|work from home)\b', re.I)
FOREIGN = re.compile(r'\b(united states|usa|us|canada|united kingdom|uk|england|scotland|wales|ohio|california|ontario)\b', re.I)
COUNTRIES = {
    'United Kingdom': r'\b(united kingdom|uk|england|scotland|wales|northern ireland)\b',
    'United States': r'\b(united states|usa|us|ohio|california)\b',
    'Canada': r'\b(canada|ontario)\b', 'Germany': r'\b(germany|deutschland)\b',
    'France': r'\bfrance\b', 'Netherlands': r'\bnetherlands\b', 'Spain': r'\bspain\b',
    'Poland': r'\bpoland\b', 'Portugal': r'\bportugal\b', 'Italy': r'\bitaly\b',
    'Australia': r'\baustralia\b',
}
COUNTY_CODES = {'D':'Dublin', 'C':'Cork', 'G':'Galway', 'LK':'Limerick', 'WD':'Waterford',
                'KE':'Kildare', 'WW':'Wicklow', 'DL':'Donegal', 'CN':'Cavan', 'CE':'Clare'}
DUBLIN_LOCALITIES = re.compile(r'\b(swords|tallaght|sandyford|blanchardstown|clondalkin|rathfarnham|'
                              r'dun laoghaire|dún laoghaire|balbriggan|malahide|portmarnock|'
                              r'donabate|skerries|howth|lucan|dundrum)\b', re.I)


def normalize_location(job):
    raw = str(job.get('location') or '').strip()
    model = str(job.get('work_model') or '')
    title = str(job.get('title') or '')
    def remote_word(value):
        return bool(REMOTE.search(value) and not re.search(r'\b(?:not|no)\s+remote\b', value, re.I))
    title_remote = bool(re.search(r'(?:\(|\[|[-–—:]\s*)remote\b|\bremote\s*[-–—:]|\bremote\s*$|^remote\b(?!\s+(?:sensing|control|desktop|access)\b)', title, re.I))
    remote = remote_word(raw) or remote_word(model) or (title_remote and not re.search(r'on.?site|hybrid',model,re.I) and not re.search(r'\b(?:not|no)\s+remote\b',title,re.I))
    if re.search(r'\bhybrid\b|on.?site|\b(?:not|no)\s+remote\b',model,re.I):
        remote = False
    parts = [s.strip() for s in re.split(r';|\s+\|\s+', raw) if s.strip()]
    places = []
    for part in parts:
        republic = re.sub(r'\bnorthern\s+ireland\b', '', part, flags=re.I)
        country = next((name for name,pattern in COUNTRIES.items() if re.search(pattern,part,re.I)), '')
        if not country and re.search(r',\s*(?:OH|CA)\s*(?:,|$)',part,re.I):
            country = 'United States'
        ireland = not country and bool(re.search(r'\b(ireland|éire|eire|ie)\b',republic,re.I) or IRELAND.search(republic) or DUBLIN_LOCALITIES.search(republic))
        if ireland:
            country = 'Ireland'
        tokens = [s.strip() for s in part.split(',') if s.strip()]
        locality = re.sub(r'\b(remote|wfh|work from home)\b', '', tokens[0], flags=re.I).strip(' -()') if tokens else ''
        city = ''
        if country == 'Ireland':
            county_dublin = bool(re.search(r'\bdublin\b',republic,re.I) or DUBLIN_LOCALITIES.search(republic) or any(t.upper()=='D' for t in tokens))
            if county_dublin:
                city = 'Dublin'
                # District numbers and repeated county codes do not create new cities.
                locality = '' if re.search(r'\bdublin\b',locality,re.I) or locality.upper()=='D' else locality.title()
            elif locality.upper() in COUNTY_CODES:
                city = COUNTY_CODES[locality.upper()]
                locality = 'County '+city
            elif locality and not re.fullmatch(r'ireland|éire|eire|ie|emea|europe|eu',locality,re.I):
                city = re.sub(r'^(?:co\.?|county)\s+', '', locality, flags=re.I).title()
                locality = ''
        else:
            if locality and not re.fullmatch(r'unspecified|distributed|not provided',locality,re.I) and not REGIONS.search(locality) and not any(re.search(p,locality,re.I) for p in COUNTRIES.values()):
                city = locality.title()
            locality = ''
        if country == 'Ireland':
            display = locality+', '+country if locality=='County '+city else ', '.join(dict.fromkeys(p for p in (locality, city, country) if p))
        elif country:
            display = ', '.join(p for p in (city, country) if p)
        else:
            display = re.sub(r'\s+', ' ', part).strip()
        places.append({'city':city,'country':country,'display':display})
    ireland_places = [p for p in places if p['country']=='Ireland']
    foreign_places = [p for p in places if p['country'] and p['country']!='Ireland']
    region = ('Global' if re.search(r'\b(worldwide|global|anywhere)\b',raw,re.I) else
              'EMEA' if re.search(r'\bemea\b',raw,re.I) else 'Europe' if REGIONS.search(raw) else '')
    if not remote:
        remote_scope = ''
    elif ireland_places:
        remote_scope = 'ireland'
    elif foreign_places:
        # A country restriction wins over a vague Europe/EMEA suffix.
        remote_scope = 'outside_target'
    elif region:
        remote_scope = 'check'
    elif not raw or re.fullmatch(r'\s*(remote|wfh|work from home|distributed|unspecified|not provided)\s*',raw,re.I):
        remote_scope = 'unclear'
    else:
        remote_scope = 'outside_target'
    dublin = any(p['city']=='Dublin' and p['country']=='Ireland' for p in places)
    ordered = ireland_places + [p for p in places if p not in ireland_places]
    display = '; '.join(dict.fromkeys(p['display'] for p in ordered if p['display']))
    if remote and not any(p['city'] for p in places):
        display = 'Remote · '+(ordered[0]['country'] if ordered and ordered[0]['country'] else region or 'location unspecified')
    return {'location_display':display or 'Location not provided',
            'location_city':ordered[0]['city'] if ordered else '',
            'location_country':ordered[0]['country'] if ordered else '',
            'location_is_dublin':dublin, 'is_remote':bool(remote), 'remote_scope':remote_scope,
            'dublin_remote':dublin or remote_scope in ('ireland','check','unclear')}


def classify_location(job, normalized=None):
    geo = normalized if normalized is not None else normalize_location(job)
    if geo['location_country']=='Ireland':
        return {'location_status':'ireland', 'location_note':'An Ireland location is advertised. Confirm the employer’s requirements and your current permission before applying.'}
    if geo['remote_scope']=='check':
        return {'location_status':'remote_check', 'location_note':'The advertised remote region may include Ireland. Confirm the employer can hire/payroll someone working from Ireland; regional wording is not confirmation.'}
    if not str(job.get('location') or '').strip() or geo['remote_scope']=='unclear':
        return {'location_status':'unclear', 'location_note':'Permitted work locations are unclear. Ask whether this role can be performed and employed from Ireland.'}
    return {'location_status':'outside_target', 'location_note':'The advertised location does not establish an Ireland option. Irish work permission is not treated as permission to relocate or work in another country.'}


def in_scope(job, scope):
    return scope != 'ireland_remote' or classify_location(job)['location_status'] in ('ireland','remote_check')
