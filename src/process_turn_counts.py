"""
Process turning count data from Excel files and convert to tidy CSV format.

This script reads turning count data from Excel files in a specified directory,
extracts intersection information, weather conditions, dates, and vehicle counts,
then outputs tidy CSV files suitable for analysis.

Two source spreadsheet formats are supported and auto-detected per file:

* **Team Traffic** — sheet named ``Table``, descriptive header string in A1,
  concrete per-lane turn descriptions (e.g. "Lane 1 Left into Morningside Dr").
  Vehicle types: Cars / Trucks / Buses / Cyclists.

* **Matrix** — sheet named ``Raw data & Hourly Summary``, metadata key/value
  pairs in the left columns, two stacked 15-minute data blocks (followed by
  hourly summary blocks that are ignored). Turning movements are labelled only
  as "Direction 1"…"Direction 20U" — the parser passes those labels through
  verbatim. Vehicle types Lights/Heavies are remapped to Cars/Trucks so output
  matches the Team Traffic vocabulary used downstream by ``create_rds.py``.
  NOTE: because Matrix files lack lane-level turn descriptions, rows from a
  Matrix file will not match an existing ``matched_turn_counts.csv`` until a
  per-site Direction-N → turnObjectId mapping is authored separately.

Usage:
    python process-turn-counts.py

Input:
    Excel files (.xlsx) in inputs/turn_counts/ directory

Output:
    - {project}_turning_counts.csv: All turning count records
    - {project}_unique_turns.csv: Unique intersection/approach/turn combinations
"""

import pandas as pd
import numpy as np
import os
import glob
import re
from datetime import datetime
from typing import Dict, Optional, List, Any
import sys
from src.intersection_finder_local import find_intersection_coordinates


# Configuration (defaults - can be overridden when calling process_all_files)
PROJECT_NAME = 'NewNorthRoad'
INPUT_FOLDER = 'inputs/turn_counts'
OUTPUT_FOLDER = 'outputs'
DEFAULT_SHEET_NAME = 'Table'
STUDY_AREA_GEOJSON = 'inputs/NNR_cordon.geojson'
FIND_INTERSECTION_COORDS = True  # Set to True to find intersection coordinates

# Sheet names used to identify each source format
TEAM_TRAFFIC_SHEET = 'Table'
MATRIX_SHEET = 'Raw data & Hourly Summary'

# Matrix uses different vehicle-class names than Team Traffic; remap so the
# output schema is identical regardless of source format.
MATRIX_VEHICLE_TYPE_MAP = {
    'Lights': 'Cars',
    'Heavies': 'Trucks',
    'Buses': 'Buses',
}


def extract_intersection_info(text: Any) -> Dict[str, Optional[str | datetime]]:
    """
    Extract intersection, weather conditions, and date from a formatted string.
    
    Expected format: "Intersection Name (Day) Weather Condition Day DD/MM/YYYY"
    Example: "Michaels Avenue Eastern-Michaels Avenue Western-Main Highway (Wed) Weather Fine Wed 14/05/2025"
    
    Returns:
        dict: Contains 'intersection', 'weather', 'date_str', and 'date' keys
    """
    if not text or pd.isna(text):
        return {"intersection": "", "weather": "", "date_str": "", "date": None}
    
    # Pattern to match the format
    # Group 1: Intersection name (everything before " (Day)")
    # Group 2: Weather condition (word after "Weather ")
    # Group 3: Date string (DD/MM/YYYY at the end)
    pattern = r'^(.+?)\s+\([A-Za-z]+\)\s+Weather\s+(\w+)\s+[A-Za-z]+\s+(\d{2}/\d{2}/\d{4})$'
    
    match = re.match(pattern, text.strip())
    
    if match:
        intersection = match.group(1).strip()
        weather = match.group(2).strip()
        date_str = match.group(3).strip()
        
        # Parse the date
        try:
            date_obj = datetime.strptime(date_str, '%d/%m/%Y')
        except ValueError:
            date_obj = None
        
        # Remove day-of-week references from intersection name
        day_pattern = r'\s*\((Mon|Tue|Wed|Thu|Thur|Fri|Sat|Sun|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\)\s*'
        intersection = re.sub(day_pattern, '', intersection, flags=re.IGNORECASE).strip()
            
        return {
            "intersection": intersection,
            "weather": weather,
            "date_str": date_str,
            "date": date_obj
        }
    else:
        # If pattern doesn't match, try to extract what we can
        # Look for date pattern at the end
        date_pattern = r'(\d{2}/\d{2}/\d{4})$'
        date_match = re.search(date_pattern, text)
        date_str = date_match.group(1) if date_match else ""
        
        # Look for weather pattern
        weather_pattern = r'Weather\s+(\w+)'
        weather_match = re.search(weather_pattern, text)
        weather = weather_match.group(1) if weather_match else ""
        
        # Everything else as intersection (remove the weather and date parts)
        intersection = text
        if weather:
            intersection = re.sub(r'\s+Weather\s+\w+.*$', '', intersection)
        
        # Remove day-of-week references from intersection name
        day_pattern = r'\s*\((Mon|Tue|Wed|Thu|Thur|Fri|Sat|Sun|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\)\s*'
        intersection = re.sub(day_pattern, '', intersection, flags=re.IGNORECASE).strip()
        
        try:
            date_obj = datetime.strptime(date_str, '%d/%m/%Y') if date_str else None
        except ValueError:
            date_obj = None
            
        return {
            "intersection": intersection.strip(),
            "weather": weather,
            "date_str": date_str,
            "date": date_obj
        }

