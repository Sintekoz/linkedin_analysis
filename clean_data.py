import pandas as pd
import os
from datetime import datetime

def get_most_recent_csv(folder_path):
    """
    Finds the most recent CSV file in the specified folder.

    Parameters:
    - folder_path: Path to the folder containing CSV files.

    Returns:
    - Path to the most recent CSV file or None if no files are found.
    """
    csv_files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
    if not csv_files:
        return None  # Return None if no CSV files found
    
    full_paths = [os.path.join(folder_path, f) for f in csv_files]
    return max(full_paths, key=os.path.getmtime)

def update_positions_with_timestamp(input_folder, output_folder):
    """
    Updates the positions list based on the input file, creating a new file with
    a timestamp, appending new positions, updating ongoing positions, marking missing ones,
    and maintaining a Last Change Timestamp column.

    Parameters:
    - input_folder: Path to the folder containing the most recent input file.
    - output_folder: Path to the folder for saving updated positions list.
    """
    # Get the most recent input file
    input_file = get_most_recent_csv(input_folder)
    if not input_file:
        print(f"No CSV files found in the folder: {input_folder}")
        return
    
    print(f"Using the most recent file as input: {input_file}")
    
    # Load the current job data
    current_data = pd.read_csv(input_file)
    
    # Get the most recent output file
    most_recent_file = get_most_recent_csv(output_folder)
    
    if most_recent_file:
        previous_data = pd.read_csv(most_recent_file)
    else:
        # If no previous file, use an empty DataFrame with the correct structure
        previous_data = pd.DataFrame(columns=current_data.columns.tolist() + ['Status', 'Last Change Timestamp'])
    
    # Ensure 'Status' and 'Last Change Timestamp' columns exist in the previous data
    if 'Status' not in previous_data.columns:
        previous_data['Status'] = None
    if 'Last Change Timestamp' not in previous_data.columns:
        previous_data['Last Change Timestamp'] = None

    # Create a set of current job URLs for comparison
    current_urls = set(current_data['Job URL'])
    previous_urls = set(previous_data['Job URL'])
    
    # Current timestamp
    current_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Update the status and last change timestamp for ongoing positions
    previous_data.loc[previous_data['Job URL'].isin(current_urls), 'Status'] = 'Ongoing'
    previous_data.loc[previous_data['Job URL'].isin(current_urls), 'Last Change Timestamp'] = previous_data.loc[
        previous_data['Job URL'].isin(current_urls), 'Last Change Timestamp'
    ]

    # Mark positions missing from the current file and retain their last change timestamp
    previous_data.loc[~previous_data['Job URL'].isin(current_urls), 'Status'] = 'Missing'
    previous_data.loc[~previous_data['Job URL'].isin(current_urls), 'Last Change Timestamp'] = previous_data.loc[
        ~previous_data['Job URL'].isin(current_urls), 'Last Change Timestamp'
    ]

    # Add new positions with status 'New' and current timestamp
    new_positions = current_data[~current_data['Job URL'].isin(previous_urls)].copy()
    new_positions['Status'] = 'New'
    new_positions['Last Change Timestamp'] = current_timestamp
    
    # Append new positions to the previous data
    updated_data = pd.concat([previous_data, new_positions], ignore_index=True)
    
    # Remove duplicates (keep the most recent entries)
    updated_data = updated_data.drop_duplicates(subset=['Job URL'], keep='last')
    
    # Save the updated data to a new file with a timestamp
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output_file = os.path.join(output_folder, f"{timestamp}_positions_list.csv")
    updated_data.to_csv(output_file, index=False)
    print(f"Updated positions list saved to {output_file}")
