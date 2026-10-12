"""Start the local dashboard. The original Selenium pipeline remains optional."""
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

LINKEDIN_SEARCH_URL = 'https://www.linkedin.com/jobs/search/?keywords=Senior%20Analyst&geoId=104738515'
PROMPT_SUFFIX = 'Score the fit from 1 to 10, with Python, SQL and finance experience in mind.'

def format_job_search_url(raw_url):
    """Keep search filters, removing only transient navigation and pagination fields."""
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    for key in ('currentJobId', 'start', 'origin', 'refresh'):
        query.pop(key, None)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', urlencode(query, doseq=True), ''))

if __name__ == '__main__':
    from dashboard.server import main
    main()
