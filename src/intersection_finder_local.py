from typing import Optional, Tuple, Dict, Any, List
import statistics
import os
import hashlib
import csv
import re
import osmnx as ox
import geopandas as gpd
from shapely.geometry import Polygon
import pandas as pd
from fuzzywuzzy import fuzz, process

# Input Configuration
STUDY_AREA_GEOJSON = "inputs/NNR_cordon.geojson"
INPUT_CSV = "outputs/NewNorthRoad/turning_counts/NewNorthRoad_unique_turns.csv"
OUTPUT_CSV = "outputs/NewNorthRoad/NewNorthRoad_intersection_locations.csv"
CACHE_DIR = "network_cache"

# Global configuration
_CACHE_DIR = CACHE_DIR
_CACHED_NETWORK = None
_STUDY_AREA_POLYGON = None

def parse_intersection(s):
    if pd.isna(s):
        return None, []
    s = s.strip()
    
    # Remove day-of-week references in parentheses (e.g., "(Wed)", "(Thursday)")
    # Pattern matches common day abbreviations and full names
    day_pattern = r'\s*\((Mon|Tue|Wed|Thu|Thur|Fri|Sat|Sun|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\)\s*'
    s = re.sub(day_pattern, ' ', s, flags=re.IGNORECASE)
    s = s.strip()
    
    # 1) Protect numeric ranges (e.g., "41 - 42", "41–42", "41 — 42")
    #    by collapsing spaces and dash variety to a single hyphen.
    s = re.sub(r'(?<=\d)\s*[-–—]\s*(?=\d)', '-', s)

    # 2) Separator for roads: any dash type with optional spaces,
    #    but NOT between digits (so "41-42" survives).
    SEP = r'(?<!\d)\s*[-–—]\s*(?!\d)'

    # 3) Pull out a leading Site label if present
    site_label = None
    if re.match(r'(?i)^site\b', s):
        parts = re.split(SEP, s, maxsplit=1)
        site_label = parts[0].strip()
        rest = parts[1] if len(parts) > 1 else ''
    else:
        rest = s

    # 4) Split remaining into road names
    roads = [p.strip() for p in re.split(SEP, rest) if p.strip()]
    return site_label, roads

def format_unique_movements(df):
    # get only the unique values for intersections
    df = df.drop_duplicates(subset=['Intersection'])
    
    SEP = r'(?<!\d)\s*[-–—]\s*(?!\d)'  # regex to match hyphens/dashes not between digits

    parsed = df['Intersection'].apply(parse_intersection)
    df['SiteLabel'] = parsed.apply(lambda x: x[0])
    df['roads_list'] = parsed.apply(lambda x: x[1])

    max_roads = df['roads_list'].str.len().max() or 0
    road_cols = [f'Road_{i+1}' for i in range(max_roads)]
    df[road_cols] = pd.DataFrame(df['roads_list'].tolist(), index=df.index)

    cols_to_keep = ['Intersection'] + road_cols
    intersection_to_match = df[cols_to_keep].copy()
    # remove any duplicate ros of road_cols
    intersection_to_match = intersection_to_match.drop_duplicates()

    return intersection_to_match



def configure_osmnx():
    """Configure OSMnx settings"""
    ox.settings.use_cache = True
    ox.settings.log_console = False

def set_cache_directory(cache_dir: str = None):
    """Set the cache directory for network files"""
    global _CACHE_DIR
    if cache_dir is None:
        cache_dir = CACHE_DIR
    _CACHE_DIR = cache_dir
    os.makedirs(_CACHE_DIR, exist_ok=True)

def _get_polygon_hash(polygon) -> str:
    """Generate a hash for the polygon to use as cache key"""
    coords_str = str(list(polygon.exterior.coords))
    return hashlib.md5(coords_str.encode()).hexdigest()[:12]

def _get_cache_filepath(polygon) -> str:
    """Get the cache file path for a given polygon"""
    polygon_hash = _get_polygon_hash(polygon)
    return os.path.join(_CACHE_DIR, f"network_{polygon_hash}.gpkg")

def _save_network_to_cache(network: gpd.GeoDataFrame, polygon) -> bool:
    """Save network to cache file"""
    try:
        cache_filepath = _get_cache_filepath(polygon)
        network.to_file(cache_filepath, driver='GPKG')
        print(f"Saved network to cache: {cache_filepath}")
        return True
    except Exception as e:
        print(f"Error saving network to cache: {e}")
        return False

