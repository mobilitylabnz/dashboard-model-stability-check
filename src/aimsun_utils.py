import pandas as pd
import numpy as np
import sqlite3
import geopandas as gpd
from shapely.geometry import LineString
from scipy.interpolate import interp1d

def extract_rows(cursor, query):
	cursor.execute(query)
	rows = cursor.fetchall()
	# convert to list of lists
	rows = [list(row) for row in rows]

	return rows

def extract_sections(replication_id, database_path, output_hours, simtype):
    # Connect to the SQLite database
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()

    sectionData = []

    header = ['did', 'oid', 'eid', 'sid',
              'total_count', 'average_ttime', 'average_speed',
              'hour', 'time_length']
    sectionData.append(header)

    for hr in output_hours:
        ent_start = output_hours[hr]['start']
        ent_end = output_hours[hr]['end']
        time_length = output_hours[hr]['time_length']

        query = f"""
            SELECT
                did,
                oid,
                eid,
                sid,
                SUM(count) AS total_count,
                SUM(ttime * count) * 1.0 / SUM(count) AS average_ttime,
                SUM(speed * count) * 1.0 / SUM(count) AS average_speed,
                '{hr}' AS hour,
                {time_length} AS time_length
            FROM {simtype}SECT
            WHERE did = {replication_id}
              AND ent BETWEEN {ent_start} AND {ent_end}
              AND count <> 0
            GROUP BY did, oid, eid, sid;
        """

        sectionData.extend(extract_rows(cursor, query))

    return sectionData

def extract_dynamic_sections(replication_id, database_path, unique_oids, simtype):
     
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()

    dynamicSectionData = []

    header = ['did', 'oid', 'eid', 'sid', 'ent', 'count', 'ttime', 'speed']

    dynamicSectionData.append(header)
    query = f"""SELECT did, oid, eid, sid, ent, count, ttime, speed
                FROM {simtype}SECT
                WHERE did = {replication_id} 
                AND oid IN ({','.join(map(str, unique_oids))});"""
    dynamicSectionData.extend(extract_rows(cursor, query))

    return dynamicSectionData

def extract_lanes(replication_id, database_path, output_hours, simtype):
    # Connect to the SQLite database
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()

    laneData = []

    header = ['did', 'oid', 'eid', 'sid', 'lane',
              'total_count', 'average_ttime', 'average_speed',
              'hour', 'time_length']
    laneData.append(header)

    for hr in output_hours:
        ent_start = output_hours[hr]['start']
        ent_end = output_hours[hr]['end']
        time_length = output_hours[hr]['time_length']

        query = f"""
            SELECT
                did,
                oid,
                eid,
                sid,
                lane,
                SUM(count) AS total_count,
                SUM(ttime * count) * 1.0 / SUM(count) AS average_ttime,
                SUM(speed * count) * 1.0 / SUM(count) AS average_speed,
                '{hr}' AS hour,
                {time_length} AS time_length
            FROM {simtype}LANE
            WHERE did = {replication_id}
              AND ent BETWEEN {ent_start} AND {ent_end}
              AND count <> 0
            GROUP BY did, oid, eid, sid, lane;
        """

        laneData.extend(extract_rows(cursor, query))

    return laneData

def extract_turns(replication_id, database_path, output_hours, simtype):
    # Connect to the SQLite database
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()

    turnData = []

    header = ['did', 'oid', 'eid', 'sid',
              'total_count', 'average_ttime', 'average_speed',
              'hour', 'time_length']
    turnData.append(header)

    for hr in output_hours:
        ent_start = output_hours[hr]['start']
        ent_end = output_hours[hr]['end']
        time_length = output_hours[hr]['time_length']

        query = f"""
            SELECT
                did,
                oid,
                eid,
                sid,
                SUM(count) AS total_count,
                SUM(ttime * count) * 1.0 / SUM(count) AS average_ttime,
                SUM(speed * count) * 1.0 / SUM(count) AS average_speed,
                '{hr}' AS hour,
                {time_length} AS time_length
            FROM {simtype}TURN
            WHERE did = {replication_id}
              AND ent BETWEEN {ent_start} AND {ent_end}
              AND count <> 0
            GROUP BY did, oid, eid, sid;
        """

        turnData.extend(extract_rows(cursor, query))

    return turnData

def extract_dynamic_turns(replication_id, database_path, unique_oids, simtype):
        
        conn = sqlite3.connect(database_path)
        cursor = conn.cursor()
    
        dynamicTurnData = []
    
        header = ['did', 'oid', 'eid', 'sid', 'ent', 'count', 'ttime', 'speed']
    
        dynamicTurnData.append(header)
        query = f"""SELECT did, oid, eid, sid, ent, count, ttime, speed
                    FROM {simtype}TURN
                    WHERE did = {replication_id} 
                    AND oid IN ({','.join(map(str, unique_oids))});"""
        dynamicTurnData.extend(extract_rows(cursor, query))
    
        return dynamicTurnData