def detect_blocks_and_reshape_team_traffic(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect data blocks in a Team Traffic Excel file and reshape into tidy format.

    Args:
        df: Raw DataFrame from Excel file

    Returns:
        Tidy DataFrame with one row per count observation
    """
    intersection = df.iloc[0, 0]
    all_records = []
    
    # Find the "Period" "Time" header row
    period_row_idx = None
    for i in range(len(df)):
        if str(df.iloc[i, 0]).strip() == "Period" and str(df.iloc[i, 1]).strip() == "Time":
            period_row_idx = i
            break
    
    if period_row_idx is None:
        return pd.DataFrame()  # No data found
    
    # Identify approaches and turns from the header rows
    approach_row = period_row_idx - 2
    turn_row = period_row_idx - 1
    
    # Scan the header rows to find all approach and turn headers
    approach_headers = []
    turn_headers = []
    
    for col in range(2, len(df.columns)):
        approach_value = df.iloc[approach_row, col]
        if pd.notna(approach_value) and str(approach_value).strip():
            approach_headers.append({"column": col, "value": approach_value})
        
        turn_value = df.iloc[turn_row, col]
        if pd.notna(turn_value) and str(turn_value).strip():
            turn_headers.append({"column": col, "value": turn_value})
    
    # Create a mapping for each column with a vehicle type
    column_mapping = []
    
    for col in range(2, len(df.columns)):
        vehicle_type = df.iloc[period_row_idx, col]
        if pd.isna(vehicle_type) or str(vehicle_type).strip() == "":
            continue  # Skip columns without a vehicle type
        
        # Find the applicable approach header (most recent to the left)
        approach = next((h["value"] for h in sorted(approach_headers, key=lambda x: x["column"], reverse=True) 
                      if h["column"] <= col), "Unknown")
        
        # Find the applicable turn header (most recent to the left)
        turn = next((h["value"] for h in sorted(turn_headers, key=lambda x: x["column"], reverse=True) 
                   if h["column"] <= col), "Unknown")
        
        column_mapping.append({
            "column": col,
            "approach": approach,
            "turn": turn,
            "vehicle_type": vehicle_type
        })
    
    # Process data rows
    for i in range(period_row_idx + 1, len(df)):
        # Skip empty rows
        if pd.isna(df.iloc[i, 0]) or str(df.iloc[i, 0]).strip() == "":
            continue
            
        period = df.iloc[i, 0]
        time = df.iloc[i, 1]
        
        # Process each vehicle type column using the mapping
        for col_map in column_mapping:
            col = col_map["column"]
            count_value = df.iloc[i, col]
            # Skip if no count value
            if pd.isna(count_value):
                continue

            # extract info from 'intersection' string
            intersection_info = extract_intersection_info(intersection)
            intersection_name = intersection_info["intersection"]
            date_str = intersection_info["date_str"]
            date = intersection_info["date"]

            all_records.append({
                "Intersection": intersection_name,
                "Approach": col_map["approach"],
                "Turn": col_map["turn"],
                "Period": period,
                "Time": time,
                "VehicleType": col_map["vehicle_type"],
                "Count": count_value,
                "Date": date
            })
    
    return pd.DataFrame(all_records)

def detect_source_format(filepath: str) -> str:
    """
    Inspect the workbook's sheet names to determine which survey company produced it.

    Returns:
        'matrix' or 'team_traffic'
    """
    sheet_names = pd.ExcelFile(filepath).sheet_names
    if MATRIX_SHEET in sheet_names:
        return 'matrix'
    if TEAM_TRAFFIC_SHEET in sheet_names:
        return 'team_traffic'
    # Default to Team Traffic for backwards compatibility — it's been the only
    # supported format historically.
    return 'team_traffic'


def reformat_turning_counts_team_traffic(filepath: str, sheet_name: str = DEFAULT_SHEET_NAME) -> pd.DataFrame:
    """Parse a Team Traffic turning-count workbook into tidy format."""
    df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
    return detect_blocks_and_reshape_team_traffic(df)


def _matrix_extract_metadata(df: pd.DataFrame) -> Dict[str, Optional[Any]]:
    """
    Pull intersection name and survey date from the metadata block at the top
    of a Matrix ``Raw data & Hourly Summary`` sheet.

    Matrix metadata is laid out as a label in column 1 and a value (prefixed
    with ': ') in a column further right (col 4 in the samples seen so far).
    """
    intersection = ""
    date_obj: Optional[datetime] = None

    for i in range(min(15, len(df))):
        label = df.iloc[i, 1]
        if pd.isna(label):
            continue
        label = str(label).strip()
        # Find the value cell (the first ':'-prefixed cell to the right)
        value: Optional[str] = None
        for c in range(2, df.shape[1]):
            cell = df.iloc[i, c]
            if pd.notna(cell) and str(cell).strip().startswith(':'):
                value = str(cell).strip().lstrip(':').strip()
                break
        if value is None:
            continue

        if label == 'Location':
            # Strip leading "<n>. " site-number prefix if present.
            intersection = re.sub(r'^\d+\.\s*', '', value).strip()
        elif label == 'Day/Date':
            try:
                # e.g. "Wed, 3 September 2025"
                date_obj = pd.to_datetime(value, dayfirst=True).to_pydatetime()
            except (ValueError, TypeError):
                date_obj = None

    return {"intersection": intersection, "date": date_obj}


def _matrix_find_blocks(df: pd.DataFrame) -> List[Dict[str, int]]:
    """
    Locate the 15-minute data blocks in a Matrix sheet.

    Each block is anchored by an "Approach" / "Direction" / "Time Period"
    triple of header rows. The sheet contains the same blocks repeated below
    in hourly-aggregated form — those repeats are detected by an intervening
    second metadata header (``Job No.`` reappears) and ignored.
    """
    blocks: List[Dict[str, int]] = []

    # Find the row index where the second metadata header starts (the hourly
    # summary repeat). Everything from that row onward is ignored.
    cutoff = len(df)
    seen_job_no = False
    for i in range(len(df)):
        if str(df.iloc[i, 1]).strip() == 'Job No.':
            if seen_job_no:
                cutoff = i
                break
            seen_job_no = True

    # Within the 15-min region, find each "Approach" header row.
    for i in range(cutoff):
        if str(df.iloc[i, 1]).strip() != 'Approach':
            continue
        if i + 2 >= cutoff:
            continue
        if str(df.iloc[i + 1, 1]).strip() != 'Direction':
            continue
        if str(df.iloc[i + 2, 1]).strip() != 'Time Period':
            continue

        # Data starts on the row after Time Period and runs until either
        # PM Totals or the next blank row.
        data_start = i + 3
        data_end = data_start
        while data_end < cutoff:
            label = df.iloc[data_end, 1]
            if pd.notna(label) and str(label).strip() == 'PM Totals':
                break
            data_end += 1

        blocks.append({
            'approach_row': i,
            'direction_row': i + 1,
            'header_row': i + 2,
            'data_start': data_start,
            'data_end': data_end,  # exclusive — does not include PM Totals
        })

    return blocks


def _matrix_build_column_mapping(df: pd.DataFrame, block: Dict[str, int]) -> List[Dict[str, Any]]:
    """
    Build a list of (column → approach, direction, vehicle_type) records for a
    block. Approach names are sparse and apply to every column at or to the
    right of where they appear, until the next named approach takes over.
    Direction labels likewise sit only in the leftmost column of each 4-col
    group and need to be forward-filled across the group.
    """
    approach_row = block['approach_row']
    direction_row = block['direction_row']
    header_row = block['header_row']

    approach_by_col: Dict[int, str] = {}
    current_approach: Optional[str] = None
    for c in range(df.shape[1]):
        v = df.iloc[approach_row, c]
        if pd.notna(v) and str(v).strip():
            current_approach = str(v).strip()
        if current_approach is not None and c >= 4:
            approach_by_col[c] = current_approach

    direction_by_col: Dict[int, str] = {}
    current_direction: Optional[str] = None
    for c in range(4, df.shape[1]):
        v = df.iloc[direction_row, c]
        if pd.notna(v) and str(v).strip():
            current_direction = str(v).strip()
        if current_direction is not None:
            direction_by_col[c] = current_direction

    column_mapping: List[Dict[str, Any]] = []
    for c in range(4, df.shape[1]):
        vehicle_type = df.iloc[header_row, c]
        if pd.isna(vehicle_type):
            continue
        vehicle_type = str(vehicle_type).strip()
        if vehicle_type not in MATRIX_VEHICLE_TYPE_MAP:
            # Skips the per-direction "Total" column and any unexpected labels.
            continue
        if c not in direction_by_col or c not in approach_by_col:
            continue
        column_mapping.append({
            'column': c,
            'approach': approach_by_col[c],
            'direction': direction_by_col[c],
            'vehicle_type': MATRIX_VEHICLE_TYPE_MAP[vehicle_type],
        })

    return column_mapping


def _matrix_period_for_row(df: pd.DataFrame, row: int, block: Dict[str, int]) -> Optional[str]:
    """
    Decide whether a data row belongs to the AM or PM segment of its block.

    Matrix puts an "AM Totals" label between the AM and PM segments of every
    block, so the period is just whichever side of that line the row sits on.
    """
    am_totals_row = None
    for r in range(block['data_start'], block['data_end']):
        label = df.iloc[r, 1]
        if pd.notna(label) and str(label).strip() == 'AM Totals':
            am_totals_row = r
            break
    if am_totals_row is None:
        return None
    if row == am_totals_row:
        return None  # caller should skip totals rows
    return 'AM' if row < am_totals_row else 'PM'


def detect_blocks_and_reshape_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect data blocks in a Matrix Excel file and reshape into tidy format.

    Args:
        df: Raw DataFrame from the ``Raw data & Hourly Summary`` sheet

    Returns:
        Tidy DataFrame with one row per (direction, vehicle type, time slot)
    """
    metadata = _matrix_extract_metadata(df)
    intersection_name = metadata['intersection']
    date_obj = metadata['date']

    blocks = _matrix_find_blocks(df)
    if not blocks:
        return pd.DataFrame()

    records: List[Dict[str, Any]] = []
    for block in blocks:
        column_mapping = _matrix_build_column_mapping(df, block)
        if not column_mapping:
            continue

        for r in range(block['data_start'], block['data_end']):
            label = df.iloc[r, 1]
            # Skip the AM Totals separator and any blank rows.
            if pd.isna(label):
                continue
            label_str = str(label).strip()
            if label_str in ('AM Totals', 'PM Totals') or label_str == '':
                continue

            period = _matrix_period_for_row(df, r, block)
            if period is None:
                continue

            start_time = df.iloc[r, 1]
            # Normalise to HH:MM:SS — Matrix stores datetime.time objects with
            # spurious microseconds (e.g. 06:15:00.001) due to Excel rounding.
            if hasattr(start_time, 'strftime'):
                time_str = start_time.strftime('%H:%M:%S')
            else:
                try:
                    time_str = pd.to_datetime(str(start_time)).strftime('%H:%M:%S')
                except (ValueError, TypeError):
                    continue

            for col_map in column_mapping:
                count_value = df.iloc[r, col_map['column']]
                if pd.isna(count_value):
                    continue
                records.append({
                    'Intersection': intersection_name,
                    'Approach': col_map['approach'],
                    'Turn': col_map['direction'],
                    'Period': period,
                    'Time': time_str,
                    'VehicleType': col_map['vehicle_type'],
                    'Count': count_value,
                    'Date': date_obj,
                })

    return pd.DataFrame(records)


def reformat_turning_counts_matrix(filepath: str) -> pd.DataFrame:
    """Parse a Matrix turning-count workbook into tidy format."""
    df = pd.read_excel(filepath, sheet_name=MATRIX_SHEET, header=None)
    return detect_blocks_and_reshape_matrix(df)


def reformat_turning_counts(filepath: str, sheet_name: str = DEFAULT_SHEET_NAME) -> pd.DataFrame:
    """
    Reformat turning count data from an Excel file to tidy format.

    Auto-detects the source format (Team Traffic vs Matrix) and dispatches to
    the appropriate parser. The ``sheet_name`` argument is only used for the
    Team Traffic path; Matrix files always read from ``MATRIX_SHEET``.
    """
    print(f"Processing: {filepath}")
    source = detect_source_format(filepath)
    if source == 'matrix':
        print("  format: Matrix")
        return reformat_turning_counts_matrix(filepath)
    print("  format: Team Traffic")
    return reformat_turning_counts_team_traffic(filepath, sheet_name=sheet_name)


def process_all_files(input_folder: str = INPUT_FOLDER, 
                      project_name: str = PROJECT_NAME,
                      output_base: str = OUTPUT_FOLDER,
                      study_area_geojson: str = STUDY_AREA_GEOJSON,
                      find_coordinates: bool = FIND_INTERSECTION_COORDS) -> None:
    """
    Process all Excel files in the input folder and generate output CSVs.
    
    Args:
        input_folder: Folder containing input Excel files
        project_name: Name of the project for output files
        output_base: Base output folder path
        study_area_geojson: Path to GeoJSON file for intersection coordinate finding
        find_coordinates: Whether to find intersection coordinates using OSM data
    """
    # Find all xlsx files
    xlsx_files = glob.glob(os.path.join(input_folder, '*.xlsx'))
    
    if not xlsx_files:
        print(f"No .xlsx files found in {input_folder}")
        sys.exit(1)
    
    print(f"Found {len(xlsx_files)} .xlsx file(s) to process")
    
    # Master dataframe to hold all rows from all files
    all_data = pd.DataFrame()
    
    # Process each file
    for file_path in xlsx_files:
        try:
            file_data = reformat_turning_counts(file_path)
            all_data = pd.concat([all_data, file_data], ignore_index=True)
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            continue
    
    if all_data.empty:
        print("No data was processed successfully.")
        sys.exit(1)

    # add a time end column based on Time column + 15 minutes
    # Convert Time to datetime for calculation, handling both string and time objects
    if all_data['Time'].dtype == 'object':
        # Check if it's already time objects or strings
        if isinstance(all_data['Time'].iloc[0], str):
            time_as_datetime = pd.to_datetime(all_data['Time'], format='mixed')
        else:
            # It's time objects, combine with today's date
            time_as_datetime = pd.to_datetime(all_data['Time'].astype(str))
    else:
        time_as_datetime = pd.to_datetime(all_data['Time'].astype(str))
    
    all_data['TimeEnd'] = (time_as_datetime + pd.Timedelta(minutes=15)).dt.strftime('%H:%M:%S')
    
    print(f"Total records collected: {len(all_data)}")
    
    # Export to CSV
    output_folder = os.path.join(output_base, project_name, 'turning_counts')
    os.makedirs(output_folder, exist_ok=True)
    
    output_file = os.path.join(output_folder, f'{project_name}_turning_counts.csv')
    all_data.to_csv(output_file, index=False)
    print(f"Turning counts exported to {output_file}")
    
    # Create dataframe of unique intersection, approach and turn
    unique_intersections = all_data[['Intersection', 'Approach', 'Turn']].drop_duplicates()
    unique_intersections = unique_intersections.reset_index(drop=True)
    
    # Find intersection coordinates if requested
    if find_coordinates and os.path.exists(study_area_geojson):
        print("\n=== Finding intersection coordinates ===")
        try:
            coords_df = find_intersection_coordinates(unique_intersections, study_area_geojson)
            # Merge coordinates back into unique_intersections on the Intersection column
            unique_intersections = unique_intersections.merge(
                coords_df, 
                on='Intersection', 
                how='left'
            )
        except ImportError as e:
            print(f"Warning: Could not import intersection finder: {e}")
            print("Skipping coordinate finding. Install required packages: osmnx, geopandas, fuzzywuzzy")
        except Exception as e:
            print(f"Warning: Error finding intersection coordinates: {e}")
            print("Continuing without coordinates...")
    elif find_coordinates:
        print(f"\nWarning: Study area GeoJSON not found at {study_area_geojson}")
        print("Skipping coordinate finding.")
    
    # Export to CSV
    unique_output_file = os.path.join(output_folder, f'{project_name}_unique_turns.csv')
    unique_intersections.to_csv(unique_output_file, index=False)
    print(f"Unique turns exported to {unique_output_file}")


def main() -> None:
    """Main entry point for the script."""
    process_all_files()


if __name__ == "__main__":
    main()