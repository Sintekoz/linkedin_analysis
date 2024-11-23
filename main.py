import os
from page_scraper import scrape_linkedin_jobs
from clean_data import update_positions_with_timestamp, get_most_recent_csv
from chatgpt_analysis import analyze_jobs_with_chatgpt
import pandas as pd
from datetime import datetime
from selenium.common.exceptions import WebDriverException

# Input your prompt
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

if __name__ == "__main__":
    print("Setting up directories...")
    create_directories()
    
    print("Starting job scraping...")
    scrape_linkedin_jobs()
    
    print("Cleaning and organizing data...")
    update_positions_with_timestamp(RAW_DATA_FOLDER, CLEAN_DATA_FOLDER)
    
    print("Analyzing data...")
    analyze_jobs_with_chatgpt(PROMPT_SUFFIX)

    print("Pipeline complete.")
