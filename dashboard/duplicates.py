"""Conservative posting identity; shared titles are never proof of duplication."""
import re
from collections import defaultdict
from urllib.parse import urlsplit, parse_qs


def posting_link(url):
    """Only an individual posting link can identify cross-platform duplicates."""
    if not url:
        return False
    link = urlsplit(url)
    query = parse_qs(link.query)
    if any(query.get(k) for k in ('jk', 'jl', 'jobListingId', 'gh_jid', 'jobId', 'jobid', 'reqId', 'requisitionId')):
        return True
    if re.search(r'/(?:search|categories|category|departments|department|locations|location|teams|team)(?:/|$)', link.path, re.I):
        return False
    # Require a job/apply path plus a concrete identifier. Career homepages and
    # common search/category links must never collapse unrelated vacancies.
    if link.hostname in ('boards.greenhouse.io','job-boards.greenhouse.io'):
        return bool(re.search(r'/jobs/\d+/?$',link.path))
    if link.hostname in ('jobs.lever.co','jobs.eu.lever.co'):
        return bool(re.search(r'/[0-9a-f-]{36}(?:/apply)?/?$',link.path,re.I))
    match = re.search(r'/(?:jobs?|postings?|requisitions?|positions?|careers?)/(?:[^/]+/)*([^/]+?)(?:/apply)?/?$', link.path, re.I)
    return bool(match and len(match[1])>=6 and re.search(r'\d',match[1]))


def similar_groups(jobs):
    groups = defaultdict(list)
    for job in jobs:
        def text(value):
            return re.sub(r'[^\w]+', ' ', str(value or '').casefold()).strip()
        key = tuple(text(job.get(k)) for k in ('company', 'title', 'location'))
        if all(key):
            groups[key].append(job['id'])
    return {jid: [other for other in ids if other != jid]
            for ids in groups.values() if len(ids)>1 for jid in ids}