def _load_network_from_cache(polygon) -> Optional[gpd.GeoDataFrame]:
    """Load network from cache file"""
    try:
        cache_filepath = _get_cache_filepath(polygon)
        if os.path.exists(cache_filepath):
            print(f"Loading network from cache: {cache_filepath}")
            network = gpd.read_file(cache_filepath)
            print(f"Loaded {len(network)} road segments from cache")
            return network
        else:
            print("No cached network found")
            return None
    except Exception as e:
        print(f"Error loading network from cache: {e}")
        return None

def load_study_area_geojson(geojson_file: str) -> Optional[Polygon]:
    """Read study area polygon from GeoJSON file"""
    try:
        gdf = gpd.read_file(geojson_file)
        
        if len(gdf) == 0:
            print(f"No geometries found in {geojson_file}")
            return None
        
        if len(gdf) > 1:
            print(f"Found {len(gdf)} geometries, creating union")
            geometry = gdf.geometry.unary_union
        else:
            geometry = gdf.geometry.iloc[0]
        
        if hasattr(geometry, 'geom_type'):
            if geometry.geom_type == 'Polygon':
                return geometry
            elif geometry.geom_type == 'MultiPolygon':
                largest_poly = max(geometry.geoms, key=lambda p: p.area)
                print(f"MultiPolygon detected, using largest polygon")
                return largest_poly
            else:
                print(f"Unsupported geometry type: {geometry.geom_type}")
                return None
        
        return geometry
        
    except Exception as e:
        print(f"Error reading GeoJSON file {geojson_file}: {e}")
        return None

def load_study_area(geojson_file: str) -> bool:
    """Load study area polygon and download network"""
    global _STUDY_AREA_POLYGON, _CACHED_NETWORK
    
    _STUDY_AREA_POLYGON = load_study_area_geojson(geojson_file)
    if _STUDY_AREA_POLYGON is None:
        return False
    
    # Try to load network from cache
    cached_network = _load_network_from_cache(_STUDY_AREA_POLYGON)
    if cached_network is not None:
        _CACHED_NETWORK = cached_network
        return True
    
    # Download network if not cached
    try:
        print("Downloading road network for study area...")
        graph = ox.graph_from_polygon(_STUDY_AREA_POLYGON, network_type='drive', simplify=False)
        _CACHED_NETWORK = ox.graph_to_gdfs(graph, nodes=False, edges=True)
        print(f"Downloaded {len(_CACHED_NETWORK)} road segments for study area")
        
        # Save to cache
        _save_network_to_cache(_CACHED_NETWORK, _STUDY_AREA_POLYGON)
        return True
        
    except Exception as e:
        print(f"Error downloading study area network: {e}")
        _CACHED_NETWORK = None
        return False

def _normalize_road_name(road_name: str) -> str:
    """Normalize road name for better matching"""
    road_name = road_name.lower().strip()
    
    # Common abbreviation normalizations
    abbreviations = {
        ' rd': ' road',
        ' st': ' street', 
        ' ave': ' avenue',
        ' dr': ' drive',
        ' cres': ' crescent',
        ' pl': ' place',
        ' tce': ' terrace',
        ' hwy': ' highway',
        ' blvd': ' boulevard',
        ' ln': ' lane'
    }
    
    for abbrev, full in abbreviations.items():
        if road_name.endswith(abbrev):
            road_name = road_name.replace(abbrev, full)
    
    return road_name

