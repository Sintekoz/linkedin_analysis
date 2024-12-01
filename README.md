# LinkedIn Analysis

A Python-based tool for analyzing LinkedIn job postings based on your CV using OpenAI's GPT models. This project allows you to extract and analyze job postings based on a LinkedIn search URL and provides insights tailored to your custom prompts and CV.

## Features

- Scrape job postings from LinkedIn using Selenium.
- Analyze job descriptions with OpenAI's GPT models.
- Get tailored insights based on your custom prompts.
- Generate a cover letter specifically for the job position you want

---

## Requirements

- **Python**: Make sure Python is installed on your system.
- **Conda**: Make sure Conda is installed on your system.
- **LinkedIn Account**: Required for accessing LinkedIn.
- **OpenAI API Key**: Required for using GPT models.
- **CV Submission**: Your CV must be placed in the `user_data` folder before running the script.
- **Access Fee**: A $5 deposit must be made to OpenAI's account to enable access to the ChatGPT model.

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Sintekoz/linkedin_analysis.git
   cd linkedin_analysis
   ```

2. **Create the environment**:
   ```bash
   conda env create --file=environment.yaml
   ```

3. **Activate the environment**:
   ```bash
   conda activate linkedin_analysis
   ```

---

## Configuration

1. **Set up environment variables**:
   - Rename the `.env.example` file to `.env`:
     ```bash
     mv .env.example .env
     ```
   - Add your LinkedIn credentials to the `.env` file:
     ```text
     LINKEDIN_USERNAME=your_username
     LINKEDIN_PASSWORD=your_password
     ```

2. **Add OpenAI API Key**:
   - Obtain your OpenAI API key from [OpenAI](https://platform.openai.com/account/api-keys).
   - Add the key to the `.env` file:
     ```text
     OPENAI_API_KEY=your_openai_secret_key
     ```

3. **Submit Your CV**:
   - Ensure your CV file is saved in the `user_data` folder within the project directory. The script may reference this information for analysis.

4. **Access Fee Payment**:
   - Deposit $5 to OpenAI's account to enable ChatGPT access. Without this, the script cannot perform GPT-based analysis.

5. **Specify the job search URL you want to parse**:
   - Go to [LinkedIn Job Search page](https://www.linkedin.com/jobs/search), adjust your preferred filters you are interested in, and copy the URL into `main.py`
---

## Usage

1. Open the `main.py` file and update the following:
   - **Search URL**: Replace the placeholder with your desired LinkedIn search URL.
   - **ChatGPT Prompt**: Customize the prompt for the type of analysis you want to perform.

   Example:
   ```python
   search_url = "https://www.linkedin.com/jobs/search/?keywords=data%20analyst"
   analysis_prompt = "Provide a summary of the key skills and qualifications required for these roles."
   ```

2. Run the script:
   ```bash
   python main.py
   ```

---

## How It Works

1. **Data Collection**:
   - The script uses Selenium to scrape job postings from the provided LinkedIn search URL.

2. **Analysis**:
   - Job descriptions are sent to OpenAI's GPT models for analysis based on your custom prompt.

3. **Output**:
   - The script outputs insights and analysis to the terminal or generates a report (customizable).

---

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request for any feature enhancements or bug fixes.

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Acknowledgements

- [LinkedIn](https://www.linkedin.com) for job data.
- [OpenAI](https://openai.com) for their GPT models.
- [Conda](https://docs.conda.io) for environment management.

---
