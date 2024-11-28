import os
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
import pdfplumber  # Library for reading PDFs

# Load environment variables from .env file
load_dotenv()

# Initialize the OpenAI client
client = OpenAI(
    api_key=os.environ['OPENAI_API_KEY']  # This is also the default and can be omitted if the environment variable is set
)

# Define directories
cv_folder = "./user_data/"
db_path = "./linkedin_jobs.db"

def get_most_recent_pdf(folder_path):
    """
    Finds the most recent PDF file in the specified folder.

    Parameters:
    - folder_path: Path to the folder containing PDF files.

    Returns:
    - Path to the most recent PDF file or None if no files are found.
    """
    pdf_files = [f for f in os.listdir(folder_path) if f.endswith('.pdf')]
    if not pdf_files:
        raise FileNotFoundError("No PDF files found in the specified folder.")
    
    full_paths = [os.path.join(folder_path, f) for f in pdf_files]
    return max(full_paths, key=os.path.getmtime)

def extract_text_from_pdf(pdf_path):
    """
    Extracts text content from a PDF file.

    Parameters:
    - pdf_path: Path to the PDF file.

    Returns:
    - Extracted text as a string.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at {pdf_path}")
    
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text.strip()

def analyze_jobs_with_chatgpt(prompt_suffix):
    """
    Analyze job positions using ChatGPT and a user-provided prompt suffix, and save the analysis in SQLite.

    Parameters:
    - prompt_suffix: The final sentence of the prompt that adjusts how ChatGPT evaluates the job position.

    Returns:
    - None: Saves the evaluations to the SQLite database.
    """

    # Get the most recent CV
    cv_path = get_most_recent_pdf(cv_folder)
    print(f"Using CV file: {cv_path}")

    # Extract CV content from PDF
    user_cv = extract_text_from_pdf(cv_path)

    # Connect to the SQLite database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Ensure the chatgpt_analysis table exists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chatgpt_analysis (
        job_id INTEGER PRIMARY KEY,
        chatgpt_message TEXT,
        FOREIGN KEY (job_id) REFERENCES job_ids (job_id)
    )
    """)
    conn.commit()

    # Fetch job descriptions for IDs not yet in chatgpt_analysis
    cursor.execute("""
    SELECT job_id, job_description 
    FROM job_description 
    WHERE job_id NOT IN (SELECT job_id FROM chatgpt_analysis)
    """)
    jobs = cursor.fetchall()

    # Process the rows to evaluate job positions
    for job in jobs:
        job_id, job_description = job

        if not job_description:
            print(f"Skipping job ID {job_id}: No job description provided.")
            continue  # Skip if no job description is available

        print(f"Processing job ID {job_id}...")

        try:
            # Send the job description and CV to ChatGPT for evaluation
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a career coach helping a user evaluate job positions based on their CV."},
                    {
                        "role": "user",
                        "content": (
                            f"Here is the user's CV:\n{user_cv}\n\n"
                            f"Here is the job description:\n{job_description}\n\n"
                            f"{prompt_suffix}"  # Dynamic prompt suffix
                        )
                    }
                ]
            )
            
            # Extract ChatGPT's message
            chatgpt_message = response.choices[0].message.content.strip()
            print(f"Job ID {job_id} processed. ChatGPT message: {chatgpt_message}")

            # Insert the analysis into the database
            cursor.execute("""
            INSERT INTO chatgpt_analysis (job_id, chatgpt_message)
            VALUES (?, ?)
            """, (job_id, chatgpt_message))

        except Exception as e:
            print(f"Error processing job ID {job_id}: {e}")

    # Commit the changes and close the connection
    conn.commit()
    conn.close()

    print("ChatGPT analysis completed and saved to the database.")