def _find_road_segments(road_network: gpd.GeoDataFrame, road_name: str) -> gpd.GeoDataFrame:
    """Find road segments matching the given road name using fuzzy matching"""
    road_name_clean = road_name.lower().strip()
    name_fields = ['name', 'ref', 'highway']
    matching_segments = gpd.GeoDataFrame()
    
    # First try exact substring matching (fast)
    road_variations = [road_name_clean]
    
    # Add common variations
    if 'rd' in road_name_clean:
        road_variations.append(road_name_clean.replace('rd', 'road'))
    if 'road' in road_name_clean:
        road_variations.append(road_name_clean.replace('road', 'rd'))
    if 'st' in road_name_clean:
        road_variations.append(road_name_clean.replace('st', 'street'))
    if 'street' in road_name_clean:
        road_variations.append(road_name_clean.replace('street', 'st'))
    if 'ave' in road_name_clean:
        road_variations.append(road_name_clean.replace('ave', 'avenue'))
    if 'avenue' in road_name_clean:
        road_variations.append(road_name_clean.replace('avenue', 'ave'))
    
    # Try exact substring matching first
    for field in name_fields:
        if field in road_network.columns:
            for variation in road_variations:
                mask = road_network[field].astype(str).str.lower().str.contains(
                    variation, na=False, regex=False
                )
                if mask.any():
                    matching_segments = pd.concat([matching_segments, road_network[mask]], 
                                                ignore_index=True)
    
    if not matching_segments.empty:
        matching_segments = matching_segments.drop_duplicates()
        return matching_segments
    
    # If no exact matches, try fuzzy matching
    print(f"  No exact match for '{road_name}', trying fuzzy matching...")
    
    normalized_search = _normalize_road_name(road_name)
    
    for field in name_fields:
        if field in road_network.columns:
            # Get all unique road names from this field
            unique_names = road_network[field].dropna().astype(str).unique()
            
            if len(unique_names) == 0:
                continue
                
            # Normalize all road names for comparison
            normalized_names = [_normalize_road_name(name) for name in unique_names]
            
            # Use fuzzy matching to find the best matches
            matches = process.extractBests(
                normalized_search,
                normalized_names,
                scorer=fuzz.ratio,
                score_cutoff=85,  # Increased minimum similarity score for better precision
                limit=3  # Reduced to top 3 matches to avoid too many false positives
            )
            
            if matches:
                print(f"    Fuzzy matches in '{field}' field:")
                # Use only the best match if it's significantly better than others
                best_match = matches[0]
                best_score = best_match[1]
                
                # If the best match is much better than the second best, use only the best
                if len(matches) > 1 and best_score - matches[1][1] >= 10:
                    matches = [best_match]
                    print(f"      Using only best match (significantly better):")
                elif best_score >= 95:  # If very high confidence, use only the best
                    matches = [best_match]
                    print(f"      Using only best match (high confidence):")
                
                for match_name, score in matches:
                    # Find the original name that corresponds to this normalized match
                    orig_idx = normalized_names.index(match_name)
                    original_name = unique_names[orig_idx]
                    print(f"      - {original_name} (score: {score})")
                    
                    # Add segments that match this road name
                    mask = road_network[field].astype(str).str.lower() == original_name.lower()
                    if mask.any():
                        matching_segments = pd.concat([matching_segments, road_network[mask]], 
                                                    ignore_index=True)
                
                # Break after finding matches in the first field with good results
                if best_score >= 90:
                    break
    
    if not matching_segments.empty:
        matching_segments = matching_segments.drop_duplicates()
        print(f"    Found {len(matching_segments)} segments via fuzzy matching")
    else:
        print(f"    No fuzzy matches found for '{road_name}'")
    
    return matching_segments

