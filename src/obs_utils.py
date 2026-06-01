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

        # Find anchor rows
        survey_rows = raw[raw[0].astype(str).str.contains("Survey Da", na=False)].index.tolist()
        time_rows = raw[raw[0].astype(str).str.match(TIME_REGEX, na=False)].index.tolist()

        if not survey_rows or not time_rows:
            raise ValueError("Could not locate 'Survey Days/Dates' or time rows; check the sheet format.")

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

def reformat_tomtom_data(
        traveltime_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Reformat the TomTom travel time data into a tidy format.
    """
    # Melt from wide to long
    long_df = traveltime_df.melt(
        id_vars=["Distance along route (m)", "Route", "Time"],  # keep these as identifiers
        value_vars=[
            "15th Percentile Travel Time [s]",
            "50th Percentile Travel Time [s]",
            "85th Percentile Travel Time [s]"
        ],                          # travel time columns
        var_name="Percentile",
        value_name="Cumulative Travel Time (s)",
    )

    # Extract just the percentile from the column name
    # e.g. "15th Percentile Travel Time [s]" -> "15th"
    long_df["Percentile"] = (
        long_df["Percentile"]
        .str.replace(" Percentile Travel Time [s]", "", regex=False)
        .str.strip()
    )

    # Tidy up column names / order
    long_df = long_df.rename(columns={"Distance along route (m)": "cumulative_distance", "Route": "route"})
    long_df['scenario_name'] = 'obs_' + long_df['Percentile']
    long_df['year'] = 2025
    long_df['year_scenario'] = long_df['year'].astype(str) + ' ' + long_df['scenario_name']
    long_df['vehicle_type'] = 'All Vehicles'

    # rename Cumulative Travel Time (s) to travel_time
    long_df = long_df.rename(columns={"Cumulative Travel Time (s)": "average_ttime"})
    # drop the Percentile column
    long_df = long_df.drop(columns=['Percentile'])

    return long_df

def import_tomtom_data_average(tomtom_path):
    """
    Import TomTom data from a CSV file.
    
    Parameters:
    tomtom_path (str): Path to the TomTom CSV file.
    
    Returns:
    DataFrame: DataFrame containing the TomTom data.
    """
    
    xlsx = pd.ExcelFile(tomtom_path)

    # Filter sheets that match the pattern {Route}-CumulativeTravelTimes
    sheets = [sheet for sheet in xlsx.sheet_names if sheet.endswith("-CumulativeTravelTimes")]

    # Read and concatenate all matching sheets into a single DataFrame
    combined_df = pd.DataFrame()

    for sheet in sheets:
        # --- Extract route name from A1 ---
        raw = pd.read_excel(tomtom_path, sheet_name=sheet, header=None)
        a1_value = str(raw.iloc[0, 0])
        route_name = re.sub(r"^Cumulative Travel time along route\s*", "", a1_value).strip()
        df = pd.read_excel(xlsx, sheet_name=sheet, skiprows=4)  # header starts at row 5
        df["route"] = route_name
        combined_df = pd.concat([combined_df, df], ignore_index=True)


    # drop the Segment ID, New Segment ID, and Speed Limit columns
    combined_df = combined_df.drop(columns=["Segment ID", "New Segment ID", "Speed Limit(kph)"])


    # Dynamically find all cumulative travel time columns
    value_cols = [c for c in combined_df.columns if c.startswith("Cumulative Travel Time (s)")]

    # Melt from wide to long
    long_df = combined_df.melt(
        id_vars=["Distance along route (m)", "route"],  # keep these as identifiers
        value_vars=value_cols,                          # dynamically detected time columns
        var_name="raw_time",
        value_name="Cumulative Travel Time (s)",
    )

    # Extract just the time window from the column name
    # e.g. "Cumulative Travel Time (s) 6:00-7:00" -> "6:00-7:00"
    long_df["Time"] = (
        long_df["raw_time"]
        .str.replace("Cumulative Travel Time (s)", "", regex=False)
        .str.strip()
    )

    # Tidy up column names / order
    long_df = long_df.drop(columns=["raw_time"])
    long_df = long_df.rename(columns={"Distance along route (m)": "Distance Along Route (m)"})
    long_df = long_df[["Distance Along Route (m)", "Time", "Cumulative Travel Time (s)", "route"]]

    # ---- Add (0 m, 0 s) row for each route + Time ----
    unique_pairs = long_df[["route", "Time"]].drop_duplicates()

    zero_rows = []
    for _, row in unique_pairs.iterrows():
        zero_rows.append({
            "Distance Along Route (m)": 0,
            "Time": row["Time"],
            "Cumulative Travel Time (s)": 0,
            "route": row["route"],
        })

    zero_df = pd.DataFrame(zero_rows)

    # Combine and sort
    final_df = pd.concat([long_df, zero_df], ignore_index=True)

    final_df = final_df.sort_values(
        by=["route", "Time", "Distance Along Route (m)"]
    ).reset_index(drop=True)

    # add the scenario name
    final_df["scenario_name"] = "obs_avg"
    final_df["year"] = 2025
    final_df["year_scenario"] = final_df["year"].astype(str) + " " + final_df["scenario_name"]
    final_df["vehicle_type"] = "All Vehicles"

    # rename Cumulative Travel Time (s) to average_ttime
    final_df = final_df.rename(columns={"Cumulative Travel Time (s)": "average_ttime", "Distance Along Route (m)": "cumulative_distance"})

    return final_df


def process_tomtom_data(
        input_path: str,
        output_hours_config: pd.DataFrame
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

    reformatted_df = reformat_tomtom_data(processed_df)
    print("Reformatted TomTom data.")

    # get the average travel time for each route and time
    average_df = import_tomtom_data_average(input_path)
    # add in the average travel time columns to the processed_df
    reformatted_df = pd.concat([reformatted_df, average_df], ignore_index=True)


    def fmt(t):
        dt = pd.to_datetime(t, format='%H:%M:%S')
        return f"{dt.hour}:{dt.minute:02d}"

    output_hours_config["Time"] = (
        output_hours_config["start_time"].apply(fmt)
        + "-" +
        output_hours_config["end_time"].apply(fmt)
    )
    output_hours_config = output_hours_config[['period', 'Time']]
    # merge in the period column from output_hours_config
    reformatted_df = pd.merge(reformatted_df, output_hours_config, on='Time', how='left')
    # rename period to hour
    reformatted_df = reformatted_df.rename(columns={'period': 'hour'})

    # drop the Time column
    reformatted_df = reformatted_df.drop(columns=['Time'])

    # drop rows where hour is NaN
    reformatted_df = reformatted_df[~reformatted_df['hour'].isna()]

    # convert average_ttime to minutes
    reformatted_df['average_ttime'] = reformatted_df['average_ttime'] / 60

    # Save to CSV
    # reformatted_df.to_csv(output_csv, index=False)
    # print(f"Saved processed TomTom data to {output_csv}.")

    return reformatted_df

def retrieve_obs_counts(
        input_path: str,
        output_hours_config: pd.DataFrame,
        obs_class_map_config: pd.DataFrame,
        type: str='turn'
    ) -> pd.DataFrame:
    
    
    counts = pd.read_csv(input_path)

    # keep only rows where VehicleType is Cars, Trucks or Buses
    counts = counts[counts['VehicleType'].isin(['Cars', 'Trucks', 'Buses'])]

    # rename turnObjectId to oid
    counts = counts.rename(columns={'turnObjectId': 'oid'})
    # rename Count to obs
    counts = counts.rename(columns={'Count': 'total_count'})

    # convert StartTime and EndTime to datetime.time
    counts['StartTime'] = pd.to_datetime(counts['StartTime'], format='%H:%M:%S').dt.time
    counts['EndTime'] = pd.to_datetime(counts['EndTime'], format='%H:%M:%S').dt.time

    all_data = pd.DataFrame()

    for _, row in output_hours_config.iterrows():
        peak = row['peak']
        period = row['period']
        start_time = row['start_time']
        end_time = row['end_time']

        filt_counts = counts[(counts['StartTime'] >= start_time) & (counts['StartTime'] < end_time)]
        filt_counts['period'] = period
        filt_counts['peak'] = peak

        # aggregate by Intersection, Approach, Turn, oid, period, peak, VehicleType summing total_count
        agg_counts = filt_counts.groupby(['Intersection', 'Approach', 'Turn', 'oid', 'period', 'peak', 'VehicleType'], as_index=False)['total_count'].sum()

        all_vehicle_count = filt_counts.groupby(['Intersection', 'Approach', 'Turn', 'oid', 'period', 'peak'], as_index=False)['total_count'].sum()
        all_vehicle_count['VehicleType'] = 'All Vehicles'

        all_data = pd.concat([all_data, agg_counts, all_vehicle_count], ignore_index=True)


    all_data['location'] = all_data['Intersection'] + ' / ' + all_data['Approach'] + ' / ' + all_data['Turn']
    all_data['year'] = 2025
    all_data['scenario_name'] = 'Obs'
    all_data['scenario'] = all_data['year'].astype(str) + ' ' + all_data['scenario_name'] + ' ' + all_data['peak']
    all_data = all_data.drop(columns=['Intersection', 'Approach', 'Turn'])
    # rename total_count to obs
    all_data = all_data.rename(columns={'total_count': 'obs'})

    # merge in the obs_class_map_config on VehicleType
    all_data = pd.merge(all_data, obs_class_map_config, left_on='VehicleType', right_on='vehicle_type', how='left')

    # group by oid, period, peak, location, class and sum obs
    all_data = all_data.groupby(['oid', 'period', 'peak', 'location', 'class'], as_index=False)['obs'].sum()
    # rename period to hour
    all_data = all_data.rename(columns={'period': 'hour'})




    dyn_agg_counts = counts.groupby(['Intersection', 'Approach', 'Turn', 'oid', 'Period', 'StartTime', 'EndTime'], as_index=False)['total_count'].sum()
    dyn_agg_counts['VehicleType'] = 'All Vehicles'
    dyn_all_data = pd.concat([counts, dyn_agg_counts], ignore_index=True)
    # create a 'location' column by combining Intersection, Approach, Turn
    dyn_all_data['location'] = dyn_all_data['Intersection'] + ' / ' + dyn_all_data['Approach'] + ' / ' + dyn_all_data['Turn']

    # rename Period to peak
    dyn_all_data = dyn_all_data.rename(columns={'Period': 'peak'})
    # create additional scenario columns
    dyn_all_data['year'] = 2025
    dyn_all_data['scenario_name'] = 'Obs'
    dyn_all_data['scenario'] = dyn_all_data['year'].astype(str) + ' '+ dyn_all_data['scenario_name'] + ' ' + dyn_all_data['peak']
    # drop the Intersection, Approach, Turn columns
    dyn_all_data = dyn_all_data.drop(columns=['Intersection', 'Approach', 'Turn'])
    # rename total_count to obs
    dyn_all_data = dyn_all_data.rename(columns={'total_count': 'obs'})
        

    return all_data, dyn_all_data





