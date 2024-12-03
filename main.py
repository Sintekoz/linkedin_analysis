from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from check_cancelled import process_ongoing_jobs
from page_scraper import setup_database, scrape_linkedin_jobs
from chatgpt_analysis import analyze_jobs_with_chatgpt

# Input your LindkedIn job search URL and specify what you want ChatGPT to analyze
LINKEDIN_SEARCH_URL = 'https://www.linkedin.com/jobs/search/?currentJobId=4083475215&f_E=3%2C4%2C5&f_JT=F%2CO&geoId=104738515&keywords=Senior%20Analyst&origin=JOB_SEARCH_PAGE_JOB_FILTER&refresh=true&sortBy=R'
PROMPT_SUFFIX = (
        "Please score how well this position fits the user on a scale from 1 to 10, "
        "and provide the score only in the format 'X'. "
        "Keep in mind that the user would want to utilise his Python and SQL skills in that position. "
        "The ideal industry for him would be finance or fintech as he is good in finance."
    )

# Define the CV directory
CV_FOLDER = "./user_data"

def format_job_search_url(raw_url):
    """
    Formats a raw LinkedIn job search URL to a simplified template with placeholders.

    Parameters:
    - raw_url (str): The raw LinkedIn job search URL.

    Returns:
    - str: The formatted URL template with placeholders for pagination.
    """

    # Parse the raw URL
    parsed_url = urlparse(raw_url)
    query_params = parse_qs(parsed_url.query)

    # Keep only the necessary parameters: geoId and keywords
    filtered_params = {
        "geoId": query_params.get("geoId", [""])[0],
        "keywords": query_params.get("keywords", [""])[0],
    }

    # Build the new query string
    new_query = urlencode(filtered_params)

    # Construct the new URL
    formatted_url = urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        "",
        new_query,
        "",
    ))

    return formatted_url

if __name__ == "__main__":
    print("Connecting to the database...")
    setup_database()

    print("Checking if the existing positions in the database are still ongoing")
    process_ongoing_jobs()

    print("Starting scrapping the new positions")
    scrape_linkedin_jobs(format_job_search_url(LINKEDIN_SEARCH_URL))

    print("Analyzing")
    analyze_jobs_with_chatgpt(PROMPT_SUFFIX)

    print("Done")
