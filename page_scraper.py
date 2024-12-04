import sqlite3
import time
import random
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv

# Setup database and tables
def setup_database():
    conn = sqlite3.connect('linkedin_jobs.db')
    cursor = conn.cursor()

    # Create job_ids table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_ids (
            job_id TEXT PRIMARY KEY,
            status TEXT,
            timestamp_added TEXT,
            timestamp_updated TEXT,
            job_url TEXT
        )
    ''')

    # Create job_description table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_description (
            job_id TEXT PRIMARY KEY,
            company_name TEXT,
            title_name TEXT,
            job_description TEXT,
            location TEXT,
            posted_date TEXT,
            number_applicants TEXT,
            work_model TEXT,
            FOREIGN KEY (job_id) REFERENCES job_ids (job_id)
        )
    ''')

    conn.commit()
    conn.close()

# Function to update job status
def update_job_status(job_id, status, job_url):
    conn = sqlite3.connect('linkedin_jobs.db')
    cursor = conn.cursor()
    current_time = datetime.now().strftime('%Y%m%d%H%M')

    cursor.execute('SELECT * FROM job_ids WHERE job_id = ?', (job_id,))
    result = cursor.fetchone()

    if result:
        # Update existing job_id status
        cursor.execute('''
            UPDATE job_ids
            SET status = ?, timestamp_updated = ?, job_url = ?
            WHERE job_id = ?
        ''', (status, current_time, job_url, job_id))
        print(f"Job ID {job_id} updated to status: {status}.")
    else:
        # Insert new job_id with initial status
        cursor.execute('''
            INSERT INTO job_ids (job_id, status, timestamp_added, timestamp_updated, job_url)
            VALUES (?, ?, ?, ?, ?)
        ''', (job_id, status, current_time, current_time, job_url))
        print(f"Job ID {job_id} added with status: {status}.")

    conn.commit()
    conn.close()

# Function to insert job details into job_description table
def insert_job_details(job_id, company_name, title_name, job_description, location, posted_date, num_applicants, work_model):
    conn = sqlite3.connect('linkedin_jobs.db')
    cursor = conn.cursor()

    cursor.execute('''
        INSERT OR REPLACE INTO job_description (
            job_id, company_name, title_name, job_description, location, posted_date, number_applicants, work_model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (job_id, company_name, title_name, job_description, location, posted_date, num_applicants, work_model))

    conn.commit()
    conn.close()

# Function to initaliase Selenium
def init_driver():
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-popup-blocking")

    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def shorten_job_url(raw_url):
    return raw_url.split("?")[0].split('/')[-2]  # Extracts job_id

# Main scraping function
def login_mainpage(driver):
    load_dotenv()

    linkedin_username = os.getenv("LINKEDIN_EMAIL")
    linkedin_password = os.getenv("LINKEDIN_PASSWORD")

    if not linkedin_username or not linkedin_password:
        raise ValueError("LinkedIn email or password not found in .env file")

    try:
        driver.get("https://www.linkedin.com/login")
        time.sleep(5 + random.uniform(1, 2))

        email_field = driver.find_element(By.ID, "username")
        password_field = driver.find_element(By.ID, "password")
        email_field.send_keys(linkedin_username)
        password_field.send_keys(linkedin_password)
        password_field.send_keys(Keys.RETURN)
        time.sleep(10 + random.uniform(1, 3))
        print("Logged in successfully.")
    except Exception as e:
        print(f"Error logging in: {e}")
        driver.quit()
        return

def get_job_details(driver, job_urls):
    for job_id in job_urls:

            # Navigate to the job page
            job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
            driver.get(job_url)
            time.sleep(2 + random.uniform(0.5, 1))

            job_status = "ongoing"  # Default status

            try:
                cancel_element = None  # Initialize with a default value
                cancel_element_text = None  # Initialize with a default value

                # Check for the cancelled job
                try:
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

def scrape_linkedin_jobs(job_search_url_template):
    #Login to the main page
    driver = init_driver()

    try:
        # Log in to LinkedIn
        login_mainpage(driver)
        page_number = 0
        job_urls = []

        while True:
            current_url = f"{job_search_url_template}&start={page_number * 25}"
            driver.get(current_url)
            time.sleep(4 + random.uniform(1, 2))

            job_list = driver.find_element(By.XPATH, "//*[@id='main']/div/div[2]/div[1]/div")
            last_scroll_top = -1
            while True:
                driver.execute_script("arguments[0].scrollTop += 500", job_list)
                time.sleep(random.uniform(0.5, 1.5))
                current_scroll_top = driver.execute_script("return arguments[0].scrollTop", job_list)
                if current_scroll_top == last_scroll_top:
                    break
                last_scroll_top = current_scroll_top

            job_cards = job_list.find_elements(By.CSS_SELECTOR, "a[href*='/jobs/view/']")
    
            page_job_urls = [shorten_job_url(job.get_attribute('href')) for job in job_cards]
            job_urls.extend(page_job_urls)

            if len(page_job_urls) < 25:
                break
            page_number += 1
    except Exception as e:
        print(f"Error collecting job URLs: {e}")
    finally:
        print(f"Collected {len(job_urls)} job IDs.")

    try:
        # Connect to the database to retrieve all existing job_ids
        conn = sqlite3.connect('linkedin_jobs.db')
        cursor = conn.cursor()
        cursor.execute('SELECT job_id FROM job_ids')
        existing_job_ids = {row[0] for row in cursor.fetchall()}
        conn.close()

        # Filter out existing job IDs from job_urls
        original_job_count = len(job_urls)
        job_urls = [job_id for job_id in job_urls if job_id not in existing_job_ids]
        print(f"Filtered out {original_job_count - len(job_urls)} existing job IDs. Remaining: {len(job_urls)}")

        # Go through each url and scrap data
        get_job_details(driver, job_urls)

    except Exception as e:
        print(f"Error processing ongoing jobs: {e}")
    finally:
        driver.quit()

