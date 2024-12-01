import sqlite3
import time
import random
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from page_scraper import update_job_status, login_mainpage, init_driver, insert_job_details, get_job_details

def process_ongoing_jobs():
    """
    This function goes through all "ongoing" positions and checks if they are cancelled now.
    """
    # Connect to the database and fetch all ongoing jobs
    conn = sqlite3.connect('linkedin_jobs.db')
    cursor = conn.cursor()
    cursor.execute("SELECT job_id FROM job_ids WHERE status = 'ongoing'")
    ongoing_jobs = cursor.fetchall()
    conn.close()

    job_urls = [job[0] for job in ongoing_jobs]

    if not job_urls:
        print("No ongoing jobs found.")
        return

    print(f"Found {len(job_urls)} ongoing jobs to process.")

    # Initialize WebDriver
    driver = init_driver()

    try:
        # Log in to the LinkedIn main page
        login_mainpage(driver)

        # Process each ongoing job
        get_job_details(driver, job_urls)

    except Exception as e:
        print(f"Error processing ongoing jobs: {e}")
    finally:
        driver.quit()
