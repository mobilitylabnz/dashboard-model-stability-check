import pandas as pd
import re

TIME_REGEX = r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$"

def extract_route_percentiles_each_segment(path: str) -> pd.DataFrame:
    """
    Extracts all time-window tables from the given sheet and appends a 'Time Window' column.
    
    Logic:
    - Locate repeating blocks that start with 'Survey Dates' (col A), followed by a time row (e.g., '6:00-7:00'),
      then a header row (e.g., 'Segment ID', 'New Segment ID', etc.).
    - The table runs until just before the next 'Survey Dates' block (or the end of the sheet).
    - Works even if the number of rows per block or the number of time blocks changes.
    """
    xlsx = pd.ExcelFile(path)

    combined_df = pd.DataFrame()

    # Filter sheets that match the pattern {Route}-PercentilesEachSegment
    sheets = [sheet for sheet in xlsx.sheet_names if sheet.endswith("-PercentilesEachSegment")]

    for sheet in sheets:
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        # --- Extract route name from A1 ---
        a1_value = str(raw.iloc[0, 0])
        route_name = re.sub(r"^Speed percentiles for each segment\s*", "", a1_value).strip()

        # Find anchor rows ("Survey Dates" in file 1, "Survey Days" in file 2)
        survey_rows = raw[raw[0].astype(str).str.contains(r"Survey Da(tes|ys)", na=False, regex=True)].index.tolist()
        time_rows = raw[raw[0].astype(str).str.match(TIME_REGEX, na=False)].index.tolist()

        if not survey_rows or not time_rows:
            raise ValueError("Could not locate 'Survey Dates/Days' or time rows; check the sheet format.")

        # Ensure pairing (time immediately after survey)
        # If counts mismatch, assume time row is survey_row + 1
        if len(time_rows) != len(survey_rows):
            time_rows = [r + 1 for r in survey_rows if (r + 1) in raw.index and
                        re.match(TIME_REGEX, str(raw.iloc[r + 1, 0]))]

        tables = []
        for i, trow in enumerate(time_rows):
            time_str = str(raw.iloc[trow, 0]).strip()
            header_idx = trow + 1

            # End of block is the next 'Survey Dates' row, or end of sheet
            if i < len(survey_rows) - 1:
                end_exclusive = survey_rows[i + 1]
            else:
                end_exclusive = len(raw)

            sub = raw.iloc[header_idx:end_exclusive].copy()
            if sub.empty:
                continue

            # First row is the header row for this block
            sub.columns = sub.iloc[0].tolist()
            sub = sub.iloc[1:].reset_index(drop=True)

            # Drop columns that are entirely NaN (keeps it clean even if shapes vary)
            sub = sub.dropna(how="all")

            # Add time window column
            sub["Time"] = time_str
            sub["Route"] = route_name
            tables.append(sub)


        if not tables:
            raise ValueError("No tables extracted; the sheet format may have changed.")
        
        out = pd.concat(tables, ignore_index=True)
        # keep only columns we need
        columns_to_keep = ["Distance along route (m)", "15th Percentile Speed [kph]", "50th Percentile Speed [kph]","85th Percentile Speed [kph]","Time", "Route"] 
        out = out[columns_to_keep]
        combined_df = pd.concat([combined_df, out], ignore_index=True)

    return combined_df


