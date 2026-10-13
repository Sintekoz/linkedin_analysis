"""Location evidence, separate from legal work-authorization decisions."""
import re

IRELAND = re.compile(r'\b(ireland|dublin|cork|galway|limerick|waterford|kilkenny|sligo|athlone|naas|kildare|wexford|drogheda|dundalk|bray|donegal|letterkenny|portlaoise|tralee)\b', re.I)
REGIONS = re.compile(r'\b(europe|european|emea|eu|worldwide|global|anywhere)\b', re.I)
REMOTE = re.compile(r'\bremote\b', re.I)


def classify_location(job):
    location = str(job.get('location') or '')
    remote = REMOTE.search(location+' '+str(job.get('work_model') or '')+' '+str(job.get('title') or ''))
    # Northern Ireland is a UK location. An Irish city must be named separately.
    republic = re.sub(r'\bnorthern\s+ireland\b', '', location, flags=re.I)
    if IRELAND.search(republic):
        return {'location_status': 'ireland', 'location_note': 'An Ireland location is advertised. Confirm the employer’s requirements and your current permission before applying.'}
    if remote and REGIONS.search(location):
        return {'location_status': 'remote_check', 'location_note': 'The advertised remote region may include Ireland. Confirm the employer can hire/payroll someone working from Ireland; regional wording is not confirmation.'}
    if not location.strip() or (remote and re.fullmatch(r'\s*(remote|distributed|unspecified|not provided)\s*', location, re.I)):
        return {'location_status': 'unclear', 'location_note': 'Permitted work locations are unclear. Ask whether this role can be performed and employed from Ireland.'}
    return {'location_status': 'outside_target', 'location_note': 'The advertised location does not establish an Ireland option. Irish work permission is not treated as permission to relocate or work in another country.'}


def in_scope(job, scope):
    return scope != 'ireland_remote' or classify_location(job)['location_status'] in ('ireland', 'remote_check')
