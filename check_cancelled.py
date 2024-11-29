import sqlite3
import time
import random
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from page_scraper import update_job_status, login_mainpage, init_driver

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
        for job_id in job_urls:
            print(f"Processing job ID: {job_id}")

            # Navigate to the job page
            job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
            driver.get(job_url)
            time.sleep(2 + random.uniform(0.5, 1))

            # Check for the "cancelled" message
            job_status = "ongoing"  # Default status
            try:
                cancel_element = driver.find_element(By.CSS_SELECTOR, "span.artdeco-inline-feedback__message")
                cancel_element_text = cancel_element.text
                if "No longer accepting applications" in cancel_element_text:
                    job_status = "cancelled"
            except NoSuchElementException:
                pass

            # Update the job status in the database
            update_job_status(job_id, job_status, job_url)

    except Exception as e:
        print(f"Error processing ongoing jobs: {e}")
    finally:
        driver.quit()

