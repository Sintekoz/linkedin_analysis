import time
import random
import pandas as pd
from datetime import datetime
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

linkedin_username = os.getenv("LINKEDIN_EMAIL")
linkedin_password = os.getenv("LINKEDIN_PASSWORD")

if not linkedin_username or not linkedin_password:
    raise ValueError("LinkedIn email or password not found in .env file")

# Setup Chrome options
chrome_options = Options()
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--start-maximized")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--disable-popup-blocking")

# Function to initialize the WebDriver
def init_driver():
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

# Function to shorten LinkedIn job URLs
def shorten_job_url(raw_url):
    return raw_url.split("?")[0]

# Initialize WebDriver
driver = init_driver()

# Open LinkedIn login page
try:
    driver.get("https://www.linkedin.com/login")
    time.sleep(2 + random.uniform(1, 2))  # Randomized wait time

    # Log in to LinkedIn
    email_field = driver.find_element(By.ID, "username")
    password_field = driver.find_element(By.ID, "password")
    email_field.send_keys(linkedin_username)
    password_field.send_keys(linkedin_password)
    password_field.send_keys(Keys.RETURN)
    time.sleep(5 + random.uniform(1, 3))  # Randomized wait time
    print("Logged in successfully.")
except Exception as e:
    print(f"Error logging in: {e}")
    driver.quit()
    exit()

# Step 1: Collect all job URLs from all pages
job_urls = []
try:
    job_search_url_template = (
        "https://www.linkedin.com/jobs/search/?geoId=104738515&keywords=senior%20analyst&start={}"
    )
    page_number = 0

    while True:
        try:
            print(f"Processing page {page_number + 1}...")
            driver.get(job_search_url_template.format(page_number * 25))
            time.sleep(5 + random.uniform(2, 4))  # Randomized wait time for page load

            # Scroll to ensure all jobs are loaded
            job_list = driver.find_element(By.XPATH, "//*[@id='main']/div/div[2]/div[1]/div")
            last_scroll_top = -1
            while True:
                driver.execute_script("arguments[0].scrollTop += 500", job_list)
                time.sleep(random.uniform(1.5, 2.5))  # Randomized wait time for scrolling
                current_scroll_top = driver.execute_script("return arguments[0].scrollTop", job_list)
                if current_scroll_top == last_scroll_top:
                    break
                last_scroll_top = current_scroll_top

            # Extract job URLs
            print("Extracting job URLs...")
            job_cards = job_list.find_elements(By.CSS_SELECTOR, "a[href*='/jobs/view/']")
            page_job_urls = [shorten_job_url(job.get_attribute('href')) for job in job_cards]
            print(f"Found {len(page_job_urls)} job URLs on page {page_number + 1}.")
            job_urls.extend(page_job_urls)

            # Stop if fewer than 25 jobs are on the page
            if len(page_job_urls) < 25:
                print("Fewer than 25 jobs on the page. Stopping pagination.")
                break

            # Increment page number for next page
            page_number += 1

        except WebDriverException as e:
            print(f"WebDriverException: {e}. Reinitializing driver...")
            driver.quit()
            driver = init_driver()

except Exception as e:
    print(f"Error collecting job URLs: {e}")
finally:
    print(f"Collected {len(job_urls)} job URLs.")

# Step 2: Extract job details for each URL
job_data = []

try:
    for idx, job_url in enumerate(job_urls, start=1):
        print(f"Processing job {idx}/{len(job_urls)}: {job_url}")
        try:
            driver.get(job_url)
            time.sleep(3 + random.uniform(1, 2))  # Randomized wait time

            # Extract job description
            paragraphs = driver.find_elements(By.CSS_SELECTOR, "p[dir='ltr']")
            job_description = " ".join(
                [
                    p.get_attribute('innerText').strip()
                    for p in paragraphs
                    if p.get_attribute('innerText').strip()
                ]
            )

            # Extract additional information
            additional_info_element = driver.find_element(By.CSS_SELECTOR, "div.t-black--light.mt2[dir='ltr']")
            additional_info = additional_info_element.get_attribute("innerText").strip()
            location, reposted_date, num_applicants = (additional_info.split(" · ") + ["Not Found"] * 3)[:3]

            # Extract work model & arrangement
            ui_label_elements = driver.find_elements(By.CSS_SELECTOR, "span.ui-label.ui-label--accent-3.text-body-small")
            work_model_arrangement = " · ".join(
                label.text.strip()
                for label in ui_label_elements if label.text.strip()
            )

            # Extract company name
            try:
                company_name_element = driver.find_element(By.CLASS_NAME, "job-details-jobs-unified-top-card__company-name")
                company_name = company_name_element.text.strip()
            except NoSuchElementException:
                company_name = "Not Found"

            # Append job details
            job_data.append({
                "Job URL": job_url,
                "Job Description": job_description,
                "Company Name": company_name,
                "Location": location,
                "Reposted Date": reposted_date,
                "Number of Applicants": num_applicants,
                "Work Model & Arrangement": work_model_arrangement,
            })
        except Exception as e:
            print(f"Error extracting data for {job_url}: {e}")
            continue

    # Save job data to a CSV file
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    save_path = f"./data/raw_data/{timestamp}_raw_data.csv"
    pd.DataFrame(job_data).to_csv(save_path, index=False)
    print(f"Job data saved to {save_path}")

except Exception as e:
    print(f"Error extracting job details: {e}")
finally:
    driver.quit()
    print("Browser closed.")