def _find_geometric_intersection(road1_segments: gpd.GeoDataFrame, 
                               road2_segments: gpd.GeoDataFrame) -> Optional[Tuple[float, float]]:
    """Find the geometric intersection point between two sets of road segments"""
    intersections = []
    
    print(f"    Checking geometric intersection between {len(road1_segments)} and {len(road2_segments)} segments...")
    
    intersection_count = 0
    shared_endpoints = []
    
    for idx1, segment1 in road1_segments.iterrows():
        for idx2, segment2 in road2_segments.iterrows():
            try:
                geom1 = segment1.geometry
                geom2 = segment2.geometry
                
                # Check for line segment intersections
                intersection = geom1.intersection(geom2)
                
                if not intersection.is_empty:
                    intersection_count += 1
                    print(f"    Found intersection {intersection_count}: {intersection.geom_type}")
                    
                    if intersection.geom_type == 'Point':
                        point_coords = (intersection.y, intersection.x)
                        intersections.append(point_coords)
                        print(f"      Point: {point_coords}")
                    elif intersection.geom_type == 'MultiPoint':
                        for point in intersection.geoms:
                            point_coords = (point.y, point.x)
                            intersections.append(point_coords)
                            print(f"      Point: {point_coords}")
                    elif hasattr(intersection, 'centroid'):
                        centroid = intersection.centroid
                        centroid_coords = (centroid.y, centroid.x)
                        intersections.append(centroid_coords)
                        print(f"      Centroid: {centroid_coords}")
                
                # Also check for shared endpoints (nodes)
                coords1 = list(geom1.coords)
                coords2 = list(geom2.coords)
                
                for coord1 in [coords1[0], coords1[-1]]:  # Start and end points
                    for coord2 in [coords2[0], coords2[-1]]:
                        # Check if endpoints are very close (within ~1 meter)
                        distance = ((coord1[0] - coord2[0])**2 + (coord1[1] - coord2[1])**2)**0.5
                        if distance < 0.00001:  # Approximately 1 meter in degrees
                            shared_point = (coord1[1], coord1[0])  # lat, lon
                            if shared_point not in shared_endpoints:
                                shared_endpoints.append(shared_point)
                        
            except Exception as e:
                print(f"    Error checking intersection: {e}")
                continue
    
    print(f"    Total line intersections found: {len(intersections)}")
    print(f"    Total shared endpoints found: {len(shared_endpoints)}")
    
    # Combine both types of intersections
    all_intersections = intersections + shared_endpoints
    
    if all_intersections:
        if len(all_intersections) == 1:
            result = all_intersections[0]
            print(f"    Using single intersection: {result}")
            return result
        else:
            lats = [coord[0] for coord in all_intersections]
            lons = [coord[1] for coord in all_intersections]
            median_coords = (statistics.median(lats), statistics.median(lons))
            print(f"    Using median of {len(all_intersections)} intersections: {median_coords}")
            return median_coords
    
    # If no intersections found, check if roads are very close
    min_distance = float('inf')
    closest_points = None
    
    for idx1, segment1 in road1_segments.iterrows():
        for idx2, segment2 in road2_segments.iterrows():
            try:
                distance = segment1.geometry.distance(segment2.geometry)
                if distance < min_distance:
                    min_distance = distance
                    # Find the closest points
                    from shapely.ops import nearest_points
                    p1, p2 = nearest_points(segment1.geometry, segment2.geometry)
                    closest_points = ((p1.y + p2.y) / 2, (p1.x + p2.x) / 2)
            except Exception:
                continue
    
    # Increased threshold to handle roundabouts and similar infrastructure
    # 0.0005 degrees ≈ 55 meters at Wellington's latitude
    if min_distance < 0.0005:  
        print(f"    Roads are close (distance: {min_distance:.8f} ≈ {min_distance * 111000:.1f}m), using midpoint: {closest_points}")
        print(f"    This may indicate a roundabout or similar infrastructure connecting the roads")
        return closest_points
    else:
        print(f"    Roads are too far apart (minimum distance: {min_distance:.8f} ≈ {min_distance * 111000:.1f}m)")
    
    return None

def debug_road_segments(road_name: str) -> None:
    """Debug function to show what segments are found for a road name"""
    global _CACHED_NETWORK
    
    if _CACHED_NETWORK is None:
        print("No network loaded")
        return
    
    print(f"\n=== Debugging road: {road_name} ===")
    segments = _find_road_segments(_CACHED_NETWORK, road_name)
    
    if segments.empty:
        print(f"❌ No segments found for '{road_name}'")
        
        # Check what road names are in the network that might be similar
        name_fields = ['name', 'ref', 'highway']
        road_name_lower = road_name.lower()
        
        # Extract key words from the road name
        key_words = [word.strip() for word in road_name_lower.replace('rd', 'road').replace('st', 'street').replace('ave', 'avenue').split()]
        
        print(f"  Searching for similar names containing: {key_words}")
        
        for field in name_fields:
            if field in _CACHED_NETWORK.columns:
                unique_names = _CACHED_NETWORK[field].dropna().astype(str).unique()
                
                # Find names that contain any of our key words
                matching_names = []
                for name in unique_names:
                    name_lower = name.lower()
                    if any(word in name_lower for word in key_words):
                        matching_names.append(name)
                
                if matching_names:
                    print(f"  Similar names in '{field}' field:")
                    for name in sorted(matching_names)[:15]:  # Show first 15 matches
                        print(f"    - {name}")
                    if len(matching_names) > 15:
                        print(f"    ... and {len(matching_names) - 15} more")
    else:
        print(f"✅ Found {len(segments)} segments for '{road_name}'")
        
        # Show the name fields for the found segments
        name_fields = ['name', 'ref', 'highway']
        for field in name_fields:
            if field in segments.columns:
                unique_names = segments[field].dropna().unique()
                if len(unique_names) > 0:
                    print(f"  {field} values: {list(unique_names)[:5]}")
        
        # Show geographic bounds
        bounds = segments.bounds
        min_lat, max_lat = bounds['miny'].min(), bounds['maxy'].max()
        min_lon, max_lon = bounds['minx'].min(), bounds['maxx'].max()
        print(f"  Geographic bounds: lat {min_lat:.6f} to {max_lat:.6f}, lon {min_lon:.6f} to {max_lon:.6f}")
        
        # Show a few sample coordinates
        print(f"  Sample segment coordinates:")
        for i, (idx, segment) in enumerate(segments.head(3).iterrows()):
            coords = list(segment.geometry.coords)
            print(f"    Segment {i+1}: {len(coords)} points, starts at {coords[0]}, ends at {coords[-1]}")
    
    return segments

