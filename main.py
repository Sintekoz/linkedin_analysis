import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from page_scraper import scrape_linkedin_jobs
from clean_data import update_positions_with_timestamp, get_most_recent_csv
from chatgpt_analysis import analyze_jobs_with_chatgpt

# Input your LindkedIn job search URL and specify what you want ChatGPT to analyze
LINKEDIN_SEARCH_URL = 'https://www.linkedin.com/jobs/search/?currentJobId=4070766249&distance=25&geoId=104738515&keywords=senior%20analyst&origin=JOBS_HOME_KEYWORD_HISTORY&refresh=true'
PROMPT_SUFFIX = (
        "Please score how well this position fits the user on a scale from 1 to 10, "
        "and provide the score only in the format 'X'. "
        "Keep in mind that the user would want to utilise his Python and SQL skills in that position. "
        "The ideal industry for him would be finance or fintech as he is good in finance."
    )

# Define directories
RAW_DATA_FOLDER = "./data/raw_data"
CLEAN_DATA_FOLDER = "./data/clean_data"
OUTPUT_FOLDER = "./output"
CV_FOLDER = "./user_data"

def create_directories():
    """Create necessary directories if they don't exist."""
    try:
        os.makedirs(RAW_DATA_FOLDER, exist_ok=True)
        os.makedirs(CLEAN_DATA_FOLDER, exist_ok=True)
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        os.makedirs(CV_FOLDER, exist_ok=True)
        print("Directories created or verified.")
    except Exception as e:
        print(f"Error while creating directories: {e}")

def format_job_search_url(raw_url):
    """
    Formats a raw LinkedIn job search URL to a simplified template with placeholders.

    Parameters:
    - raw_url (str): The raw LinkedIn job search URL.

    Returns:
    - str: The formatted URL template with placeholders for pagination.
    """
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    # Parse the raw URL
    parsed_url = urlparse(raw_url)
    query_params = parse_qs(parsed_url.query)

    # Keep only the necessary parameters: geoId and keywords
    filtered_params = {
        "geoId": query_params.get("geoId", [""])[0],
        "keywords": query_params.get("keywords", [""])[0],
    }

    # Build the new query string
    filtered_params["start"] = "{}"  # Add the pagination placeholder
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
    print("Setting up directories...")
    create_directories()
    
    print("Starting job scraping...")
    job_search_url_template = format_job_search_url(LINKEDIN_SEARCH_URL)
    scrape_linkedin_jobs(job_search_url_template)
    
    print("Cleaning and organizing data...")
    update_positions_with_timestamp(RAW_DATA_FOLDER, CLEAN_DATA_FOLDER)
    
    print("Analyzing data...")
    analyze_jobs_with_chatgpt(PROMPT_SUFFIX)

    print("Pipeline complete.")
