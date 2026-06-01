"""
Process tube count data from Excel files and convert to tidy CSV format.

This script reads tube count data from Excel files in a specified directory,
extracts traffic counts by vehicle type and time interval, performs averaging
for selected day types, and outputs tidy CSV files suitable for analysis.

Usage:
    python process-tube-counts.py

Input:
    Excel files (.xlsm) in inputs/tube-counts/ directory

Output:
    - {project}_tube_counts_{day}.csv: Processed tube count data (includes DatesUsed column)
    - {project}_unique_tubes.csv: Unique tube site locations with coordinates
"""

import pandas as pd
import numpy as np
import re
import os
import glob
from pathlib import Path
from pyproj import Transformer
from typing import Dict, Optional, List, Any, Tuple
import sys


# Configuration (defaults - can be overridden when calling process_all_files)
PROJECT_NAME = 'NewNorthRoad'
DAY_TYPE = 'Weekday'  # Change to 'Weekday' or 'Saturday' as needed
INPUT_FOLDER = 'inputs/tube-counts'
OUTPUT_FOLDER = 'outputs'
INPUT_SHEET_NAME = 'Input'

# Day type filters
WEEKDAY_FILTERS = ['Tuesday', 'Wednesday', 'Thursday']
SATURDAY_FILTER = ['Saturday']


def process_excel_file(file_path: str) -> List[List[Any]]:
    """
    Process a single Excel file and extract tube count data.
    
    Args:
        file_path: Path to the Excel file (.xlsm)
        
    Returns:
        List of data rows, each containing tube count information
    """
    print(f"Processing file: {file_path}")
    
    # Read the Excel file with pandas (no header, read all as raw data)
    df = pd.read_excel(file_path, sheet_name=INPUT_SHEET_NAME, header=None)
    
    # Extract metadata from specific cells (0-indexed in pandas)
    roadname = df.iloc[1, 1] if len(df) > 1 and len(df.columns) > 1 else None
    location = df.iloc[2, 1] if len(df) > 2 and len(df.columns) > 1 else None
    start = df.iloc[4, 1] if len(df) > 4 and len(df.columns) > 1 else None
    end = df.iloc[5, 1] if len(df) > 5 and len(df.columns) > 1 else None
    
    # Regex pattern to match a date like 'Thursday, 19 September 2024'
    date_pattern = re.compile(r'^\w+,\s\d{1,2}\s\w+\s\d{4}$')
    
    # List to hold data from this file
    file_data = []
    
    # Loop through rows in the first column (column A, index 0)
    for row_idx in range(len(df)):
        cell_value = df.iloc[row_idx, 0]
        if isinstance(cell_value, str) and date_pattern.match(cell_value.strip()):
            date_str = cell_value.strip()
            data_start_row = row_idx + 5
            data_end_row = row_idx + 52
            year = date_str.split()[-1]
            month = date_str.split()[2]
            
            # Extract data rows for this date block
            for r in range(data_start_row, min(data_end_row + 1, len(df))):
                # Get values from columns A (0) to U (20) - 21 columns total
                row_values = df.iloc[r, 0:21].tolist()
                
                # Append metadata
                row_values.append(roadname)
                row_values.append(location)
                row_values.append(start)
                row_values.append(end)
                row_values.append(date_str)
                row_values.append(year)
                row_values.append(month)
                file_data.append(row_values)
    
    return file_data


def filter_dates_by_day_type(dates: List[str], day_type: str) -> List[str]:
    """
    Filter dates based on day type (Weekday or Saturday).
    
    Args:
        dates: List of date strings
        day_type: Type of day to filter ('Weekday' or 'Saturday')
        
    Returns:
        Filtered list of date strings
    """
    if day_type == 'Weekday':
        return [date for date in dates if any(day in date for day in WEEKDAY_FILTERS)]
    elif day_type == 'Saturday':
        return [date for date in dates if any(day in date for day in SATURDAY_FILTER)]
    else:
        return dates