def find_intersection_multi_road(roads: List[str]) -> Optional[Dict[str, Any]]:
    """Find intersection coordinates for multiple roads (2-5 roads)"""
    global _CACHED_NETWORK
    
    if _CACHED_NETWORK is None:
        print("No study area network loaded. Please call load_study_area() first.")
        return None
    
    # Filter out empty roads
    roads = [road.strip() for road in roads if road and road.strip()]
    
    if len(roads) < 2:
        print("Need at least 2 roads to find intersection")
        return None
    
    try:
        # Find road segments for all roads, tracking which ones are found
        all_road_segments = []
        found_roads = []
        missing_roads = []
        
        for road in roads:
            segments = _find_road_segments(_CACHED_NETWORK, road)
            if segments.empty:
                print(f"Could not find road segments for {road}")
                missing_roads.append(road)
            else:
                all_road_segments.append(segments)
                found_roads.append(road)
        
        # Check if we have at least 2 roads found
        if len(found_roads) < 2:
            print(f"Need at least 2 roads found to find intersection. Found: {found_roads}, Missing: {missing_roads}")
            return {
                'latitude': None,
                'longitude': None,
                'roads': roads,
                'road_count': len(roads),
                'found_roads': found_roads,
                'missing_roads': missing_roads,
                'roads_found_count': len(found_roads),
                'partial_match': False
            }
        
        # If we're missing some roads but have at least 2, proceed with partial matching
        partial_match = len(missing_roads) > 0
        if partial_match:
            print(f"Proceeding with partial match using {len(found_roads)} of {len(roads)} roads: {found_roads}")
        
        # For multiple roads, find the point that's closest to all found roads
        # Start with intersection of first two found roads
        intersection_point = _find_geometric_intersection(all_road_segments[0], all_road_segments[1])
        
        if intersection_point is None:
            print(f"No intersection found between {found_roads[0]} and {found_roads[1]}")
            return {
                'latitude': None,
                'longitude': None,
                'roads': roads,
                'road_count': len(roads),
                'found_roads': found_roads,
                'missing_roads': missing_roads,
                'roads_found_count': len(found_roads),
                'partial_match': partial_match
            }
        
        # If more than 2 roads found, verify the intersection point is near the other roads
        if len(found_roads) > 2:
            from shapely.geometry import Point
            point = Point(intersection_point[1], intersection_point[0])  # lon, lat for Point
            
            # Check if the intersection point is within a reasonable distance of other roads
            max_distance = 0.001  # approximately 100 meters in degrees
            for i, segments in enumerate(all_road_segments[2:], start=2):
                min_dist = float('inf')
                for idx, segment in segments.iterrows():
                    dist = point.distance(segment.geometry)
                    min_dist = min(min_dist, dist)
                
                if min_dist > max_distance:
                    print(f"Intersection point too far from {found_roads[i]} (distance: {min_dist:.6f})")
                    return {
                        'latitude': None,
                        'longitude': None,
                        'roads': roads,
                        'road_count': len(roads),
                        'found_roads': found_roads,
                        'missing_roads': missing_roads,
                        'roads_found_count': len(found_roads),
                        'partial_match': partial_match
                    }
        
        lat, lon = intersection_point
        result = {
            'latitude': lat,
            'longitude': lon,
            'roads': roads,
            'road_count': len(roads),
            'found_roads': found_roads,
            'missing_roads': missing_roads,
            'roads_found_count': len(found_roads),
            'partial_match': partial_match
        }
        
        if partial_match:
            print(f"✅ Partial intersection found using {len(found_roads)}/{len(roads)} roads!")
        
        return result
        
    except Exception as e:
        print(f"Error finding intersection: {e}")
        return {
            'latitude': None,
            'longitude': None,
            'roads': roads,
            'road_count': len(roads),
            'found_roads': [],
            'missing_roads': roads,
            'roads_found_count': 0,
            'partial_match': False,
            'error': str(e)
        }