def extract_system(replication_id, database_path, output_hours, simtype):
    

    # Connect to the SQLite database
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()

    
    systemData = []

    header  = ['did', 'sid', 'flow', 'traveltime', 'totalWaitingTime', 'virtualQueue', 'meanVirtualQueue', 'hour', 'time_length']
    systemData.append(header)

    for hr in output_hours:
        ent_start = output_hours[hr]['start']
        ent_end = output_hours[hr]['end']
        time_length = output_hours[hr]['time_length']
        query = f"""SELECT did, sid, 
                    AVG(flow) as flow,
                    SUM(traveltime) as traveltime,
                    SUM(totalWaitingTime) as totalWaitingTime,
                    SUM(vWait) as virtualQueue,
                    AVG(qvmean) as meanVirtualQueue,
                    '{hr}' as hour,
                    {time_length} as time_length
                FROM {simtype}SYS
                WHERE did = {replication_id} 
                    AND ent between {ent_start} and {ent_end}
                GROUP BY did, sid;"""
        systemData.extend(extract_rows(cursor, query))

    return systemData

def extract_system_full(replication_id, database_path, output_hours, simtype):
    # Connect to the SQLite database
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()

    systemData = []

    # Get column names first
    cursor.execute(f"PRAGMA table_info({simtype}SYS)")
    columns = cursor.fetchall()
    header = [column[1] for column in columns]  # column[1] is the column name
    systemData.append(header)

    # Now get the data
    query = f"""
            SELECT *
            FROM {simtype}SYS
            WHERE did = {replication_id};
            """
    systemData.extend(extract_rows(cursor, query))

    conn.close()
    return systemData


def interpolate_route_scenarios(df):
    """
    Interpolate values so all scenarios within each route have the same cumulative distances.
    Works with long format data where hour is a column.
    Only interpolates within the range of each scenario - does not extrapolate beyond.
    """
    
    result_dfs = []
    
    # Group by route, vehicle_type, and hour
    for (route, vehicle_type, hour), route_group in df.groupby(['route', 'vehicle_type', 'hour']):
        # print(f"Processing route: {route}, vehicle_type: {vehicle_type}, hour: {hour}")
        
        # Get all unique cumulative distances for this route/vehicle_type/hour combination
        # (use the longest route's distances)
        all_distances = sorted(route_group['cumulative_distance'].unique())
        
        scenario_dfs = []
        
        # Process each scenario within this route/vehicle_type/hour
        for scenario in route_group['year_scenario'].unique():
            scenario_data = route_group[route_group['year_scenario'] == scenario].copy()
            scenario_data = scenario_data.sort_values('cumulative_distance')
            
            # Get this scenario's max distance (don't extrapolate beyond this)
            scenario_max_distance = scenario_data['cumulative_distance'].max()
            
            # Only interpolate for distances within this scenario's range
            scenario_distances = [d for d in all_distances if d <= scenario_max_distance]
            
            # Create new dataframe with scenario-specific distances
            new_data = pd.DataFrame({'cumulative_distance': scenario_distances})
            
            # Add categorical columns
            new_data['route'] = route
            new_data['year_scenario'] = scenario
            new_data['vehicle_type'] = vehicle_type
            new_data['hour'] = hour
            
            # Interpolate numeric columns (average_ttime)
            existing_distances = scenario_data['cumulative_distance'].values
            
            if 'average_ttime' in scenario_data.columns:
                existing_values = scenario_data['average_ttime'].values
                
                # Handle NaN values in existing data
                valid_mask = ~np.isnan(existing_values)
                if valid_mask.sum() > 1:  # Need at least 2 points to interpolate
                    valid_distances = existing_distances[valid_mask]
                    valid_values = existing_values[valid_mask]
                    
                    # Create interpolation function (no extrapolation)
                    f = interp1d(valid_distances, valid_values, 
                               kind='linear', bounds_error=False, fill_value=np.nan)
                    
                    # Interpolate for scenario-specific distances
                    new_data['average_ttime'] = f(scenario_distances)
                else:
                    # If not enough valid points, fill with NaN
                    new_data['average_ttime'] = np.nan
            else:
                new_data['average_ttime'] = np.nan
            
            # For non-numeric columns, we'll use the closest existing value
            for col in ['year', 'scenario_name', 'object_id', 'length', 'object_type', 'oid']:
                if col in scenario_data.columns:
                    # For each target distance, find the closest existing distance and use its value
                    interpolated_values = []
                    for target_dist in scenario_distances:
                        # Find the closest existing distance
                        closest_idx = np.argmin(np.abs(existing_distances - target_dist))
                        interpolated_values.append(scenario_data.iloc[closest_idx][col])
                    new_data[col] = interpolated_values
                else:
                    new_data[col] = None
            
            scenario_dfs.append(new_data)
        
        # Combine all scenarios for this route/vehicle_type/hour
        route_result = pd.concat(scenario_dfs, ignore_index=True)
        result_dfs.append(route_result)
    
    # Combine all routes
    final_result = pd.concat(result_dfs, ignore_index=True)
    
    # Reorder columns to match original
    original_cols = df.columns.tolist()
    final_result = final_result[original_cols]
    
    return final_result