def calculate_vehicle_type_averages(df: pd.DataFrame, numeric_cols: List[str]) -> pd.DataFrame:
    """
    Calculate average counts by vehicle type and add aggregated columns.
    
    Args:
        df: DataFrame with tube count data
        numeric_cols: List of numeric column names to average
        
    Returns:
        DataFrame with averaged counts and vehicle type aggregations
    """
    # Create a mapping of dates used for each site/direction combination
    # Group by Site and Direction to get unique dates for each combination
    site_dates = (
        df.groupby(['Site', 'Direction', 'Start', 'End'])['Date']
        .apply(lambda x: '; '.join(sorted(set(x))))
        .reset_index()
        .rename(columns={'Date': 'DatesUsed'})
    )
    
    averaged_df = (
        df
        .groupby(['Time', 'Direction', 'Site', 'Location', 'Start', 'End', 'Year'])[numeric_cols]
        .mean()
        .reset_index()
    )
    
    # Merge the dates used back into the averaged dataframe
    averaged_df = averaged_df.merge(site_dates, on=['Site', 'Direction', 'Start', 'End'], how='left')
    
    averaged_df['Cars'] = averaged_df['Cls1'] + averaged_df['Cls2'] + averaged_df['Cls3']
    averaged_df['Trucks'] = (averaged_df['Cls4'] + averaged_df['Cls5'] + averaged_df['Cls6'] + 
                             averaged_df['Cls7'] + averaged_df['Cls8'] + averaged_df['Cls9'] + 
                             averaged_df['Cls10'] + averaged_df['Cls11'] + averaged_df['Cls12'] + 
                             averaged_df['Cls13'])
    averaged_df['Total'] = averaged_df['Cars'] + averaged_df['Trucks']
    
    return averaged_df


def distribute_counts_by_interval(df: pd.DataFrame) -> pd.DataFrame:
    """
    Distribute vehicle counts across 15-minute intervals.
    
    Args:
        df: DataFrame with aggregated vehicle counts
        
    Returns:
        DataFrame with counts distributed by interval
    """
    # Calculate the percentages for vehicle types
    car_ratio = df['Cars'] / df['Total']
    truck_ratio = df['Trucks'] / df['Total']
    
    # Apply these percentages across all intervals
    for interval in ['00', '15', '30', '45']:
        drop_col = f"Drop--{interval}"
        df[f"Car-{interval}"] = car_ratio * df[drop_col]
        df[f"Truck-{interval}"] = truck_ratio * df[drop_col]
    
    return df


