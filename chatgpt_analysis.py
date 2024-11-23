import pandas as pd
import os
from dotenv import load_dotenv
from datetime import datetime
from openai import OpenAI
import pdfplumber  # Library for reading PDFs
from clean_data import get_most_recent_csv

# Load environment variables from .env file
load_dotenv()

# Initialize the OpenAI client
client = OpenAI(
    api_key=os.environ['OPENAI_API_KEY']  # This is also the default and can be omitted if the environment variable is set
)

# Define directories
cv_folder = "./user_data/"
data_folder = "./data/clean_data/"
output_folder = "./output/"

def analyze_jobs_with_chatgpt(prompt_suffix):
    """
    Analyze job positions using ChatGPT and a user-provided prompt suffix.

    Parameters:
    - prompt_suffix: The final sentence of the prompt that adjusts how ChatGPT evaluates the job position.

    Returns:
    - None: Saves the evaluated CSV to the output folder.
    """
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

    # Get the most recent CV
    cv_path = get_most_recent_pdf(cv_folder)
    print(f"Using CV file: {cv_path}")

    # Extract CV content from PDF
    user_cv = extract_text_from_pdf(cv_path)

    # Get the latest file
    latest_file = get_most_recent_csv(data_folder)
    print(f"Processing file: {latest_file}")

    # Load the CSV file
    df = pd.read_csv(latest_file)

    # Initialize new column for fit scores
    df['Fit Score'] = None

    # Process the rows to evaluate job positions
    for index, row in df.iterrows():
        job_description = row.get('Job Description', '')  # Replace with the actual column name for job descriptions
        if not job_description:
            print(f"Skipping row {index + 1}: No job description provided.")
            continue  # Skip if no job description is available

        print(f"Processing row {index + 1}...")
        
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
            
            # Extract fit score
            fit_score = response.choices[0].message.content.strip()
            print(f"Row {index + 1} processed. Fit score: {fit_score}")

            # Add the fit score to the DataFrame
            df.at[index, 'Fit Score'] = fit_score

        except Exception as e:
            print(f"Error processing row {index}: {e}")

    # Save the updated DataFrame to a new CSV file with a timestamp
    os.makedirs("./output", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output_file = os.path.join(output_folder, f"{timestamp}_evaluated_jobs.csv")
    df.to_csv(output_file, index=False)

    print(f"Processed CSV file saved to {output_file}")
    