def interpolate_route_productivity(df):
    """
    Interpolate productivity values so all scenarios within each route have the same cumulative lengths.
    Preserves step function structure with proper duplicate handling.
    """
    import numpy as np
    from scipy.interpolate import interp1d
    
    result_dfs = []
    
    # Group by route and hour
    for (route, hour), route_group in df.groupby(['route', 'hour']):
        # Find the scenario with the most points (most complete step structure)
        reference_scenario = None
        max_points = 0
        
        for year_scenario in route_group['year_scenario'].unique():
            scenario_data = route_group[route_group['year_scenario'] == year_scenario]
            if len(scenario_data) > max_points:
                max_points = len(scenario_data)
                reference_scenario = year_scenario
        
        # Get reference cumulative distances (WITH duplicates for step function)
        reference_data = route_group[route_group['year_scenario'] == reference_scenario].copy()
        reference_data = reference_data.sort_values('cumulative_distance').reset_index(drop=True)
        all_lengths = reference_data['cumulative_distance'].values  # Keep ALL values including duplicates!
        
        scenario_dfs = []
        
        # Process each scenario
        for year_scenario in route_group['year_scenario'].unique():
            scenario_data = route_group[route_group['year_scenario'] == year_scenario].copy()
            scenario_data = scenario_data.sort_values('cumulative_distance').reset_index(drop=True)
            
            # If this IS the reference scenario, just use it as-is
            if year_scenario == reference_scenario:
                scenario_dfs.append(scenario_data[['scenario', 'year', 'peak', 'hour', 
                                                   'year_scenario', 'route', 
                                                   'cumulative_distance', 'productivity']])
                continue
            
            # For other scenarios, interpolate to match reference structure
            existing_lengths = scenario_data['cumulative_distance'].values
            existing_values = scenario_data['productivity'].values
            
            # Remove NaN values
            valid_mask = ~np.isnan(existing_values)
            valid_lengths = existing_lengths[valid_mask]
            valid_values = existing_values[valid_mask]
            
            if len(valid_lengths) < 2:
                # Not enough data, skip this scenario or fill with constant
                continue
            
            # Get unique lengths for interpolation (remove duplicates temporarily)
            unique_valid_lengths = []
            unique_valid_values = []
            for i, length in enumerate(valid_lengths):
                if i == 0 or length != valid_lengths[i-1]:
                    unique_valid_lengths.append(length)
                    unique_valid_values.append(valid_values[i])
            
            # Create interpolation function
            f = interp1d(unique_valid_lengths, unique_valid_values,
                        kind='previous', bounds_error=False,
                        fill_value=(unique_valid_values[0], unique_valid_values[-1]))
            
            # Apply to reference lengths (preserving duplicates)
            interpolated_values = f(all_lengths)
            
            # Create new dataframe
            first_row = scenario_data.iloc[0]
            new_data = pd.DataFrame({
                'scenario': first_row['scenario'],
                'year': first_row['year'],
                'peak': first_row['peak'],
                'hour': hour,
                'year_scenario': year_scenario,
                'route': route,
                'cumulative_distance': all_lengths,
                'productivity': interpolated_values
            })
            
            scenario_dfs.append(new_data)
        
        # Combine all scenarios for this route/hour
        if scenario_dfs:
            route_result = pd.concat(scenario_dfs, ignore_index=True)
            result_dfs.append(route_result)
    
    # Combine all routes
    final_result = pd.concat(result_dfs, ignore_index=True)
    
    return final_result

def concatenate_linestring_coords(geom):
    """Extract all coordinates from MultiLineString or LineString and create single LineString"""
    all_coords = []
    
    if geom.geom_type == 'LineString':
        all_coords.extend(geom.coords)
    elif geom.geom_type == 'MultiLineString':
        for line in geom.geoms:
            all_coords.extend(line.coords)
    
    return LineString(all_coords) if len(all_coords) >= 2 else geom