def map_direction_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map direction codes ('P' and 'S') to human-readable labels.
    
    Args:
        df: DataFrame with Direction, Start, and End columns
        
    Returns:
        DataFrame with updated Direction labels
    """
    df['Direction'] = df.apply(
        lambda row: f"{row['Start']} to {row['End']}" if row['Direction'] == 'P' 
        else (f"{row['End']} to {row['Start']}" if row['Direction'] == 'S' else row['Direction']), 
        axis=1
    )
    return df


def reshape_to_tidy_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reshape data to tidy format with one row per observation.
    
    Args:
        df: DataFrame in wide format
        
    Returns:
        DataFrame in tidy (long) format
    """
    # Select relevant columns (including DatesUsed)
    df = df[['Time', 'Year', 'Direction', 'Site', 'Location', 
             'Car-00', 'Car-15', 'Car-30', 'Car-45', 
             'Truck-00', 'Truck-15', 'Truck-30', 'Truck-45',
             'DatesUsed']]
    
    # Melt the dataframe to long format
    melted_df = df.melt(
        id_vars=['Time', 'Year', 'Direction', 'Site', 'Location', 'DatesUsed'],
        value_vars=['Car-00', 'Car-15', 'Car-30', 'Car-45',
                    'Truck-00', 'Truck-15', 'Truck-30', 'Truck-45'],
        var_name='VehicleInterval',
        value_name='Volume'
    )
    
    # Split vehicle type and minute offset
    melted_df[['VehicleType', 'MinuteOffset']] = melted_df['VehicleInterval'].str.split('-', expand=True)
    
    # Convert 'Time' (e.g., 100) to base hour and add minute offset
    melted_df['Hour'] = melted_df['Time'].astype(str).str.zfill(4).str[:2].astype(int)
    melted_df['Minute'] = melted_df['MinuteOffset'].astype(int)
    
    # Create formatted timestamp as HH:MM
    melted_df['TimeOfDay'] = melted_df.apply(lambda row: f"{row['Hour']:02}:{row['Minute']:02}", axis=1)
    
    # Drop intermediate columns and reorder
    final_df = melted_df[['TimeOfDay', 'Year', 'Direction', 'Site', 'Location', 'VehicleType', 'Volume', 'DatesUsed']]
    
    # Convert TimeOfDay to datetime for calculations
    final_df['TimeOfDay'] = pd.to_datetime(final_df['TimeOfDay'], format='%H:%M')
    
    # Sort by TimeOfDay
    final_df = final_df.sort_values(by=['TimeOfDay', 'Year', 'Direction', 'Site', 'Location', 'VehicleType'])
    final_df.reset_index(drop=True, inplace=True)
    
    # Rename TimeOfDay to StartTime and create EndTime
    final_df.rename(columns={'TimeOfDay': 'StartTime'}, inplace=True)
    final_df['EndTime'] = final_df['StartTime'] + pd.Timedelta(minutes=15)
    
    # Convert to time objects for final output
    final_df['StartTime'] = final_df['StartTime'].dt.time
    final_df['EndTime'] = final_df['EndTime'].dt.time

    # reorder to put endtime after starttime
    final_df = final_df[['StartTime', 'EndTime', 'Year', 'Site', 'Direction', 'Location', 'VehicleType', 'Volume', 'DatesUsed']]

    return final_df


def extract_coordinates(location_str: Any) -> Tuple[Optional[int], Optional[int]]:
    """
    Extract easting and northing coordinates from location string.
    
    Args:
        location_str: Location string containing coordinates (e.g., "E1234567 N5678901")
        
    Returns:
        Tuple of (easting, northing) or (None, None) if not found
    """
    if isinstance(location_str, str):
        match = re.search(r'E(\d+)\s*N(\d+)', location_str)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None


def transform_coordinates_nztm_to_wgs84(row: pd.Series) -> pd.Series:
    """
    Transform coordinates from NZTM (EPSG:2193) to WGS84 (EPSG:4326).
    
    Args:
        row: DataFrame row with 'easting' and 'northing' columns
        
    Returns:
        Series with 'latitude' and 'longitude'
    """
    transformer = Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True)
    
    if row['easting'] and row['northing']:
        lon, lat = transformer.transform(row['easting'], row['northing'])
        return pd.Series({'latitude': lat, 'longitude': lon})
    return pd.Series({'latitude': None, 'longitude': None})


