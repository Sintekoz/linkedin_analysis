import os
from page_scraper import init_driver, shorten_job_url
from clean_data import update_positions_with_timestamp, get_most_recent_csv
from chatgpt_analysis import extract_text_from_pdf, client
import pandas as pd
from datetime import datetime
from selenium.common.exceptions import WebDriverException

# Define directories
RAW_DATA_FOLDER = "./raw_data"
CLEAN_DATA_FOLDER = "./data/clean_data"
OUTPUT_FOLDER = "/Users/sintekoz/Library/CloudStorage/OneDrive-Personal/Data Science/Personal/linkedin_analysis/output"
CV_PATH = "/Users/sintekoz/Library/CloudStorage/OneDrive-Personal/Data Science/Personal/linkedin_analysis/user_data/"

def scrape_jobs():
    """Scrape job listings and save them as raw data."""
    try:
        driver = init_driver()
        # Logic for scraping jobs goes here.
        # Save raw data to RAW_DATA_FOLDER
    except WebDriverException as e:
        print(f"Scraping failed: {e}")
    finally:
        driver.quit()

def clean_and_organize_data():
    """Clean the raw job data and organize it."""
    most_recent_raw_file = get_most_recent_csv(RAW_DATA_FOLDER)
    if not most_recent_raw_file:
        print("No raw data file found.")
        return
    
    update_positions_with_timestamp(RAW_DATA_FOLDER, CLEAN_DATA_FOLDER)

def analyze_data():
    """Analyze cleaned job data and save the results."""
    try:
        # Extract CV text
        user_cv = extract_text_from_pdf(CV_PATH)

        # Get the most recent cleaned data
        most_recent_cleaned_file = get_most_recent_csv(CLEAN_DATA_FOLDER)
        if not most_recent_cleaned_file:
            print("No cleaned data file found.")
            return

        # Load the CSV file
        df = pd.read_csv(most_recent_cleaned_file)
        df['Fit Score'] = None

        # Analyze each job
        for index, row in df.iterrows():
            job_description = row.get('Job Description', '')
            if not job_description:
                continue

            # Call GPT for analysis
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a career coach helping a user evaluate job positions based on their CV."},
                    {
                        "role": "user",
                        "content": (
                            f"Here is the user's CV:\n{user_cv}\n\n"
                            f"Here is the job description:\n{job_description}\n\n"
                            "Please score how well this position fits the user on a scale from 1 to 10."
                        )
                    }
                ]
            )
            fit_score = response.choices[0].message.content.strip()
            df.at[index, 'Fit Score'] = fit_score

        # Save results
        timestamp = datetime.now().strftime("%Y%m%d%H%M")
        output_file = os.path.join(OUTPUT_FOLDER, f"{timestamp}_evaluated_jobs.csv")
        df.to_csv(output_file, index=False)
        print(f"Analysis results saved to {output_file}")

    except Exception as e:
        print(f"Error during analysis: {e}")

if __name__ == "__main__":
    print("Starting job scraping...")
    scrape_jobs()
    
    print("Cleaning and organizing data...")
    clean_and_organize_data()
    
    print("Analyzing data...")
    analyze_data()
    print("Pipeline complete.")
