import sqlite3
import time
import random
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from page_scraper import update_job_status, login_mainpage, init_driver, insert_job_details

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

            job_status = "ongoing"  # Default status

            try:
                cancel_element = None  # Initialize with a default value
                cancel_element_text = None  

                try:
                    #Check for the cancelled job
                    cancel_element = driver.find_element(By.CSS_SELECTOR, "span.artdeco-inline-feedback__message")
                    cancel_element_text = cancel_element.text
                    if "No longer accepting applications" in cancel_element_text:
                        job_status = "cancelled"
                        update_job_status(job_id, job_status, f"https://www.linkedin.com/jobs/view/{job_id}/")
                except NoSuchElementException:
                    pass  # Ignore if not found

             # Check for the deleted job
                if not cancel_element_text:
                    try:
                        cancel_element = driver.find_element(By.CSS_SELECTOR, "p.jobs-box__body.jobs-no-job__error-msg")
                        cancel_element_text = cancel_element.text
                        if "The job you were looking for was not found." in cancel_element_text:
                            job_status = "deleted"
                            update_job_status(job_id, job_status, f"https://www.linkedin.com/jobs/view/{job_id}/")
                    except NoSuchElementException:
                    
                        update_job_status(job_id, job_status, f"https://www.linkedin.com/jobs/view/{job_id}/")

                        # Extract company_name
                        company_element = driver.find_element(By.CLASS_NAME, "job-details-jobs-unified-top-card__company-name")
                        company_text = company_element.text
                        company_name = company_text.split("\n")[0].strip()

                        # Extract title_name
                        job_title_element = driver.find_element(By.CSS_SELECTOR, "h1.t-24.t-bold.inline")
                        title_name = job_title_element.text.strip()
                        
                        # Extract job_description
                        paragraphs = driver.find_elements(By.CSS_SELECTOR, "p[dir='ltr']")
                        job_description = " ".join(
                            [p.get_attribute('innerText').strip() for p in paragraphs if p.get_attribute('innerText').strip()]
                        )

                        # Extract location, posted_date, num_applicants
                        try:
                            additional_info_element = driver.find_element(By.CSS_SELECTOR, "div.t-black--light.mt2[dir='ltr']")
                            additional_info = additional_info_element.get_attribute("innerText").strip()
                            location, posted_date, num_applicants = (additional_info.split(" · ") + ["Not Found"] * 3)[:3]
                        except NoSuchElementException:
                            # If the element is not found, fill the variables with "Not Found"
                            location, posted_date, num_applicants = "Not Found", "Not Found", "Not Found"

                        # Extract work_model
                        ui_label_elements = driver.find_elements(By.CSS_SELECTOR, "span.ui-label.ui-label--accent-3.text-body-small")
                        work_model = " · ".join([label.text.strip() for label in ui_label_elements if label.text.strip()])

                        # Insert job details into the database
                        insert_job_details(job_id, company_name, title_name, job_description, location, posted_date, num_applicants, work_model)

            except Exception as e:
                print(f"Error processing job ID {job_id}: {e}")
    except Exception as e:
        print(f"Error processing ongoing jobs: {e}")
    finally:
        driver.quit()