def create_unique_sites_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create dataframe of unique tube sites with coordinates.
    
    Args:
        df: DataFrame with tube count data
        
    Returns:
        DataFrame with unique sites and their coordinates
    """
    # Get unique values
    unique_df = df[['Site', 'Direction', 'Location']].drop_duplicates()
    unique_df = unique_df.sort_values(by=['Site', 'Direction', 'Location'])
    unique_df.reset_index(drop=True, inplace=True)
    
    # Extract coordinates
    unique_df['easting'] = unique_df['Location'].apply(lambda x: extract_coordinates(x)[0])
    unique_df['northing'] = unique_df['Location'].apply(lambda x: extract_coordinates(x)[1])
    
    # Transform to lat/lon
    unique_df[['latitude', 'longitude']] = unique_df.apply(transform_coordinates_nztm_to_wgs84, axis=1)
    
    # Drop the Location column
    unique_df.drop(columns=['Location'], inplace=True)
    
    return unique_df


def process_all_files(input_folder: str = INPUT_FOLDER,
                      project_name: str = PROJECT_NAME,
                      day_type: str = DAY_TYPE,
                      output_base: str = OUTPUT_FOLDER) -> None:
    """
    Process all Excel files in the input folder and generate output CSVs.
    
    Args:
        input_folder: Base folder containing project subfolders with input files
        project_name: Name of the project (subfolder name)
        day_type: Type of day to process ('Weekday' or 'Saturday')
        output_base: Base output folder path
    """
    # Find all xlsm files in the project folder
    project_input_folder = os.path.join(input_folder)
    xlsm_files = glob.glob(os.path.join(project_input_folder, '*.xlsm'))
    
    if not xlsm_files:
        print(f"No .xlsm files found in {project_input_folder}")
        sys.exit(1)
    
    print(f"Found {len(xlsm_files)} .xlsm file(s) to process")
    
    # Master list to hold all rows from all files
    all_data = []
    
    # Process each file
    for file_path in xlsm_files:
        try:
            file_data = process_excel_file(file_path)
            all_data.extend(file_data)
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            continue
    
    if not all_data:
        print("No data was processed successfully.")
        sys.exit(1)
    
    print(f"Total records collected: {len(all_data)}")
    
    # Define custom headers
    custom_headers = [
        "Time", "Direction", "Total", 
        "Cls1", "Cls2", "Cls3", "Cls4", "Cls5", "Cls6", "Cls7", "Cls8", "Cls9", "Cls10", "Cls11", "Cls12", "Cls13", "Cls14", 
        "Drop--00", "Drop--15", "Drop--30", "Drop--45",
        "Site", "Location", "Start", "End", "Date", "Year", "Month"
    ]
    
    # Create dataframe
    combined_df = pd.DataFrame(all_data, columns=custom_headers)
    
    # Get unique dates and filter by day type
    unique_dates = combined_df['Date'].unique().tolist()
    selected_dates = filter_dates_by_day_type(unique_dates, day_type)
    
    # Define numeric columns for averaging
    numeric_cols = [
        'Total', 'Cls1', 'Cls2', 'Cls3', 'Cls4', 'Cls5', 'Cls6',
        'Cls7', 'Cls8', 'Cls9', 'Cls10', 'Cls11', 'Cls12', 'Cls13', 'Cls14',
        "Drop--00", "Drop--15", "Drop--30", "Drop--45"
    ]
    
    # Filter data by selected dates
    filtered_df = combined_df[combined_df['Date'].isin(selected_dates)].copy()
    
    # Calculate averages by vehicle type
    averaged_df = calculate_vehicle_type_averages(filtered_df, numeric_cols)
    
    # Distribute counts by interval
    averaged_df = distribute_counts_by_interval(averaged_df)
    
    # Map direction labels
    averaged_df = map_direction_labels(averaged_df)
    
    # Reshape to tidy format
    final_df = reshape_to_tidy_format(averaged_df)
    
    # Create unique sites dataframe
    unique_sites_df = create_unique_sites_dataframe(final_df)
    
    # Create output directory if it doesn't exist
    output_folder = os.path.join(output_base, project_name, 'tube_counts')
    os.makedirs(output_folder, exist_ok=True)
    
    # Save the unique sites
    unique_output_file = os.path.join(output_folder, f'{project_name}_unique_tubes.csv')
    unique_sites_df.to_csv(unique_output_file, index=False)
    print(f"Unique tube sites exported to {unique_output_file}")
    
    # Save the final result
    output_file = os.path.join(output_folder, f'{project_name}_tube_counts_{day_type}.csv')
    final_df.to_csv(output_file, index=False)
    print(f"Tube counts exported to {output_file}")


def main() -> None:
    """Main entry point for the script."""
    process_all_files()


if __name__ == "__main__":
    main()