def find_intersection(road1: str, road2: str, city: str = None, state: str = None) -> Optional[Dict[str, Any]]:
    """Find intersection coordinates for two roads (backward compatibility)"""
    result = find_intersection_multi_road([road1, road2])
    if result:
        result.update({
            'road_1': road1,
            'road_2': road2,
            'city': city,
            'state': state
        })
    return result

def find_intersections_batch(intersection_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find multiple intersections efficiently"""
    results = []
    for intersection_data in intersection_list:
        roads = intersection_data['roads']
        intersection_name = intersection_data['intersection_name']
        
        result = find_intersection_multi_road(roads)
        if result:
            # Add the intersection name to the result
            result['intersection_name'] = intersection_name
            results.append(result)
        else:
            # This shouldn't happen with the new implementation, but keep as fallback
            results.append({
                'intersection_name': intersection_name,
                'latitude': None,
                'longitude': None,
                'roads': roads,
                'road_count': len([r for r in roads if r and r.strip()]),
                'found_roads': [],
                'missing_roads': roads,
                'roads_found_count': 0,
                'partial_match': False,
                'error': 'Intersection not found'
            })
    return results

def read_intersections_csv(input_source) -> List[Dict[str, Any]]:
    """
    Read intersection list from CSV file or DataFrame.
    
    Args:
        input_source: Either a file path (str) or a pandas DataFrame with 
                     'Intersection' and 'Road_1' through 'Road_5' columns
    
    Returns:
        List of dictionaries with 'intersection_name' and 'roads' keys
    """
    try:
        # Check if input is a DataFrame
        if isinstance(input_source, pd.DataFrame):
            df = input_source
        else:
            # Read from CSV file
            df = pd.read_csv(input_source, encoding='utf-8')
        
        intersection_list = []
        
        for idx, row in df.iterrows():
            roads = []
            # Read up to 5 road columns
            for i in range(1, 6):
                road_col = f'Road_{i}'
                if road_col in row and pd.notna(row[road_col]) and str(row[road_col]).strip():
                    roads.append(str(row[road_col]).strip())
            
            if len(roads) >= 2:
                intersection_data = {
                    'intersection_name': str(row.get('Intersection', '')).strip() if pd.notna(row.get('Intersection')) else '',
                    'roads': roads
                }
                intersection_list.append(intersection_data)
            else:
                print(f"Skipping row with less than 2 roads: {roads}")
        
        source_name = "DataFrame" if isinstance(input_source, pd.DataFrame) else input_source
        print(f"Read {len(intersection_list)} intersections from {source_name}")
        return intersection_list
        
    except Exception as e:
        print(f"Error reading intersections: {e}")
        return []

def export_intersections_csv(results: List[Dict[str, Any]], output_file: str) -> bool:
    """Export intersection results to CSV file"""
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'intersection', 'road_1', 'road_2', 'road_3', 'road_4', 'road_5', 
                'total_roads', 'roads_found', 'roads_missing',
                'found_road_names', 'missing_road_names',
                'partial_match', 'latitude', 'longitude', 
                'found', 'error'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for result in results:
                roads = result.get('roads', [])
                found_roads = result.get('found_roads', [])
                missing_roads = result.get('missing_roads', [])
                
                csv_row = {
                    'intersection': result.get('intersection_name', ''),
                    'road_1': roads[0] if len(roads) > 0 else '',
                    'road_2': roads[1] if len(roads) > 1 else '',
                    'road_3': roads[2] if len(roads) > 2 else '',
                    'road_4': roads[3] if len(roads) > 3 else '',
                    'road_5': roads[4] if len(roads) > 4 else '',
                    'total_roads': result.get('road_count', 0),
                    'roads_found': result.get('roads_found_count', 0),
                    'roads_missing': len(missing_roads),
                    'found_road_names': '; '.join(found_roads),
                    'missing_road_names': '; '.join(missing_roads),
                    'partial_match': result.get('partial_match', False),
                    'latitude': result.get('latitude'),
                    'longitude': result.get('longitude'),
                    'found': result.get('latitude') is not None,
                    'error': result.get('error', '')
                }
                writer.writerow(csv_row)
        
        found_count = sum(1 for r in results if r.get('latitude') is not None)
        partial_count = sum(1 for r in results if r.get('partial_match', False) and r.get('latitude') is not None)
        total_count = len(results)
        
        print(f"Export complete!")
        print(f"  File: {output_file}")
        print(f"  Total intersections: {total_count}")
        print(f"  Found: {found_count} (including {partial_count} partial matches)")
        print(f"  Not found: {total_count - found_count}")
        
        if partial_count > 0:
            print(f"  Partial matches: {partial_count} intersections found using subset of roads")
        
        return True
        
    except Exception as e:
        print(f"Error exporting to CSV: {e}")
        return False

def process_intersections(input_source, study_area_geojson: str, output_csv: str = "intersection_results.csv") -> bool:
    """
    Main processing function - load study area, find intersections, export results.
    
    Args:
        input_source: Either a CSV file path (str) or pandas DataFrame with intersection data
        study_area_geojson: Path to GeoJSON file with study area polygon
        output_csv: Path for output CSV file
    
    Returns:
        bool: True if successful, False otherwise
    """
    configure_osmnx()
    set_cache_directory()
    
    # Load study area
    if not load_study_area(study_area_geojson):
        print(f"Failed to load study area from {study_area_geojson}")
        return False

    # read in and parse the input_source
    movement_df = pd.read_csv(input_source, encoding='utf-8')
    intersection_df = format_unique_movements(movement_df)
    
    # Read intersections
    intersection_list = read_intersections_csv(intersection_df)
    if not intersection_list:
        print("No valid intersections found in input")
        return False
    
    # Find intersections
    print(f"Finding {len(intersection_list)} intersections...")
    results = find_intersections_batch(intersection_list)
    
    # Export results
    return export_intersections_csv(results, output_csv)


def find_intersection_coordinates(unique_turns_df: pd.DataFrame, study_area_geojson: str) -> pd.DataFrame:
    """
    Simplified function to find intersection coordinates from a unique turns DataFrame.
    
    This function is designed to be called from other scripts like process-turn-counts.py.
    Returns only unique intersections with their coordinates for merging.
    
    Args:
        unique_turns_df: DataFrame with 'Intersection', 'Approach', 'Turn' columns
        study_area_geojson: Path to GeoJSON file with study area polygon
    
    Returns:
        DataFrame with columns: Intersection, latitude, longitude
    """
    configure_osmnx()
    set_cache_directory()
    
    # Load study area
    print(f"Loading study area from {study_area_geojson}")
    if not load_study_area(study_area_geojson):
        print(f"Failed to load study area from {study_area_geojson}")
        return pd.DataFrame(columns=['Intersection', 'latitude', 'longitude'])
    
    # Format intersections
    print("Parsing intersection names...")
    intersection_df = format_unique_movements(unique_turns_df)
    
    # Read intersections
    intersection_list = read_intersections_csv(intersection_df)
    if not intersection_list:
        print("No valid intersections found in input")
        return pd.DataFrame(columns=['Intersection', 'latitude', 'longitude'])
    
    # Find intersections
    print(f"Finding coordinates for {len(intersection_list)} intersections...")
    results = find_intersections_batch(intersection_list)
    
    # Convert results to DataFrame with only the needed columns
    coords_data = []
    for result in results:
        coords_data.append({
            'Intersection': result['intersection_name'],
            'latitude': result.get('latitude'),
            'longitude': result.get('longitude')
        })
    
    coords_df = pd.DataFrame(coords_data)
    
    found_count = coords_df['latitude'].notna().sum()
    total_count = len(coords_df)
    print(f"Found coordinates for {found_count}/{total_count} unique intersections")
    
    return coords_df

def debug_intersection(roads: List[str]) -> None:
    """Debug a specific intersection"""
    configure_osmnx()
    set_cache_directory()
    
    # Load study area
    geojson_file = STUDY_AREA_GEOJSON
    if not load_study_area(geojson_file):
        print(f"Failed to load study area from {geojson_file}")
        return
    
    print(f"\n=== Debugging intersection: {' & '.join(roads)} ===")
    
    # Debug each road individually and store the segments
    road_segments_list = []
    for road in roads:
        segments = debug_road_segments(road)
        road_segments_list.append(segments)
    
    # Check if we can compare the geographic bounds
    if len(road_segments_list) == 2 and not road_segments_list[0].empty and not road_segments_list[1].empty:
        print(f"\n=== Geographic proximity analysis ===")
        
        bounds1 = road_segments_list[0].bounds
        bounds2 = road_segments_list[1].bounds
        
        # Check if bounding boxes overlap
        overlap_lat = not (bounds1['maxy'].max() < bounds2['miny'].min() or bounds2['maxy'].max() < bounds1['miny'].min())
        overlap_lon = not (bounds1['maxx'].max() < bounds2['minx'].min() or bounds2['maxx'].max() < bounds1['minx'].min())
        
        print(f"  Road 1 bounds: lat {bounds1['miny'].min():.6f} to {bounds1['maxy'].max():.6f}, lon {bounds1['minx'].min():.6f} to {bounds1['maxx'].max():.6f}")
        print(f"  Road 2 bounds: lat {bounds2['miny'].min():.6f} to {bounds2['maxy'].max():.6f}, lon {bounds2['minx'].min():.6f} to {bounds2['maxx'].max():.6f}")
        print(f"  Bounding boxes overlap: lat={overlap_lat}, lon={overlap_lon}")
        
        if not overlap_lat or not overlap_lon:
            print("  ⚠️  Roads' bounding boxes don't overlap - they may not intersect")
    
    # Try to find the intersection
    print(f"\n=== Attempting to find intersection ===")
    result = find_intersection_multi_road(roads)
    
    if result:
        print(f"✅ Intersection found!")
        print(f"   Latitude: {result['latitude']}")
        print(f"   Longitude: {result['longitude']}")
    else:
        print(f"❌ Intersection not found")

def test_fuzzy_matching():
    """Test fuzzy matching with problematic road names"""
    configure_osmnx()
    set_cache_directory()
    
    # Load study area
    geojson_file = STUDY_AREA_GEOJSON
    if not load_study_area(geojson_file):
        print(f"Failed to load study area from {geojson_file}")
        return
    
    # Test some problematic road names
    test_roads = [
        "Lamton Quay",  # Should match "Lambton Quay"
        "Featherston  St",  # Extra space
        "Cambridge Tce",  # Should match "Cambridge Terrace"
        "Kilbernie Cres",  # Should match "Kilbirnie Crescent"
    ]
    
    for road in test_roads:
        print(f"\n=== Testing fuzzy matching for: {road} ===")
        debug_road_segments(road)

def main():
    """Main function for command line usage"""
    print("=== OSMnx Intersection Finder ===")
    print("Production-ready intersection coordinate finder using OpenStreetMap data")
    print()
    
    # Check if we're running in debug mode
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        # Debug specific intersection: Adelaide Rd, Riddiford St, John St
        debug_intersection(["Adelaide Rd", "Riddiford St", "John St"])
        return True
    elif len(sys.argv) > 1 and sys.argv[1] == "debug2":
        # Debug specific intersection: Cobham Dr, Calabar Rd
        debug_intersection(["Cobham Dr", "Calabar Rd"])
        return True
    elif len(sys.argv) > 1 and sys.argv[1] == "debug3":
        # Debug specific intersection: Molesworth St, Mway Onramp, May St
        debug_intersection(["Molesworth St", "Mway Onramp", "May St"])
        return True
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test fuzzy matching
        test_fuzzy_matching()
        return True
    
    # Use configuration parameters from top of file
    geojson_file = STUDY_AREA_GEOJSON
    input_csv = INPUT_CSV
    output_csv = OUTPUT_CSV
    
    missing_files = []
    if not os.path.exists(geojson_file):
        missing_files.append(geojson_file)
    if not os.path.exists(input_csv):
        missing_files.append(input_csv)
    
    if missing_files:
        print("❌ Missing required input files:")
        for file in missing_files:
            print(f"   - {file}")
        print()
        print("Required files:")
        print(f"1. {geojson_file} - GeoJSON file containing the study area polygon")
        print(f"2. {input_csv} - CSV file with columns: Road_1, Road_2, Road_3, Road_4, Road_5")
        return False
    
    success = process_intersections(input_csv, geojson_file, output_csv)


    
    if success:
        print("\n✅ Processing complete!")
        print(f"📄 Results saved to: {output_csv}")
    else:
        print("❌ Processing failed.")
    
    return success

if __name__ == "__main__":
    main()