def calculate_travel_time(speed_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate travel time for each segment based on speed percentiles for each time and route.
    """
    # Calculate distance for each segment
    # For the first row in each Route/Time group, use the 'Distance along route (m)' value
    # For subsequent rows, use the difference from the previous row
    speed_df['Distance (m)'] = speed_df.groupby(['Route', 'Time'])['Distance along route (m)'].transform(
        lambda x: x.diff().fillna(x)
    )

    # Calculate travel time for each speed percentile
    for percentile in ['15th Percentile Speed [kph]', '50th Percentile Speed [kph]', '85th Percentile Speed [kph]']:
        travel_time_col = percentile.replace('Speed [kph]', 'Travel Time [s]')
        # Convert speed from kph to m/s by dividing by 3.6
        speed_mps = pd.to_numeric(speed_df[percentile], errors='coerce') / 3.6
        # Travel time (s) = Distance (m) / Speed (m/s)
        speed_df[travel_time_col] = speed_df['Distance (m)'] / speed_mps

    # swap the 15th and 85th percentile travel time names
    speed_df.rename(columns={
        '15th Percentile Travel Time [s]': '85th Percentile Travel Time [s]',
        '85th Percentile Travel Time [s]': '15th Percentile Travel Time [s]',
    }, inplace=True)

    # add in a row for 0 distance with 0 travel time for each route and time
    zero_rows = speed_df.groupby(['Route', 'Time']).first().reset_index()
    zero_rows['Distance along route (m)'] = 0
    zero_rows['Distance (m)'] = 0
    for percentile in ['15th Percentile Travel Time [s]', '50th Percentile Travel Time [s]', '85th Percentile Travel Time [s]']:
        zero_rows[percentile] = 0

    # Concatenate zero rows before sorting
    speed_df = pd.concat([speed_df, zero_rows], ignore_index=True)

    # Sort by Route, Time, and Distance
    speed_df = speed_df.sort_values(by=['Route', 'Time', 'Distance along route (m)']).reset_index(drop=True)

    # Convert travel time columns to numeric before cumsum
    for percentile in ['15th Percentile Travel Time [s]', '50th Percentile Travel Time [s]', '85th Percentile Travel Time [s]']:
        speed_df[percentile] = pd.to_numeric(speed_df[percentile], errors='coerce')
    
    # Now calculate the cumulative travel time for each route and time
    for percentile in ['15th Percentile Travel Time [s]', '50th Percentile Travel Time [s]', '85th Percentile Travel Time [s]']:
        speed_df[percentile] = speed_df.groupby(['Route', 'Time'])[percentile].cumsum()

    return speed_df


def process_tomtom_data(
        input_path: str,
        output_csv: str
    ):
    """
    Process TomTom data from the given Excel file and save the results to a CSV.
    """
    print("=" * 60)
    print("Processing TomTom Data")
    print("=" * 60)

    # Extract data
    extracted_df = extract_route_percentiles_each_segment(input_path)
    print(f"Extracted {len(extracted_df)} rows from TomTom data.")

    # Calculate travel times
    processed_df = calculate_travel_time(extracted_df)
    print("Calculated travel times for each segment.")

    # Save to CSV
    processed_df.to_csv(output_csv, index=False)
    print(f"Saved processed TomTom data to {output_csv}.")


def extract_cumulative_avg(path: str) -> pd.DataFrame:
    """
    Extract obs_avg from CumulativeTravelTimes sheets.
    Returns long-format DataFrame with scenario_name='obs_avg'.
    """
    xlsx = pd.ExcelFile(path)
    sheets = [s for s in xlsx.sheet_names if s.endswith("-CumulativeTravelTimes")]

    dfs = []
    for sheet in sheets:
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        route_name = re.sub(r"^Cumulative Travel time along route\s*", "", str(raw.iloc[0, 0])).strip()

        header = raw.iloc[4].tolist()
        data = raw.iloc[5:].copy()
        data.columns = header
        data = data.dropna(how="all").reset_index(drop=True)

        dist_col = "Distance along route (m)"
        time_cols = [
            c for c in data.columns
            if isinstance(c, str) and re.match(r"Cumulative Travel Time \(s\) \d", c)
        ]

        sub = data[[dist_col] + time_cols].copy()
        for c in sub.columns:
            sub[c] = pd.to_numeric(sub[c], errors="coerce")
        sub = sub.dropna(subset=[dist_col])

        # Prepend zero row (start of route)
        zero_row = {c: 0.0 for c in sub.columns}
        sub = pd.concat([pd.DataFrame([zero_row]), sub], ignore_index=True)

        melted = sub.melt(id_vars=[dist_col], value_vars=time_cols, var_name="Time", value_name="ttime_s")
        melted["Time"] = melted["Time"].str.replace(r"^Cumulative Travel Time \(s\) ", "", regex=True)
        melted["route"] = route_name
        melted["average_ttime"] = pd.to_numeric(melted["ttime_s"], errors="coerce")
        melted["scenario_name"] = "obs_avg"
        melted = melted.rename(columns={dist_col: "cumulative_distance"})
        dfs.append(melted[["cumulative_distance", "route", "Time", "average_ttime", "scenario_name"]])

    return pd.concat(dfs, ignore_index=True)


def reformat_percentiles_to_long(speed_df: pd.DataFrame) -> pd.DataFrame:
    """
    Reformat calculate_travel_time output to long format with scenario_name column.
    Travel times converted from seconds to minutes.
    """
    col_map = {
        "15th Percentile Travel Time [s]": "obs_15th",
        "50th Percentile Travel Time [s]": "obs_50th",
        "85th Percentile Travel Time [s]": "obs_85th",
    }

    parts = []
    for col, scenario in col_map.items():
        sub = speed_df[["Distance along route (m)", "Route", "Time", col]].copy()
        sub = sub.rename(columns={
            "Distance along route (m)": "cumulative_distance",
            "Route": "route",
            col: "average_ttime",
        })
        sub["average_ttime"] = pd.to_numeric(sub["average_ttime"], errors="coerce")
        sub["scenario_name"] = scenario
        parts.append(sub)

    return pd.concat(parts, ignore_index=True)


def process_tomtom_to_final_csv(
    input_paths: list,
    output_csv: str,
    year: int = 2025,
):
    """
    Process one or more TomTom Excel files into the final long-format CSV
    matching the tomtom_processed.csv schema.
    """
    print("=" * 60)
    print("Processing TomTom Data to Final CSV")
    print("=" * 60)

    all_parts = []
    for path in input_paths:
        print(f"  Processing: {path}")

        avg_df = extract_cumulative_avg(path)
        print(f"    obs_avg: {len(avg_df)} rows")

        perc_raw = extract_route_percentiles_each_segment(path)
        travel_df = calculate_travel_time(perc_raw)
        long_df = reformat_percentiles_to_long(travel_df)
        print(f"    obs_15th/50th/85th: {len(long_df)} rows")

        all_parts.append(avg_df)
        all_parts.append(long_df)

    final = pd.concat(all_parts, ignore_index=True)
    final["year"] = year
    final["year_scenario"] = final["year"].astype(str) + " " + final["scenario_name"]
    final["vehicle_type"] = "All Vehicles"
    final["hour"] = float("nan")

    final = final.sort_values(["scenario_name", "route", "Time", "cumulative_distance"]).reset_index(drop=True)

    final.to_csv(output_csv, index=False)
    print(f"Saved {len(final)} rows to {output_csv}")