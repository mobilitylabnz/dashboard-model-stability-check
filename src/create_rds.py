import pandas as pd


def create_rds(
        PROJECT_NAME: str,
        MATCH_TURNS: bool,
        MATCH_TUBES: bool
    ):
    """
    Create RDS files based on processed turn and tube counts.
    """
    if MATCH_TURNS:
        print("\n" + "=" * 60)
        print("STEP 3: Creating RDS for Turn Counts")
        print("=" * 60)
        # Read in the matched turn counts with model IDs
        turn_ids = pd.read_csv(f'outputs/{PROJECT_NAME}/turning_counts/matched_turn_counts.csv')
        # read in the full count data
        turn_data = pd.read_csv(f'outputs/{PROJECT_NAME}/turning_counts/{PROJECT_NAME}_turning_counts.csv')

        # merge turn_ids into turn_data on "Intersection", "Approach", "Turn"
        merged_turns = pd.merge(turn_data, turn_ids, on=["Intersection", "Approach", "Turn"], how="left")

        # remove rows that have no turnObjectId
        merged_turns = merged_turns[merged_turns['turnObjectId'].notna()]
        # drop the Latitude and Longitude columns
        merged_turns = merged_turns.drop(columns=['Latitude', 'Longitude'])
        # convert turnObjectId to integer
        merged_turns['turnObjectId'] = merged_turns['turnObjectId'].astype(int)

        # aggregate by Period, Time, VehicleType, Date, turnObjectId summing Count, and keeping first Intersection, Approach, Turn
        merged_turns = merged_turns.groupby(["Period", "Time", "VehicleType", "Date", "turnObjectId"], as_index=False).agg({'Count': 'sum', 'Intersection': 'first', 'Approach': 'first', 'Turn': 'first'})

        # average by Period, Time, VehicleType, turnObjectId and keep first Intersection, Approach, Turn
        merged_turns = merged_turns.groupby(["Period", "Time", "VehicleType", "turnObjectId"], as_index=False).agg({'Count': 'mean', 'Intersection': 'first', 'Approach': 'first', 'Turn': 'first'})

        # create EndTime column by adding 15 minutes to Time column
        merged_turns['EndTime'] = pd.to_datetime(merged_turns['Time'], format='%H:%M:%S') + pd.Timedelta(minutes=15)
        # convert EndTime back to string in HH:MM format
        merged_turns['EndTime'] = merged_turns['EndTime'].dt.strftime('%H:%M:%S')
        # rename Time to StartTime
        merged_turns = merged_turns.rename(columns={'Time': 'StartTime'})
        # reorder columns
        merged_turns = merged_turns[['Intersection', 'Approach', 'Turn', 'turnObjectId', 'Period', 'StartTime', 'EndTime', 'VehicleType', 'Count']]
        # save to csv
        merged_turns.to_csv(f'outputs/{PROJECT_NAME}/turning_counts/{PROJECT_NAME}_turning_counts_rds.csv', index=False)
        # create a version that aggregates vehicles together
        # first strip out any vehicleType not equal to "Car" or "Truck"
        merged_turns = merged_turns[merged_turns['VehicleType'].isin(['Cars', 'Trucks', 'Buses'])]
        # aggregate by Period, StartTime, EndTime, turnObjectId summing Count, and keeping first Intersection, Approach, Turn
        merged_turns_agg = merged_turns.groupby(["Period", "StartTime", "EndTime", "turnObjectId"], as_index=False).agg({'Count': 'sum', 'Intersection': 'first', 'Approach': 'first', 'Turn': 'first'})
        merged_turns_agg.to_csv(f'outputs/{PROJECT_NAME}/turning_counts/{PROJECT_NAME}_turning_counts_rds_all.csv', index=False)

    if MATCH_TUBES:
        print("\n" + "=" * 60)
        print("STEP 4: Creating RDS for Tube Counts")
        print("=" * 60)
        # Read in the matched tube counts with model IDs
        tube_ids = pd.read_csv(f'outputs/{PROJECT_NAME}/tube_counts/matched_tube_counts.csv')
        # read in the full count data
        tube_data = pd.read_csv(f'outputs/{PROJECT_NAME}/tube_counts/{PROJECT_NAME}_tube_counts_Weekday.csv')

        # merge tube_ids into tube_data on "Site" and "Direction"
        merged_tubes = pd.merge(tube_data, tube_ids, on=["Site", "Direction"], how="left")

        # remove rows that have no linkId 
        merged_tubes = merged_tubes[merged_tubes['linkId'].notna()]
        # remove columns Location, Latitude, Longitude, easting, northing
        merged_tubes = merged_tubes.drop(columns=['Location', 'Latitude', 'Longitude', 'easting', 'northing'])
        # convert linkId to integer
        merged_tubes['linkId'] = merged_tubes['linkId'].astype(int)

        # export to csv
        merged_tubes.to_csv(f'outputs/{PROJECT_NAME}/tube_counts/{PROJECT_NAME}_tube_counts_rds.csv', index=False)

        # aggregate by StartTime, EndTime, Site, Direction, linkId summing Count
        merged_tubes_agg = merged_tubes.groupby(["StartTime", "EndTime", "Site", "Direction", "linkId"], as_index=False).agg({'Volume': 'sum'})
        # export to csv
        merged_tubes_agg.to_csv(f'outputs/{PROJECT_NAME}/tube_counts/{PROJECT_NAME}_tube_counts_rds_all.csv', index=False)
