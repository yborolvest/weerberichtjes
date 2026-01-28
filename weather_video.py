import os
import random
import wave
import math
import json
import tempfile
from datetime import datetime

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.video.VideoClip import ImageClip
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.audio.AudioClip import CompositeAudioClip, concatenate_audioclips
from moviepy.video.fx import FadeIn
from moviepy.video.fx import CrossFadeIn

try:
    import netCDF4
    HAS_NETCDF = True
except ImportError:
    HAS_NETCDF = False
    print("Warning: netCDF4 not installed. Install with: pip install netCDF4")

# ---------- CONFIG ----------

# KNMI Open Data API key (use anonymous key or get registered key from https://developer.dataplatform.knmi.nl/)
# Anonymous key valid until July 1, 2026:
KNMI_API_KEY = os.environ.get("KNMI_API_KEY") or "eyJvcmciOiI1ZTU1NGUxOTI3NGE5NjAwMDEyYTNlYjEiLCJpZCI6ImVlNDFjMWI0MjlkODQ2MThiNWI4ZDViZDAyMTM2YTM3IiwiaCI6Im11cm11cjEyOCJ9"

# City/region for the forecast
CITY = "De Bilt"
COUNTRY = "Netherlands"

# Paths (adjust if you change your folder structure)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_CLIPS_DIR = os.path.join(BASE_DIR, "voice", "jeroen", "clips")
MUSIC_DIR = os.path.join(BASE_DIR, "music")
# Prefer video backgrounds from video_parts/backgrounds
BACKGROUND_DIR = os.path.join(BASE_DIR, "video_parts", "backgrounds")
ICONS_DIR = os.path.join(BASE_DIR, "video_parts", "icons", "256w")  # Weather icons (256w PNG files)
AVATAR_IMAGE = os.path.join(BASE_DIR, "avatar.png")  # optional, if present
EXTRA_TAIL_SECONDS = 5.0  # extra background-only time after voice ends

# Font configuration
NORMAL_TEXT_FONT_SIZE = 40  # Font size for normal text (subtitles)
TEMPERATURE_TEXT_FONT_SIZE = 140  # Font size for temperature text

# Border width configuration
SUBTITLE_BORDER_WIDTH = 3  # Border width for subtitle box
TEMPERATURE_OVERLAY_BORDER_WIDTH = 2  # Border width for temperature overlay box
FORECAST_OVERLAY_BORDER_WIDTH = 2  # Border width for forecast overlay box


# ---------- WEATHER ----------

def get_weather(city=CITY, country=COUNTRY):
    """
    Fetch current weather from KNMI Open Data API for De Bilt (station 0-20000-0-06260).
    Uses the 10-minute in-situ meteorological observations dataset.
    """
    if not HAS_NETCDF:
        raise RuntimeError(
            "netCDF4 is required for KNMI data. Install with: pip install netCDF4"
        )
    
    if not KNMI_API_KEY:
        raise RuntimeError(
            "KNMI_API_KEY is not set. "
            "Set an environment variable KNMI_API_KEY or use the default anonymous key."
        )
    
    base_url = "https://api.dataplatform.knmi.nl/open-data/v1"
    dataset_name = "10-minute-in-situ-meteorological-observations"
    dataset_version = "1.0"
    
    # De Bilt station code (found via coordinate matching: 52.0989°N, 5.1797°E)
    station_code = "0-20000-0-06260"
    
    # Step 1: List files to get the most recent one
    list_url = f"{base_url}/datasets/{dataset_name}/versions/{dataset_version}/files"
    headers = {"Authorization": KNMI_API_KEY}
    
    # Get the most recent file (sorted by lastModified descending)
    params = {
        "maxKeys": 1,
        "sorting": "desc",
        "orderBy": "lastModified"
    }
    
    print(f"Fetching most recent observation file from KNMI...")
    list_resp = requests.get(list_url, headers=headers, params=params)
    list_resp.raise_for_status()
    list_data = list_resp.json()
    
    if not list_data.get("files"):
        raise RuntimeError("No observation files found in KNMI dataset")
    
    filename = list_data["files"][0]["filename"]
    print(f"Found file: {filename}")
    
    # Step 2: Get download URL for the file
    download_url_endpoint = f"{base_url}/datasets/{dataset_name}/versions/{dataset_version}/files/{filename}/url"
    download_resp = requests.get(download_url_endpoint, headers=headers)
    download_resp.raise_for_status()
    download_data = download_resp.json()
    temp_download_url = download_data["temporaryDownloadUrl"]
    
    # Step 3: Download and parse the NetCDF file
    print(f"Downloading and parsing observation data...")
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp_file:
        tmp_path = tmp_file.name
        try:
            file_resp = requests.get(temp_download_url, stream=True)
            file_resp.raise_for_status()
            for chunk in file_resp.iter_content(chunk_size=8192):
                tmp_file.write(chunk)
            tmp_file.flush()
            
            # Step 4: Parse NetCDF file
            with netCDF4.Dataset(tmp_path, 'r') as nc:
                # Get station indices - try common variable names
                # KNMI uses 'station' or 'wsi' (weather station ID)
                station_idx = None
                station_var = None
                for var_name in ['wsi', 'WSI', 'station_id', 'STN', 'station', 'stations']:
                    if var_name in nc.variables:
                        station_var = nc.variables[var_name]
                        break
                
                # If not found, try case-insensitive search
                if station_var is None:
                    var_names_lower = {k.lower(): k for k in nc.variables.keys()}
                    for check_name in ['wsi', 'station_id', 'stn', 'station', 'stations']:
                        if check_name in var_names_lower:
                            station_var = nc.variables[var_names_lower[check_name]]
                            break
                
                # Find De Bilt station (0-20000-0-06260)
                if station_var is not None:
                    try:
                        station_data = station_var[:]
                        if isinstance(station_data, np.ndarray):
                            station_list = station_data.flatten()
                        else:
                            station_list = np.array(station_data).flatten()
                        
                        # Find index of De Bilt station
                        # Convert station_code to match the data type in station_list
                        if station_list.dtype.kind in ['U', 'S']:  # String or bytes array
                            matches = np.where(station_list.astype(str) == str(station_code))[0]
                        else:
                            matches = np.where(station_list == station_code)[0]
                        if len(matches) > 0:
                            station_idx = int(matches[0])
                        else:
                            print(f"Warning: Station {station_code} (De Bilt) not found, using first available station")
                            station_idx = 0
                    except Exception as e:
                        print(f"Warning: Could not parse station data: {e}, using first station")
                        station_idx = 0
                else:
                    print("Warning: No station variable found, assuming single station")
                    station_idx = 0
                
                # Get temperature (T in degrees Celsius)
                # KNMI uses lowercase 'ta' for air temperature
                temp_var = None
                # Check both exact case and case-insensitive
                for var_name in ['ta', 'TA', 'T', 'temperature', 'temp', 'TEMP', 'T_2M', 'T2M']:
                    if var_name in nc.variables:
                        temp_var = nc.variables[var_name]
                        break
                
                # If not found, try case-insensitive search
                if temp_var is None:
                    var_names_lower = {k.lower(): k for k in nc.variables.keys()}
                    for check_name in ['ta', 't', 'temperature', 'temp', 't_2m', 't2m']:
                        if check_name in var_names_lower:
                            temp_var = nc.variables[var_names_lower[check_name]]
                            break
                
                if temp_var is None:
                    raise RuntimeError("Temperature variable not found in NetCDF file. Available variables: " + 
                                     ", ".join(sorted(nc.variables.keys())))
                
                # Get the most recent temperature value
                temp_data = temp_var[:]
                temp_array = np.array(temp_data)
                
                # Handle different dimensionalities
                if temp_array.ndim == 0:
                    # Scalar value
                    temp_c = float(temp_array)
                elif temp_array.ndim == 1:
                    # 1D: time series, get last value
                    temp_c = float(temp_array[-1])
                elif temp_array.ndim == 2:
                    # 2D: (time, station) or (station, time)
                    # Try to determine dimension order by checking which dimension matches station count
                    if station_idx is not None:
                        # Assume (time, station) format - get last time, specific station
                        try:
                            temp_c = float(temp_array[-1, station_idx])
                        except IndexError:
                            # Try (station, time) format
                            try:
                                temp_c = float(temp_array[station_idx, -1])
                            except IndexError:
                                # Fallback: use last value of first dimension
                                temp_c = float(temp_array[-1, 0])
                    else:
                        temp_c = float(temp_array[-1, 0])
                else:
                    # Higher dimensions - flatten and get last
                    temp_c = float(temp_array.flatten()[-1])
                
                # Get weather condition (WW code - present weather)
                # WW codes: 0=clear, 1-9=various cloud/weather, 10-19=precipitation, etc.
                # KNMI uses lowercase 'ww' for weather code
                condition_text = "onbekend"
                
                # Try to find weather code variable
                ww_var = None
                for var_name in ['ww', 'WW', 'present_weather', 'weather_code', 'WMO_WW']:
                    if var_name in nc.variables:
                        ww_var = nc.variables[var_name]
                        break
                
                # If not found, try case-insensitive search
                if ww_var is None:
                    var_names_lower = {k.lower(): k for k in nc.variables.keys()}
                    for check_name in ['ww', 'present_weather', 'weather_code', 'wmo_ww']:
                        if check_name in var_names_lower:
                            ww_var = nc.variables[var_names_lower[check_name]]
                            break
                
                if ww_var is not None:
                    ww_data = np.array(ww_var[:])
                    
                    # Extract weather code based on dimensionality
                    if ww_data.ndim == 0:
                        ww_code = int(ww_data)
                    elif ww_data.ndim == 1:
                        ww_code = int(ww_data[-1])
                    elif ww_data.ndim == 2:
                        if station_idx is not None:
                            try:
                                ww_code = int(ww_data[-1, station_idx])
                            except IndexError:
                                try:
                                    ww_code = int(ww_data[station_idx, -1])
                                except IndexError:
                                    ww_code = int(ww_data[-1, 0])
                        else:
                            ww_code = int(ww_data[-1, 0])
                    else:
                        ww_code = int(ww_data.flatten()[-1])
                    
                    # Map WW code to Dutch condition text
                    condition_text = map_ww_code_to_condition(ww_code)
                else:
                    # Fallback: determine condition from temperature and other variables
                    # Check for precipitation
                    # KNMI uses R1H, R6H, R12H, R24H for precipitation (1 hour, 6 hour, etc.)
                    precip_var = None
                    for var_name in ['R1H', 'r1h', 'R6H', 'r6h', 'R12H', 'r12h', 'R24H', 'r24h', 
                                     'RH', 'rh', 'precipitation', 'PRECIP', 'RR', 'rr', 'R', 'r']:
                        if var_name in nc.variables:
                            precip_var = nc.variables[var_name]
                            break
                    
                    # If not found, try case-insensitive search
                    if precip_var is None:
                        var_names_lower = {k.lower(): k for k in nc.variables.keys()}
                        for check_name in ['r1h', 'r6h', 'r12h', 'r24h', 'rh', 'precipitation', 'precip', 'rr', 'r']:
                            if check_name in var_names_lower:
                                precip_var = nc.variables[var_names_lower[check_name]]
                                break
                    
                    if precip_var is not None:
                        precip_data = np.array(precip_var[:])
                        if precip_data.ndim == 0:
                            precip = float(precip_data)
                        elif precip_data.ndim == 1:
                            precip = float(precip_data[-1])
                        elif precip_data.ndim == 2:
                            if station_idx is not None:
                                try:
                                    precip = float(precip_data[-1, station_idx])
                                except IndexError:
                                    precip = float(precip_data[station_idx, -1])
                            else:
                                precip = float(precip_data[-1, 0])
                        else:
                            precip = float(precip_data.flatten()[-1])
                        
                        if precip > 0:
                            condition_text = "regen"
                        else:
                            condition_text = "bewolkt" if temp_c < 15 else "gedeeltelijk bewolkt"
                    else:
                        # Simple fallback based on temperature
                        if temp_c <= 5:
                            condition_text = "koud en bewolkt"
                        elif temp_c > 20:
                            condition_text = "zonnig"
                        else:
                            condition_text = "gedeeltelijk bewolkt"
            
            print(f"Temperature: {temp_c:.1f}°C, Condition: {condition_text}")
            return temp_c, condition_text.lower()
            
        finally:
            # Clean up temporary file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def get_forecast(city=CITY):
    """
    Fetch today's forecast from KNMI Open Data API using the uwcw_extra_lv_ha43_nl_2km dataset.
    Returns forecast temperature, condition, and max/min temperatures for today.
    """
    if not HAS_NETCDF:
        print("Warning: netCDF4 not available, skipping forecast")
        return None, None, None, None
    
    if not KNMI_API_KEY:
        print("Warning: KNMI_API_KEY not set, skipping forecast")
        return None, None, None, None
    
    base_url = "https://api.dataplatform.knmi.nl/open-data/v1"
    dataset_name = "uwcw_extra_lv_ha43_nl_2km"
    dataset_version = "1.0"
    
    # Get coordinates for De Bilt
    target_lat = 52.10  # De Bilt latitude
    target_lon = 5.18    # De Bilt longitude
    
    try:
        # Step 1: List files to find air-temperature file
        list_url = f"{base_url}/datasets/{dataset_name}/versions/{dataset_version}/files"
        headers = {"Authorization": KNMI_API_KEY}
        
        params = {
            "maxKeys": 100,
            "sorting": "desc",
            "orderBy": "lastModified"
        }
        
        print(f"Fetching forecast files from KNMI...")
        list_resp = requests.get(list_url, headers=headers, params=params)
        list_resp.raise_for_status()
        list_data = list_resp.json()
        
        if not list_data.get("files"):
            print("Warning: No forecast files found")
            return None, None, None, None
        
        # Find air-temperature file
        temp_filename = None
        for file_info in list_data.get("files", []):
            filename = file_info.get("filename", "")
            if "air-temperature-hagl" in filename:
                temp_filename = filename
                break
        
        if not temp_filename:
            print("Warning: air-temperature forecast file not found")
            return None, None, None, None
        
        print(f"Found temperature forecast file: {temp_filename}")
        
        # Step 2: Get download URL for temperature file
        download_url_endpoint = f"{base_url}/datasets/{dataset_name}/versions/{dataset_version}/files/{temp_filename}/url"
        download_resp = requests.get(download_url_endpoint, headers=headers)
        download_resp.raise_for_status()
        download_data = download_resp.json()
        temp_download_url = download_data["temporaryDownloadUrl"]
        
        # Step 3: Download and parse temperature
        print(f"Downloading and parsing temperature forecast...")
        forecast_temp = None
        lat_idx = None
        lon_idx = None
        
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp_file:
            tmp_path = tmp_file.name
            try:
                file_resp = requests.get(temp_download_url, stream=True)
                file_resp.raise_for_status()
                for chunk in file_resp.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_file.flush()
                
                with netCDF4.Dataset(tmp_path, 'r') as nc:
                    # Find lat/lon
                    lat_var = nc.variables.get('latitude')
                    lon_var = nc.variables.get('longitude')
                    
                    if lat_var is None or lon_var is None:
                        print("Warning: Could not find lat/lon in forecast file")
                        return None, None, None, None
                    
                    lat_data = np.array(lat_var[:])
                    lon_data = np.array(lon_var[:])
                    
                    # Find nearest grid point
                    lat_idx = int(np.argmin(np.abs(lat_data - target_lat)))
                    lon_idx = int(np.argmin(np.abs(lon_data - target_lon)))
                    
                    # Get temperature variable (air-temperature-hagl)
                    temp_var = nc.variables.get('air-temperature-hagl')
                    if temp_var is None:
                        print("Warning: air-temperature-hagl variable not found")
                        return None, None, None, None
                    
                    temp_data = np.array(temp_var[:])
                    # Shape is (time, height_level, lat, lon) = (60, 1, 390, 390)
                    # Get first time step (today), first height level, at De Bilt location
                    if temp_data.ndim == 4:
                        forecast_temp = float(temp_data[0, 0, lat_idx, lon_idx])
                    elif temp_data.ndim == 3:
                        forecast_temp = float(temp_data[0, lat_idx, lon_idx])
                    elif temp_data.ndim == 2:
                        forecast_temp = float(temp_data[lat_idx, lon_idx])
                    else:
                        forecast_temp = float(temp_data[0])
                    
                    print(f"Forecast temperature: {forecast_temp:.1f}°C at grid point ({lat_data[lat_idx]:.2f}°N, {lon_data[lon_idx]:.2f}°E)")
                    
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        
        # Step 4: Get precipitation data to derive weather condition
        forecast_condition = None
        # Find rainfall file
        rainfall_filename = None
        for file_info in list_data.get("files", []):
            filename = file_info.get("filename", "")
            if "rainfall-accumulation-01h-hagl" in filename:
                rainfall_filename = filename
                break
        
        if rainfall_filename and lat_idx is not None and lon_idx is not None:
            try:
                download_url_endpoint = f"{base_url}/datasets/{dataset_name}/versions/{dataset_version}/files/{rainfall_filename}/url"
                download_resp = requests.get(download_url_endpoint, headers=headers)
                download_resp.raise_for_status()
                download_data = download_resp.json()
                precip_download_url = download_data["temporaryDownloadUrl"]
                
                with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                    try:
                        file_resp = requests.get(precip_download_url, stream=True)
                        file_resp.raise_for_status()
                        for chunk in file_resp.iter_content(chunk_size=8192):
                            tmp_file.write(chunk)
                        tmp_file.flush()
                        
                        with netCDF4.Dataset(tmp_path, 'r') as nc:
                            precip_var = nc.variables.get('rainfall-accumulation-01h-hagl')
                            if precip_var is not None:
                                precip_data = np.array(precip_var[:])
                                # Get first time step
                                if precip_data.ndim == 4:
                                    precip = float(precip_data[0, 0, lat_idx, lon_idx])
                                elif precip_data.ndim == 3:
                                    precip = float(precip_data[0, lat_idx, lon_idx])
                                elif precip_data.ndim == 2:
                                    precip = float(precip_data[lat_idx, lon_idx])
                                else:
                                    precip = float(precip_data[0])
                                
                                # Derive condition from precipitation
                                if precip > 0.5:
                                    forecast_condition = "regen"
                                elif precip > 0.1:
                                    forecast_condition = "lichte regen"
                                else:
                                    # Simple condition based on temperature
                                    if forecast_temp <= 5:
                                        forecast_condition = "bewolkt"
                                    elif forecast_temp > 20:
                                        forecast_condition = "gedeeltelijk bewolkt"
                                    else:
                                        forecast_condition = "bewolkt"
                    finally:
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
            except Exception as e:
                print(f"Warning: Could not get precipitation data: {e}")
        
        # Fallback condition if precipitation data not available
        if forecast_condition is None:
            if forecast_temp <= 5:
                forecast_condition = "bewolkt"
            elif forecast_temp > 20:
                forecast_condition = "gedeeltelijk bewolkt"
            else:
                forecast_condition = "bewolkt"
        
        # Calculate max/min from temperature forecast (use first few hours for min, later hours for max)
        max_temp = None
        min_temp = None
        if forecast_temp is not None:
            # For now, use forecast_temp as average, estimate max/min
            max_temp = forecast_temp + 3  # Rough estimate
            min_temp = forecast_temp - 3  # Rough estimate
        
        print(f"Forecast: {forecast_temp:.1f}°C, Condition: {forecast_condition}, Max: {max_temp}, Min: {min_temp}")
        return forecast_temp, forecast_condition, max_temp, min_temp
                    
    except Exception as e:
        print(f"Warning: Could not fetch forecast: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None


def map_ww_code_to_condition(ww_code):
    """
    Map WMO weather code (WW) to Dutch condition text.
    Based on WMO code table 4677: Present weather reported from a manned weather station.
    Reference: https://www.nodc.noaa.gov/archive/arc0021/0002199/1.1/data/0-data/HTML/WMO-CODE/WMO4677.HTM
    """
    ww = int(ww_code)
    
    # ww = 00-09: No precipitation, cloud development/visibility conditions
    if ww == 0:
        return "helder"  # Cloud development not observed or not observable
    if ww == 1:
        return "opklarend"  # Clouds generally dissolving or becoming less developed
    if ww == 2:
        return "bewolkt"  # State of sky on the whole unchanged
    if ww == 3:
        return "toenemende bewolking"  # Clouds generally forming or developing
    if ww == 4:
        return "rook"  # Visibility reduced by smoke
    if ww == 5:
        return "nevel"  # Haze
    if ww == 6:
        return "stof in de lucht"  # Widespread dust in suspension
    if ww == 7:
        return "stof of zand opwaaiend"  # Dust or sand raised by wind
    if ww == 8:
        return "stofhoos"  # Well developed dust whirl(s) or sand whirl(s)
    if ww == 9:
        return "stofstorm"  # Duststorm or sandstorm
    
    # ww = 10-19: No precipitation, fog, lightning, distant precipitation
    if ww == 10:
        return "mist"  # Mist
    if ww == 11:
        return "mistbanken"  # Patches of shallow fog
    if ww == 12:
        return "mist"  # More or less continuous shallow fog
    if ww == 13:
        return "bliksem"  # Lightning visible, no thunder heard
    if ww == 14:
        return "neerslag in de verte"  # Precipitation within sight, not reaching ground
    if ww == 15:
        return "neerslag in de verte"  # Precipitation distant (>5 km)
    if ww == 16:
        return "neerslag in de buurt"  # Precipitation near but not at station
    if ww == 17:
        return "onweer zonder neerslag"  # Thunderstorm, but no precipitation
    if ww == 18:
        return "windstoten"  # Squalls
    if ww == 19:
        return "windhoos"  # Funnel cloud(s) / Tornado
    
    # ww = 20-29: Precipitation/fog/thunderstorm during preceding hour but not at time of observation
    if ww == 20:
        return "motregen"  # Drizzle (not freezing) or snow grains
    if ww == 21:
        return "regen"  # Rain (not freezing)
    if ww == 22:
        return "sneeuw"  # Snow
    if ww == 23:
        return "natte sneeuw"  # Rain and snow or ice pellets
    if ww == 24:
        return "ijzel"  # Freezing drizzle or freezing rain
    if ww == 25:
        return "regenbuien"  # Shower(s) of rain
    if ww == 26:
        return "sneeuwbuien"  # Shower(s) of snow, or of rain and snow
    if ww == 27:
        return "hagelbuien"  # Shower(s) of hail
    if ww == 28:
        return "mist"  # Fog or ice fog
    if ww == 29:
        return "onweer"  # Thunderstorm (with or without precipitation)
    
    # ww = 30-39: Duststorm, sandstorm, drifting or blowing snow
    if ww in [30, 31, 32]:
        return "stofstorm"  # Slight or moderate duststorm or sandstorm
    if ww in [33, 34, 35]:
        return "zware stofstorm"  # Severe duststorm or sandstorm
    if ww in [36, 37, 38, 39]:
        return "opwaaiende sneeuw"  # Blowing or drifting snow
    
    # ww = 40-49: Fog or ice fog at the time of observation
    if ww in [40, 41, 42, 43, 44, 45, 46, 47, 48, 49]:
        return "mist"  # Various fog conditions
    
    # ww = 50-59: Drizzle
    if ww in [50, 51]:
        return "lichte motregen"  # Drizzle, not freezing, slight/intermittent/continuous
    if ww in [52, 53]:
        return "matige motregen"  # Drizzle, not freezing, moderate
    if ww in [54, 55]:
        return "zware motregen"  # Drizzle, not freezing, heavy
    if ww == 56:
        return "lichte ijzel"  # Drizzle, freezing, slight
    if ww == 57:
        return "ijzel"  # Drizzle, freezing, moderate or heavy
    if ww == 58:
        return "lichte motregen en regen"  # Drizzle and rain, slight
    if ww == 59:
        return "motregen en regen"  # Drizzle and rain, moderate or heavy
    
    # ww = 60-69: Rain
    if ww == 60:
        return "lichte regen"  # Rain, not freezing, intermittent, slight
    if ww == 61:
        return "regen"  # Rain, not freezing, continuous
    if ww == 62:
        return "matige regen"  # Rain, not freezing, intermittent, moderate
    if ww == 63:
        return "matige regen"  # Rain, not freezing, continuous
    if ww == 64:
        return "zware regen"  # Rain, not freezing, intermittent, heavy
    if ww == 65:
        return "zware regen"  # Rain, not freezing, continuous
    if ww == 66:
        return "lichte ijzel"  # Rain, freezing, slight
    if ww == 67:
        return "ijzel"  # Rain, freezing, moderate or heavy
    if ww == 68:
        return "lichte regen of motregen en sneeuw"  # Rain or drizzle and snow, slight
    if ww == 69:
        return "regen of motregen en sneeuw"  # Rain or drizzle and snow, moderate or heavy
    
    # ww = 70-79: Solid precipitation not in showers (Snow)
    if ww == 70:
        return "lichte sneeuw"  # Intermittent fall of snowflakes, slight
    if ww == 71:
        return "sneeuw"  # Continuous fall of snowflakes
    if ww == 72:
        return "matige sneeuw"  # Intermittent fall of snowflakes, moderate
    if ww == 73:
        return "matige sneeuw"  # Continuous fall of snowflakes
    if ww == 74:
        return "zware sneeuw"  # Intermittent fall of snowflakes, heavy
    if ww == 75:
        return "zware sneeuw"  # Continuous fall of snowflakes
    if ww == 76:
        return "diamantstof"  # Diamond dust (with or without fog)
    if ww == 77:
        return "sneeuwkorrels"  # Snow grains (with or without fog)
    if ww == 78:
        return "sneeuwkristallen"  # Isolated star-like snow crystals
    if ww == 79:
        return "ijskorrels"  # Ice pellets
    
    # ww = 80-99: Showery precipitation, or precipitation with current or recent thunderstorm
    if ww == 80:
        return "lichte regenbuien"  # Rain shower(s), slight
    if ww == 81:
        return "regenbuien"  # Rain shower(s), moderate or heavy
    if ww == 82:
        return "zware regenbuien"  # Rain shower(s), violent
    if ww == 83:
        return "lichte regen- en sneeuwbuien"  # Shower(s) of rain and snow mixed, slight
    if ww == 84:
        return "regen- en sneeuwbuien"  # Shower(s) of rain and snow mixed, moderate or heavy
    if ww == 85:
        return "lichte sneeuwbuien"  # Snow shower(s), slight
    if ww == 86:
        return "sneeuwbuien"  # Snow shower(s), moderate or heavy
    if ww == 87:
        return "lichte hagelbuien"  # Shower(s) of snow pellets or small hail, slight
    if ww == 88:
        return "hagelbuien"  # Shower(s) of snow pellets or small hail, moderate or heavy
    if ww == 89:
        return "lichte hagelbuien"  # Shower(s) of hail, slight
    if ww == 90:
        return "hagelbuien"  # Shower(s) of hail, moderate or heavy
    if ww == 91:
        return "lichte regen met onweer"  # Slight rain, thunderstorm during preceding hour
    if ww == 92:
        return "regen met onweer"  # Moderate or heavy rain, thunderstorm during preceding hour
    if ww == 93:
        return "lichte sneeuw of regen en sneeuw met onweer"  # Slight snow/rain and snow/hail, thunderstorm
    if ww == 94:
        return "sneeuw of regen en sneeuw met onweer"  # Moderate or heavy snow/rain and snow/hail, thunderstorm
    if ww == 95:
        return "onweer met regen"  # Thunderstorm, slight or moderate, with rain and/or snow
    if ww == 96:
        return "onweer met hagel"  # Thunderstorm, slight or moderate, with hail
    if ww == 97:
        return "zwaar onweer"  # Thunderstorm, heavy, with rain and/or snow
    if ww == 98:
        return "onweer met stofstorm"  # Thunderstorm combined with duststorm or sandstorm
    if ww == 99:
        return "zwaar onweer met hagel"  # Thunderstorm, heavy, with hail
    
    # Default fallback
    return "bewolkt"


# ---------- MOOD & MUSIC ----------

def pick_mood_and_music(temp_c, condition_text, forecast_temp=None, forecast_condition=None):
    """
    Map weather and temperature -> mood and a music filename.
    Uses your existing tracks: cold.mp3, hot.mp3, normal.mp3, rainy.mp3, warm.mp3.
    Considers both current weather and forecast if available.
    """
    cond = condition_text.lower()
    forecast_cond = (forecast_condition or "").lower() if forecast_condition else ""

    # Base tracks in your music folder
    mapping = {
        "rainy": [os.path.join(MUSIC_DIR, "rainy.mp3")],
        "cold": [os.path.join(MUSIC_DIR, "cold.mp3")],
        "warm": [os.path.join(MUSIC_DIR, "warm.mp3")],
        "hot": [os.path.join(MUSIC_DIR, "hot.mp3")],
        "normal": [os.path.join(MUSIC_DIR, "normal.mp3")],
    }

    # Helper to easily support multiple alternatives later
    def pick_track(key):
        tracks = mapping.get(key)
        if not tracks:
            return None
        return random.choice(tracks)

    # Use forecast if available, otherwise use current
    use_temp = forecast_temp if forecast_temp is not None else temp_c
    use_cond = forecast_cond if forecast_cond else cond

    # Rain-based mood (overrides pure temp)
    if "regen" in use_cond or "bui" in use_cond or "motregen" in use_cond:
        return "regenachtig", pick_track("rainy")

    # Temperature bands (tweak thresholds as you like)
    if use_temp <= 5:
        return "erg koud", pick_track("cold")
    if 5 < use_temp <= 15:
        return "aangenaam", pick_track("normal")
    if 15 < use_temp <= 23:
        return "lekker warm", pick_track("warm")
    if use_temp > 23:
        return "heet", pick_track("hot")

    # Fallback
    return "Normale dag", pick_track("normal")


def get_weather_icon_path(condition_text, temp_c):
    """
    Map weather condition and temperature to an icon filename.
    Returns the full path to the icon file, or None if not found.
    """
    if not os.path.exists(ICONS_DIR):
        return None
    
    cond = condition_text.lower()
    
    # Map common weather conditions to icon filenames
    # Try exact matches first, then partial matches
    icon_mapping = {
        # Rain
        "regen": "rain",
        "bui": "rain",
        "motregen": "rain",
        "onweer": "thunderstorm",
        "storm": "wind",
        # Snow
        "sneeuw": "snow",
        "hagel": "snow",
        # Clear/Sunny
        "zonnig": "sunny",
        "helder": "sunny",
        "zon": "sunny",
        # Cloudy
        "bewolkt": "cloudy",
        "wolken": "cloudy",
        "gedeeltelijk bewolkt": "partly-cloudy",
        # Fog
        "mist": "fog",
        "nevel": "fog",
    }
    
    # Try exact match first
    for keyword, icon_name in icon_mapping.items():
        if keyword in cond:
            icon_path = os.path.join(ICONS_DIR, f"{icon_name}.png")
            if os.path.exists(icon_path):
                return icon_path
    
    # Fallback: try temperature-based selection
    if temp_c <= 5:
        icon_path = os.path.join(ICONS_DIR, "cold.png")
    elif temp_c > 23:
        icon_path = os.path.join(ICONS_DIR, "hot.png")
    else:
        icon_path = os.path.join(ICONS_DIR, "normal.png")
    
    if os.path.exists(icon_path):
        return icon_path
    
    # Last resort: return first available PNG
    icon_files = [f for f in os.listdir(ICONS_DIR) if f.endswith('.png')]
    if icon_files:
        return os.path.join(ICONS_DIR, icon_files[0])
    
    return None


# ---------- TEXT (template-based "AI-ish" generation) ----------

WEEKDAGEN_NL = [
    "maandag", "dinsdag", "woensdag",
    "donderdag", "vrijdag", "zaterdag", "zondag",
]

MAANDEN_NL = [
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
]

GREETINGS = [
    "Goedendag!",
    "Hallo daar!",
    "Goedemorgen!",
    "Hoi allemaal!",
    "Wees gegroet!",
]

TEMP_PATTERNS = [
    "Vandaag in {city} wordt het ongeveer {temp} graden.",
    "In {city} schommelt de temperatuur rond de {temp} graden.",
    "Rond de {temp} graden vandaag in {city}.",
    "De temperatuur in {city} ligt vandaag rond de {temp} graden.",
]

COND_PATTERNS = [
    "Er wordt {cond} voorspeld.",
    "Je kunt {cond} verwachten.",
    "We krijgen te maken met {cond}.",
    "Het weerbeeld: {cond}.",
]

MOOD_PATTERNS = [
    "Al met al voelt het {mood}.",
    "De dag voelt daardoor {mood}.",
    "Het voelt dus {mood}.",
    "Ik heb het {mood}."
]

PREDICTION_PATTERNS = [
    "Voor vandaag wordt {temp} graden en {cond} voorspeld.",
    "De voorspelling voor vandaag: {temp} graden en {cond}.",
    "Vandaag wordt het naar verwachting {temp} graden met {cond}.",
    "De verwachting is {temp} graden en {cond} vandaag.",
]

CLOSINGS = [
    "Een fijne dag gewenst! Houdoe.",
    "Geniet van het weer en tot snel!",
    "Maak er een mooie dag van!",
    "Blijf warm en droog, en tot de volgende keer!",
    "Ik ga denk ik kipraps met surimikrapsalade eten als lunch! Wat gaan jullie eten?",
    "doei"
]

def jacket_advice(temp_c, condition_text, forecast_temp=None, forecast_condition=None):
    """Return a short Dutch recommendation about wearing a jacket.
    Considers both current weather and forecast if available."""
    temp = float(temp_c)
    cond = (condition_text or "").lower()
    
    # Use forecast if available, otherwise use current
    use_temp = forecast_temp if forecast_temp is not None else temp_c
    use_cond = forecast_condition.lower() if forecast_condition else cond
    
    # Check if forecast shows worse conditions
    forecast_worse = False
    if forecast_temp is not None and forecast_condition:
        forecast_cond_lower = forecast_condition.lower()
        if ("regen" in forecast_cond_lower or "bui" in forecast_cond_lower) and "regen" not in cond and "bui" not in cond:
            forecast_worse = True
        if forecast_temp < temp - 3:  # Forecast is significantly colder
            forecast_worse = True

    if use_temp <= 5:
        advice = "Doe zeker een dikke jas aan en misschien zelfs een sjaal om."
        if forecast_worse:
            advice += " En houd rekening met de voorspelling: het kan nog kouder worden."
        return advice
    if "regen" in use_cond or "bui" in use_cond or "motregen" in use_cond:
        advice = "Neem zeker een jas en liefst ook een regenjas mee."
        if forecast_worse:
            advice += " De voorspelling geeft aan dat het later nog natter kan worden."
        return advice
    if 5 < use_temp <= 12:
        advice = "Een jas is aan te raden, vooral in de ochtend en avond."
        if forecast_worse:
            advice += " De voorspelling suggereert dat het later kouder wordt."
        return advice
    if 12 < use_temp <= 18:
        return "Een lichte jas of vest is meestal voldoende."
    return "Een jas is vandaag echt niet nodig."


def bbq_advice(temp_c, condition_text):
    """Return a short Dutch recommendation about doing a barbecue."""
    temp = float(temp_c)
    cond = (condition_text or "").lower()

    if "onweer" in cond or "storm" in cond:
        return "Barbecue wordt afgeraden door de kans op onweer en harde wind."
    if "regen" in cond or "bui" in cond or "motregen" in cond:
        return "Barbecue kan, maar alleen met beschutting: er is kans op regen."
    if temp < 12:
        return "Het is vrij koud voor een lange barbecue buiten."
    if 12 <= temp <= 25:
        return "Prima barbecueweer als je rekening houdt met de wind."
    return "Het is behoorlijk warm, zorg bij een barbecue voor genoeg drinken en schaduw."

def build_forecast_text(city, temp_c, condition_text, mood_text, forecast_temp=None, forecast_condition=None):
    """
    Build a varied Dutch forecast text from simple templates,
    starting with weekday + date.
    Includes prediction if forecast data is available.
    """
    now = datetime.now()
    weekday = WEEKDAGEN_NL[now.weekday()]
    day = now.day
    month = MAANDEN_NL[now.month - 1]
    year = now.year
    datum_zin = f"Vandaag is het {weekday} {day} {month} {year}."

    temp = int(temp_c)
    parts = [
        random.choice(GREETINGS),
        datum_zin,
        random.choice(TEMP_PATTERNS).format(city=city, temp=temp),
        random.choice(COND_PATTERNS).format(cond=condition_text),
        random.choice(MOOD_PATTERNS).format(mood=mood_text),
    ]
    
    # Add prediction if available
    if forecast_temp is not None and forecast_condition:
        forecast_temp_int = int(forecast_temp)
        parts.append(random.choice(PREDICTION_PATTERNS).format(temp=forecast_temp_int, cond=forecast_condition))
    
    parts.extend([
        jacket_advice(temp_c, condition_text, forecast_temp, forecast_condition),
        # include bbq_advice(temp_c, condition_text) here if you still want it spoken
        random.choice(CLOSINGS),
    ])
    return " ".join(p.strip() for p in parts if p.strip())

# ---------- GIBBERISH VOICE & SYLLABLES ----------

VOWELS = set("aeiouáéíóúäëïöüAEIOU")


def _split_word_into_syllables(word: str):
    """
    Very rough syllable splitter: splits a word into chunks that each contain
    at least one vowel. This is not linguistically perfect but gives us
    small syllable-like units for timing.
    """
    syllables = []
    start = 0
    i = 0
    n = len(word)
    while i < n:
        # Move i until we've seen at least one vowel in this chunk
        has_vowel = False
        while i < n:
            if word[i] in VOWELS:
                has_vowel = True
            i += 1
            if has_vowel:
                break
        # Extend until just before next vowel (to keep following consonants)
        while i < n and word[i] not in VOWELS:
            # Stop if the remaining part is all consonants (avoid 1-char tails)
            if all(ch not in VOWELS for ch in word[i:]):
                i = n
                break
            i += 1
        syllables.append(word[start:i])
        start = i
    if start < n:
        syllables.append(word[start:n])
    return syllables


def split_into_syllable_tokens(text: str):
    """
    Split full text into a list of tokens that approximate syllables,
    while preserving spaces and punctuation as separate tokens.
    Joining all tokens reproduces the original text.
    """
    tokens = []
    current_word = ""
    for ch in text:
        if ch.isspace():
            # Flush any current word into syllables, keep space as separate token
            if current_word:
                tokens.extend(_split_word_into_syllables(current_word))
                current_word = ""
            tokens.append(ch)
        elif ch.isalpha():
            # Build up word characters
            current_word += ch
        else:
            # Punctuation or other non-space, non-letter character.
            # Flush the word into syllables first.
            if current_word:
                sylls = _split_word_into_syllables(current_word)
                if sylls:
                    # Attach punctuation to the last syllable so it is "part of"
                    # that spoken unit instead of a separate token.
                    sylls[-1] = sylls[-1] + ch
                    tokens.extend(sylls)
                else:
                    tokens.append(ch)
                current_word = ""
            else:
                # No word in progress: append punctuation to previous token if it
                # exists and is not purely whitespace, otherwise as its own token.
                if tokens and not tokens[-1].isspace():
                    tokens[-1] = tokens[-1] + ch
                else:
                    tokens.append(ch)
    if current_word:
        tokens.extend(_split_word_into_syllables(current_word))
    return tokens


def create_gibberish_voice(text, voices_dir=VOICE_CLIPS_DIR, out_file="voice.wav"):
    """
    Create a Banjo-Kazooie style gibberish voice by concatenating random WAV clips,
    one per (approximate) syllable in the forecast text, with extra pauses at
    sentence boundaries.
    Implemented with the built-in wave module to avoid MoviePy edge-case issues.
    """
    if not os.path.isdir(voices_dir):
        raise FileNotFoundError(
            f"Voice clips directory not found: {voices_dir}"
        )

    files = [
        os.path.join(voices_dir, f)
        for f in os.listdir(voices_dir)
        if f.lower().endswith(".wav")
    ]
    if not files:
        raise FileNotFoundError(
            f"No audio clips found in '{voices_dir}'. "
            "Add some .wav files."
        )

    tokens = split_into_syllable_tokens(text)

    combined_frames = bytearray()
    chosen_params = None
    current_time = 0.0

    # For syncing subtitles later: record timing for each syllable-like token
    syllable_events = []  # list of dicts: { "token_index": int, "start": float, "end": float }

    def append_silence(seconds: float):
        nonlocal combined_frames, chosen_params
        if chosen_params is None or seconds <= 0:
            return
        nframes = int(chosen_params.framerate * seconds)
        silence = b"\x00" * nframes * chosen_params.nchannels * chosen_params.sampwidth
        combined_frames.extend(silence)

    for idx, tok in enumerate(tokens):
        if tok.isspace():
            # Small pause for spaces
            append_silence(0.05)
            current_time += 0.05
            continue

        # For syllable-like tokens (may include punctuation like "." or "?"),
        # append one random clip and then, if it ends with sentence punctuation,
        # add an extra pause.
        path = random.choice(files)
        with wave.open(path, "rb") as wf:
            params = wf.getparams()
            raw_frames = wf.readframes(params.nframes)

        # Optional: apply small random pitch/speed modulation by resampling
        sampwidth = params.sampwidth
        n_channels = params.nchannels
        framerate = params.framerate or 44100

        dtype = None
        if sampwidth == 1:
            dtype = np.int8
        elif sampwidth == 2:
            dtype = np.int16
        elif sampwidth == 4:
            dtype = np.int32

        if dtype is not None:
            audio = np.frombuffer(raw_frames, dtype=dtype)
            if n_channels > 1:
                audio = audio.reshape((-1, n_channels))
            else:
                audio = audio.reshape((-1, 1))

            # Choose a small random pitch factor (0.9–1.1)
            pitch_factor = random.uniform(0.9, 1.1)
            orig_len = audio.shape[0]
            new_len = max(1, int(orig_len / pitch_factor))
            orig_idx = np.linspace(0, orig_len - 1, num=orig_len)
            new_idx = np.linspace(0, orig_len - 1, num=new_len)

            resampled = np.empty((new_len, n_channels), dtype=np.float32)
            for ch in range(n_channels):
                resampled[:, ch] = np.interp(
                    new_idx, orig_idx, audio[:, ch].astype(np.float32)
                )

            resampled_int = np.clip(
                resampled, np.iinfo(dtype).min, np.iinfo(dtype).max
            ).astype(dtype)
            frames = resampled_int.tobytes()
            n_frames = new_len
        else:
            # Fallback: no pitch change
            frames = raw_frames
            n_frames = params.nframes

        dur = n_frames / float(framerate)

        if chosen_params is None:
            chosen_params = params
        else:
            if (
                params.nchannels != chosen_params.nchannels
                or params.sampwidth != chosen_params.sampwidth
                or framerate != chosen_params.framerate
            ):
                raise ValueError(
                    "All voice clips must have the same channels, sample width and framerate."
                )

        combined_frames.extend(frames)

        # Record timing for this syllable token (audio portion only)
        start_time = current_time
        end_time = current_time + dur
        syllable_events.append(
            {"token_index": idx, "start": start_time, "end": end_time}
        )
        current_time = end_time

        # If this syllable ends with sentence punctuation, add a longer pause
        if tok.strip().endswith((".", "!", "?")):
            append_silence(0.6)
            current_time += 0.6

    # Write the combined WAV
    if chosen_params is None:
        raise RuntimeError("No voice clips were used to build gibberish audio.")

    with wave.open(out_file, "wb") as out_wf:
        out_wf.setparams(chosen_params)
        out_wf.writeframes(combined_frames)

    # Save timing metadata for subtitles/animation sync
    timing_path = os.path.splitext(out_file)[0] + "_timing.json"
    try:
        with open(timing_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "tokens": tokens,
                    "syllables": syllable_events,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        # Non-fatal: subtitles will fall back to unsynced mode if this fails
        pass

    return out_file


# ---------- SLIDE IMAGE ----------

def create_slide(city, temp_c, condition_text, mood_text, out_file="slide.png"):
    """
    Create a simple 1920x1080 PNG with text.
    """
    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), color=(0, 0, 0, 0))  # transparent background
    draw = ImageDraw.Draw(img)

    try:
        # You can drop any .ttf into the project and reference it here
        font_big = ImageFont.truetype("Arial.ttf", 90)
        font_medium = ImageFont.truetype("Arial.ttf", 60)
    except IOError:
        font_big = ImageFont.load_default()
        font_medium = ImageFont.load_default()

    img.save(out_file)
    return out_file


# ---------- VIDEO ----------

def create_video(slide_img, voice_file, music_file, forecast_text, temp_c, condition_text, mood_text, 
                 forecast_temp=None, forecast_condition=None, forecast_max=None, forecast_min=None, 
                 out_file="weer_vandaag.mp4"):
    """
    Combine the slide image, gibberish voice and background music into one MP4.
    Includes forecast overlay if forecast data is provided.
    """
    if not os.path.exists(music_file):
        raise FileNotFoundError(
            f"Background music file not found: {music_file}. "
            "Create the file under the 'music' folder or update the path."
        )

    voice = AudioFileClip(voice_file)
    music_base = AudioFileClip(music_file)

    # Avatar entry animation duration (1 second)
    avatar_entry_duration = 1.0

    # Total video duration: entry animation + voice + extra background-only time
    total_duration = avatar_entry_duration + voice.duration + EXTRA_TAIL_SECONDS

    # Fit background music to total_duration (loop or trim)
    if music_base.duration >= total_duration:
        music = music_base.subclipped(0, total_duration)
    else:
        loops = int(total_duration // music_base.duration) + 1
        music_long = concatenate_audioclips([music_base] * loops)
        music = music_long.subclipped(0, total_duration)

    # Music plays from the start, voice is delayed by avatar_entry_duration to sync with avatar entry animation
    voice_delayed = voice.with_start(avatar_entry_duration)

    # Voice is shorter; CompositeAudioClip will just have voice where present
    # Music starts immediately, voice starts after entry animation
    final_audio = CompositeAudioClip([music, voice_delayed])

    # --- Background visual: optional video, else static slide ---
    bg_clip = None
    bg_candidate = None
    if os.path.isdir(BACKGROUND_DIR):
        # Try to match background video name to music filename, e.g. rainy.mp4 for rainy.mp3
        music_name = os.path.splitext(os.path.basename(music_file))[0]
        candidate = os.path.join(BACKGROUND_DIR, f"{music_name}.mp4")
        if os.path.exists(candidate):
            bg_candidate = candidate

    if bg_candidate:
        base_bg = VideoFileClip(bg_candidate)
        base_duration = base_bg.duration if base_bg.duration and base_bg.duration > 0 else total_duration
        base_get_frame = base_bg.get_frame  # capture original frame function

        # Create a clip with the correct total duration
        if base_duration >= total_duration:
            bg_clip = base_bg.subclipped(0, total_duration)
        else:
            bg_clip = base_bg.with_duration(total_duration)

        # Combined looping + blur frame function, always calling back into base_bg.get_frame
        def looped_blurred_frame(t, get_frame=base_get_frame, dur=base_duration):
            if dur <= 0:
                frame = get_frame(0)
            else:
                t_wrapped = t % dur
                frame = get_frame(t_wrapped)
            img = Image.fromarray(frame)
            img = img.filter(ImageFilter.GaussianBlur(radius=12))
            return np.array(img)

        bg_clip.frame_function = looped_blurred_frame
    else:
        # Fallback: static slide stretched to total_duration (also blurred)
        base_clip = ImageClip(slide_img).with_duration(total_duration)
        base_get_frame = base_clip.get_frame

        def blurred_static_frame(t, get_frame=base_get_frame):
            frame = get_frame(t)
            img = Image.fromarray(frame)
            img = img.filter(ImageFilter.GaussianBlur(radius=12))
            return np.array(img)

        base_clip.frame_function = blurred_static_frame
        bg_clip = base_clip

    # --- Precompute audio envelope for bounce (based on voice loudness) ---
    # Use RMS per audio frame, normalized to [0,1]
    try:
        audio_array = voice.to_soundarray()
        # In case of stereo, average channels
        if audio_array.ndim == 2:
            rms = np.sqrt((audio_array.astype(float) ** 2).mean(axis=1))
        else:
            rms = np.sqrt((audio_array.astype(float) ** 2))
        max_rms = float(rms.max()) if rms.size > 0 else 0.0
        if max_rms > 0:
            env = rms / max_rms
        else:
            env = np.zeros_like(rms)
    except Exception:
        env = None

    # --- Bouncing avatar in lower-left (optional, if image exists) ---
    avatar_clip = None
    if os.path.exists(AVATAR_IMAGE):
        # Force avatar to a fixed 256x256 size for a consistent look
        avatar = ImageClip(AVATAR_IMAGE).resized((256, 256)).with_duration(total_duration)
        
        # Capture values for closure to avoid binding issues
        bg_height = bg_clip.h
        avatar_height = avatar.h
        entry_duration = avatar_entry_duration
        audio_env = env
        voice_duration = voice.duration if hasattr(voice, 'duration') else 0

        def bounce_pos(t):
            x = 40
            base_y = bg_height - avatar_height - 40
            amp = 35  # maximum bounce height in pixels
            
            # Entry animation: bounce up from below screen over 1 second
            if t < entry_duration:
                # Start position: below screen
                start_y = bg_height
                # End position: base position
                end_y = base_y
                # Use an easing function for smooth bounce-up animation
                progress = t / entry_duration
                
                # Smooth ease-out quintic (1 - (1-t)^5) for more natural deceleration
                # This creates a smoother, more gradual slowdown
                eased = 1 - (1 - progress) ** 5
                
                # Add a subtle bounce/overshoot at the end for a playful effect
                # The bounce is smaller and happens later for a more refined feel
                if progress > 0.8:
                    # Subtle bounce: smaller amplitude, shorter duration
                    bounce_progress = (progress - 0.8) / 0.2  # Normalize to 0-1 for last 20%
                    bounce_factor = 0.05 * math.sin(bounce_progress * math.pi)
                    eased += bounce_factor
                
                y = start_y + (end_y - start_y) * eased
            else:
                # After entry animation, use normal bouncing
                # Adjust time for bouncing calculation (subtract entry duration)
                bounce_t = t - entry_duration
                if audio_env is not None and voice_duration > 0:
                    # Map current time to envelope index (clamped)
                    if bounce_t >= voice_duration:
                        idx = len(audio_env) - 1
                    else:
                        idx = int((bounce_t / voice_duration) * (len(audio_env) - 1))
                    idx = max(0, min(len(audio_env) - 1, idx))
                    level = float(audio_env[idx])
                    y = base_y - amp * level
                else:
                    # Fallback: gentle idle bounce if envelope not available
                    y = base_y - 10 * abs(math.sin(2 * math.pi * bounce_t / 0.8))
            return (x, y)

        avatar_clip = avatar.with_position(bounce_pos)

    # --- Subtitles: render forecast text with Pillow next to the avatar/head,
    # syllable-based and synced to audio using timing metadata (if available) ---
    subtitle_clips = []
    if forecast_text:
        try:
            # Determine subtitle box width
            box_width = int(bg_clip.w * 0.75)

            # Load a font for normal text (subtitles)
            try:
                font = ImageFont.truetype("Arial.ttf", NORMAL_TEXT_FONT_SIZE)
            except IOError:
                font = ImageFont.load_default()

            # Position horizontally to the right of the avatar (if present)
            if os.path.exists(AVATAR_IMAGE):
                try:

                    x_pos = 256 + 20 + 60
                except Exception:
                    x_pos = 256 + 20 + 60
            else:
                x_pos = 256 + 20 + 60

            # Try to load precise timing from JSON created by create_gibberish_voice
            timing_path = os.path.splitext(voice_file)[0] + "_timing.json"
            tokens = None
            syllables = None
            if os.path.exists(timing_path):
                try:
                    with open(timing_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    tokens = meta.get("tokens")
                    syllables = meta.get("syllables")
                except Exception:
                    tokens = None
                    syllables = None

            # Fallback: if timing file missing, derive tokens without exact sync
            if not tokens:
                tokens = split_into_syllable_tokens(forecast_text)
            if not tokens:
                raise ValueError("Empty forecast_text for subtitles")

            # Determine syllable events for subtitles
            if syllables:
                syllable_events = syllables
            else:
                # Approximate: create evenly spaced events over voice duration
                syllable_indices = [
                    idx for idx, tok in enumerate(tokens) if any(ch.isalpha() for ch in tok)
                ]
                total_syllables = len(syllable_indices)
                if total_syllables == 0:
                    raise ValueError("No syllable tokens found for subtitles")
                syllable_events = []
                for step, idx in enumerate(syllable_indices, start=1):
                    start_t = (step - 1) / total_syllables * voice.duration
                    if step < total_syllables:
                        next_start = step / total_syllables * voice.duration
                        end_t = next_start
                    else:
                        end_t = voice.duration
                    syllable_events.append(
                        {"token_index": idx, "start": start_t, "end": end_t}
                    )

            total_events = len(syllable_events)
            # Find the first actual word (skip punctuation/whitespace)
            first_word_index = None
            first_word_start_t = None
            for i, ev in enumerate(syllable_events):
                idx = ev["token_index"]
                if idx < len(tokens):
                    token = tokens[idx]
                    # Check if this is an actual word (contains letters)
                    if any(ch.isalpha() for ch in token):
                        first_word_index = i
                        first_word_start_t = float(ev.get("start", 0.0))
                        break
            
            # If no word found, use first event
            if first_word_index is None and syllable_events:
                first_word_index = 0
                first_word_start_t = float(syllable_events[0].get("start", 0.0))
            
            for i, ev in enumerate(syllable_events):
                idx = ev["token_index"]
                start_t = float(ev.get("start", 0.0))
                
                # Don't show subtitle box until the first word is spoken
                if first_word_index is not None and i < first_word_index:
                    # This is before the first word, skip creating subtitle for this event
                    continue

                # Text up to and including this syllable token (accumulate from start)
                partial_text = "".join(tokens[: idx + 1])

                # Wrap partial text to fit the box width
                words = partial_text.split()
                lines = []
                current = ""
                for w in words:
                    test = (current + " " + w).strip()
                    if font.getlength(test) <= box_width:
                        current = test
                    else:
                        if current:
                            lines.append(current)
                        current = w
                if current:
                    lines.append(current)

                line_height = int(font.size * 1.3)
                text_height = line_height * len(lines)

                # Create a "speech bubble": rounded rectangle with padding behind text
                pad_x, pad_y = 20, 12
                bubble_w = box_width + 2 * pad_x
                bubble_h = text_height + 2 * pad_y

                subtitle_img = Image.new(
                    "RGBA", (bubble_w, bubble_h), (0, 0, 0, 0)
                )
                draw = ImageDraw.Draw(subtitle_img)

                # Bubble background (semi-transparent black with white border)
                try:
                    draw.rounded_rectangle(
                        [(0, 0), (bubble_w - 1, bubble_h - 1)],
                        radius=18,
                        fill=(0, 0, 0, 180),
                        outline=(255, 255, 255, 220),
                        width=SUBTITLE_BORDER_WIDTH,
                    )
                except AttributeError:
                    # Fallback if rounded_rectangle is unavailable
                    draw.rectangle(
                        [(0, 0), (bubble_w - 1, bubble_h - 1)],
                        fill=(0, 0, 0, 180),
                        outline=(255, 255, 255, 220),
                        width=SUBTITLE_BORDER_WIDTH,
                    )

                # Draw text inside the bubble with padding
                y = pad_y
                for line in lines:
                    draw.text((pad_x, y), line, font=font, fill=(255, 255, 255, 255))
                    y += line_height

                # Vertically: above bottom with padding
                y_pos = bg_clip.h - bubble_h - 50

                # Duration:
                # - For all but the last: keep subtitle on screen until the NEXT syllable starts,
                #   so text does not disappear during pauses between syllables.
                # - For the last: keep it until the end of the whole video (voice + tail).
                if i < total_events - 1:
                    next_start = float(syllable_events[i + 1].get("start", start_t))
                    clip_duration = max(0.01, next_start - start_t)
                else:
                    # Last subtitle stays until total_duration (voice + tail)
                    clip_duration = max(0.01, total_duration - start_t)

                # Delay subtitle start time by avatar_entry_duration to sync with audio
                subtitle_start_t = start_t + avatar_entry_duration
                
                sc = (
                    ImageClip(np.array(subtitle_img))
                    .with_duration(clip_duration)
                    .with_start(subtitle_start_t)
                    .with_position((x_pos, y_pos))
                )
                subtitle_clips.append(sc)
        except Exception:
            subtitle_clips = []

    # --- Temperature overlay: Icon + Temperature + City (centered column) ---
    temp_overlay = None
    temp_overlay_clips = None
    # Initialize these for use in forecast overlay
    temp_overlay_x = None
    temp_overlay_y = None
    temp_box_w = None
    try:
        # Icon size (larger)
        icon_size = 256
        icon_spacing = 40  # Space between icon and text (larger)
        
        # Load icon based on weather condition
        icon_path = get_weather_icon_path(condition_text, temp_c)
        
        # Load icon if available
        icon_img = None
        if icon_path and os.path.exists(icon_path):
            icon_img = Image.open(icon_path).convert("RGBA")
            icon_img = icon_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
        
        # Temperature text (larger)
        temp_label = f"{int(temp_c)}°C"
        try:
            temp_font = ImageFont.truetype("Arial.ttf", TEMPERATURE_TEXT_FONT_SIZE)
        except IOError:
            temp_font = ImageFont.load_default()
        
        # City name (larger)
        city_label = CITY
        try:
            city_font = ImageFont.truetype("Arial.ttf", 64)
        except IOError:
            city_font = ImageFont.load_default()
        
        # Calculate dimensions
        temp_w = int(temp_font.getlength(temp_label))
        temp_h = int(temp_font.size * 1.2)
        city_w = int(city_font.getlength(city_label))
        city_h = int(city_font.size * 1.2)
        
        # Total width is max of icon, temp, and city
        total_w = max(icon_size, temp_w, city_w)
        # Total height: icon + spacing + temp + spacing + city
        total_h = icon_size + icon_spacing + temp_h + icon_spacing + city_h
        
        # Add padding for the background box
        box_padding = 30
        box_w = total_w + 2 * box_padding
        box_h = total_h + 2 * box_padding
        
        # Create background box with radial gradient: dark blue, 90% opacity, with white border
        bg_box_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        
        # Create radial gradient: dark in center, lighter at edges
        # Dark blue center: RGB(0, 50, 100), lighter blue edge: RGB(20, 70, 120)
        center_x, center_y = box_w // 2, box_h // 2
        max_dist = math.sqrt(center_x**2 + center_y**2)  # Maximum distance from center
        
        # Draw radial gradient pixel by pixel
        for y in range(box_h):
            for x in range(box_w):
                # Calculate distance from center
                dist = math.sqrt((x - center_x)**2 + (y - center_y)**2)
                # Normalize distance (0 at center, 1 at corners)
                normalized_dist = min(1.0, dist / max_dist)
                
                # Interpolate between dark center and lighter edge
                r = int(0 + normalized_dist * 20)  # 0 to 20
                g = int(50 + normalized_dist * 20)  # 50 to 70
                b = int(100 + normalized_dist * 20)  # 100 to 120
                alpha = 230  # 90% opacity
                
                bg_box_img.putpixel((x, y), (r, g, b, alpha))
        
        # Draw white border on top of gradient
        bg_draw = ImageDraw.Draw(bg_box_img)
        border_color = (255, 255, 255, 255)  # White border
        bg_draw.rectangle(
            [(0, 0), (box_w - 1, box_h - 1)],
            fill=None,  # No fill (gradient already applied)
            outline=border_color,
            width=TEMPERATURE_OVERLAY_BORDER_WIDTH
        )
        
        # Create separate images for each element, all with same dimensions (box_w x box_h)
        # This ensures MoviePy can composite them properly
        # Content is offset by padding
        content_x_offset = box_padding
        content_y_offset = box_padding
        
        # Icon image (padded to box size with content offset)
        icon_img_separate = None
        if icon_img:
            icon_img_separate = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            icon_x = content_x_offset + (total_w - icon_size) // 2
            icon_y = content_y_offset
            icon_img_separate.paste(icon_img, (icon_x, icon_y), icon_img)
        
        # Temperature image (padded to box size, positioned at correct y with offset)
        temp_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        temp_draw = ImageDraw.Draw(temp_img)
        temp_x = content_x_offset + (total_w - temp_w) // 2
        temp_y_in_img = content_y_offset + icon_size + icon_spacing
        temp_draw.text((temp_x, temp_y_in_img), temp_label, font=temp_font, fill=(255, 255, 255, 255))
        
        # City image (padded to box size, positioned at correct y with offset)
        city_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        city_draw = ImageDraw.Draw(city_img)
        city_x = content_x_offset + (total_w - city_w) // 2
        city_y_in_img = content_y_offset + icon_size + icon_spacing + temp_h + icon_spacing
        city_draw.text((city_x, city_y_in_img), city_label, font=city_font, fill=(255, 255, 255, 255))
        
        # Calculate positions for overlay (all clips use same position since they're same size)
        # Move up by 100 pixels
        overlay_x = (bg_clip.w - box_w) // 2
        overlay_y = (bg_clip.h - box_h) // 2 - 100
        
        # Store these for use in forecast overlay
        temp_overlay_x = overlay_x
        temp_overlay_y = overlay_y
        temp_box_w = box_w

        # Default fade start (fallback) - delay by avatar_entry_duration
        fade_start = avatar_entry_duration + 1.0
        fade_duration = 0.5

        # Try to sync fade_start with the syllable where temperature is spoken
        try:
            timing_path = os.path.splitext(voice_file)[0] + "_timing.json"
            if os.path.exists(timing_path):
                with open(timing_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                tokens = meta.get("tokens") or []
                syllables = meta.get("syllables") or []

                # Find first syllable token that contains the numeric temperature or 'graden'
                temp_int = int(temp_c)
                for ev in syllables:
                    idx = ev.get("token_index")
                    if idx is None or idx < 0 or idx >= len(tokens):
                        continue
                    tok = str(tokens[idx])
                    if str(temp_int) in tok or "graden" in tok.lower():
                        # Add avatar_entry_duration to sync with delayed audio
                        fade_start = avatar_entry_duration + float(ev.get("start", 1.0))
                        break
        except Exception:
            # If anything fails, just keep the default fade_start
            pass

        # Clip only needs to run from fade_start to end
        visible_duration = max(0.0, total_duration - fade_start)
        if visible_duration <= 0:
            temp_overlay = None
            temp_overlay_clips = None
        else:
            # Stagger delays: 0ms, 50ms, 100ms
            stagger_delay = 0.05  # 50ms in seconds
            overlay_clips = []
            
            # Create clips and apply CrossFadeIn
            # CrossFadeIn fades from t=0 of the clip, so we apply it first, then set start times
            crossfade_effect = CrossFadeIn(duration=fade_duration)
            
            # Background box clip (fades in first, appears behind everything)
            bg_box_arr = np.array(bg_box_img).astype(np.uint8)
            bg_box_clip = ImageClip(bg_box_arr).with_duration(visible_duration)
            try:
                bg_box_clip = crossfade_effect.apply(bg_box_clip)
            except Exception as e:
                print(f"Warning: Background box CrossFadeIn failed: {e}")
            bg_box_clip = bg_box_clip.with_start(fade_start).with_position((overlay_x, overlay_y))
            overlay_clips.append(bg_box_clip)  # Add first so it appears behind
            
            # Icon clip (fades in first, no delay)
            if icon_img_separate:
                icon_arr = np.array(icon_img_separate).astype(np.uint8)
                icon_clip = ImageClip(icon_arr).with_duration(visible_duration)
                try:
                    icon_clip = crossfade_effect.apply(icon_clip)
                except Exception as e:
                    print(f"Warning: Icon CrossFadeIn failed: {e}")
                icon_clip = icon_clip.with_start(fade_start).with_position((overlay_x, overlay_y))
                overlay_clips.append(icon_clip)
            
            # Temperature clip (fades in second, 50ms delay)
            temp_arr = np.array(temp_img).astype(np.uint8)
            temp_clip = ImageClip(temp_arr).with_duration(visible_duration)
            try:
                temp_clip = crossfade_effect.apply(temp_clip)
            except Exception as e:
                print(f"Warning: Temp CrossFadeIn failed: {e}")
            temp_clip = temp_clip.with_start(fade_start + stagger_delay).with_position((overlay_x, overlay_y))
            overlay_clips.append(temp_clip)
            
            # City clip (fades in third, 100ms delay)
            city_arr = np.array(city_img).astype(np.uint8)
            city_clip = ImageClip(city_arr).with_duration(visible_duration)
            try:
                city_clip = crossfade_effect.apply(city_clip)
            except Exception as e:
                print(f"Warning: City CrossFadeIn failed: {e}")
            city_clip = city_clip.with_start(fade_start + 2 * stagger_delay).with_position((overlay_x, overlay_y))
            overlay_clips.append(city_clip)
            
            # Store clips for later composition (don't composite them together yet)
            # CrossFadeIn works better when clips are composited directly over background
            temp_overlay_clips = overlay_clips if overlay_clips else None
    except Exception:
        temp_overlay = None
        temp_overlay_clips = None

    # --- Forecast overlay: Display to the right of current weather ---
    forecast_overlay_clips = None
    print(f"DEBUG: forecast_temp={forecast_temp}, forecast_condition={forecast_condition}")
    if forecast_temp is not None and forecast_condition:
        print(f"DEBUG: Creating forecast overlay...")
        try:
            # Use smaller icon for forecast
            forecast_icon_size = 128
            forecast_icon_spacing = 20
            
            # Load icon for forecast
            forecast_icon_path = get_weather_icon_path(forecast_condition, forecast_temp)
            forecast_icon_img = None
            if forecast_icon_path and os.path.exists(forecast_icon_path):
                forecast_icon_img = Image.open(forecast_icon_path).convert("RGBA")
                forecast_icon_img = forecast_icon_img.resize((forecast_icon_size, forecast_icon_size), Image.Resampling.LANCZOS)
            
            # Forecast temperature text
            forecast_temp_label = f"{int(forecast_temp)}°C"
            try:
                forecast_temp_font = ImageFont.truetype("Arial.ttf", 80)
            except IOError:
                forecast_temp_font = ImageFont.load_default()
            
            # Forecast label
            forecast_label_text = "Voorspelling"
            try:
                forecast_label_font = ImageFont.truetype("Arial.ttf", 40)
            except IOError:
                forecast_label_font = ImageFont.load_default()
            
            # Calculate dimensions
            forecast_temp_w = int(forecast_temp_font.getlength(forecast_temp_label))
            forecast_temp_h = int(forecast_temp_font.size * 1.2)
            forecast_label_w = int(forecast_label_font.getlength(forecast_label_text))
            forecast_label_h = int(forecast_label_font.size * 1.2)
            
            # Total width is max of icon, temp, and label
            forecast_total_w = max(forecast_icon_size, forecast_temp_w, forecast_label_w)
            # Total height: label + spacing + icon + spacing + temp
            forecast_total_h = forecast_label_h + forecast_icon_spacing + forecast_icon_size + forecast_icon_spacing + forecast_temp_h
            
            # Add padding for the background box
            forecast_box_padding = 20
            forecast_box_w = forecast_total_w + 2 * forecast_box_padding
            forecast_box_h = forecast_total_h + 2 * forecast_box_padding
            
            # Create forecast background box with radial gradient
            forecast_bg_box_img = Image.new("RGBA", (forecast_box_w, forecast_box_h), (0, 0, 0, 0))
            
            # Create radial gradient
            forecast_center_x, forecast_center_y = forecast_box_w // 2, forecast_box_h // 2
            forecast_max_dist = math.sqrt(forecast_center_x**2 + forecast_center_y**2)
            
            # Draw radial gradient
            for y in range(forecast_box_h):
                for x in range(forecast_box_w):
                    dist = math.sqrt((x - forecast_center_x)**2 + (y - forecast_center_y)**2)
                    normalized_dist = min(1.0, dist / forecast_max_dist)
                    r = int(0 + normalized_dist * 20)
                    g = int(50 + normalized_dist * 20)
                    b = int(100 + normalized_dist * 20)
                    alpha = 230
                    forecast_bg_box_img.putpixel((x, y), (r, g, b, alpha))
            
            # Draw white border
            forecast_bg_draw = ImageDraw.Draw(forecast_bg_box_img)
            forecast_bg_draw.rectangle(
                [(0, 0), (forecast_box_w - 1, forecast_box_h - 1)],
                fill=None,
                outline=(255, 255, 255, 255),
                width=FORECAST_OVERLAY_BORDER_WIDTH
            )
            
            # Create separate images for each element
            forecast_content_x_offset = forecast_box_padding
            forecast_content_y_offset = forecast_box_padding
            
            # Forecast label image
            forecast_label_img = Image.new("RGBA", (forecast_box_w, forecast_box_h), (0, 0, 0, 0))
            forecast_label_draw = ImageDraw.Draw(forecast_label_img)
            forecast_label_x = forecast_content_x_offset + (forecast_total_w - forecast_label_w) // 2
            forecast_label_y = forecast_content_y_offset
            forecast_label_draw.text((forecast_label_x, forecast_label_y), forecast_label_text, 
                                     font=forecast_label_font, fill=(255, 255, 255, 255))
            
            # Forecast icon image
            forecast_icon_img_separate = None
            if forecast_icon_img:
                forecast_icon_img_separate = Image.new("RGBA", (forecast_box_w, forecast_box_h), (0, 0, 0, 0))
                forecast_icon_x = forecast_content_x_offset + (forecast_total_w - forecast_icon_size) // 2
                forecast_icon_y = forecast_content_y_offset + forecast_label_h + forecast_icon_spacing
                forecast_icon_img_separate.paste(forecast_icon_img, (forecast_icon_x, forecast_icon_y), forecast_icon_img)
            
            # Forecast temperature image
            forecast_temp_img = Image.new("RGBA", (forecast_box_w, forecast_box_h), (0, 0, 0, 0))
            forecast_temp_draw = ImageDraw.Draw(forecast_temp_img)
            forecast_temp_x = forecast_content_x_offset + (forecast_total_w - forecast_temp_w) // 2
            forecast_temp_y = forecast_content_y_offset + forecast_label_h + forecast_icon_spacing + forecast_icon_size + forecast_icon_spacing
            forecast_temp_draw.text((forecast_temp_x, forecast_temp_y), forecast_temp_label, 
                                   font=forecast_temp_font, fill=(255, 255, 255, 255))
            
            # Position forecast box to the right of current weather box
            # Current weather is centered, so forecast goes to the right
            # Use temp_overlay_x/box_w if available, otherwise calculate from center
            try:
                forecast_overlay_x = temp_overlay_x + temp_box_w + 40  # 40px gap between boxes
                forecast_overlay_y = temp_overlay_y  # Same vertical position
            except NameError:
                # Fallback: center both boxes side by side
                total_boxes_width = forecast_box_w + 40 + temp_box_w if 'temp_box_w' in locals() else forecast_box_w
                forecast_overlay_x = (bg_clip.w - total_boxes_width) // 2 + (temp_box_w if 'temp_box_w' in locals() else 0) + 40
                forecast_overlay_y = (bg_clip.h - forecast_box_h) // 2 - 100
            
            # Create clips with fade-in
            forecast_overlay_clips = []
            fade_duration = 0.5
            stagger_delay = 0.05
            visible_duration = total_duration - fade_start
            
            # Background box
            forecast_bg_arr = np.array(forecast_bg_box_img).astype(np.uint8)
            forecast_bg_clip = ImageClip(forecast_bg_arr).with_duration(visible_duration)
            try:
                crossfade_effect = CrossFadeIn(duration=fade_duration)
                forecast_bg_clip = crossfade_effect.apply(forecast_bg_clip)
            except Exception as e:
                print(f"Warning: Forecast background CrossFadeIn failed: {e}")
            forecast_bg_clip = forecast_bg_clip.with_start(fade_start).with_position((forecast_overlay_x, forecast_overlay_y))
            forecast_overlay_clips.append(forecast_bg_clip)
            
            # Label clip
            forecast_label_arr = np.array(forecast_label_img).astype(np.uint8)
            forecast_label_clip = ImageClip(forecast_label_arr).with_duration(visible_duration)
            try:
                forecast_label_clip = crossfade_effect.apply(forecast_label_clip)
            except Exception as e:
                print(f"Warning: Forecast label CrossFadeIn failed: {e}")
            forecast_label_clip = forecast_label_clip.with_start(fade_start).with_position((forecast_overlay_x, forecast_overlay_y))
            forecast_overlay_clips.append(forecast_label_clip)
            
            # Icon clip
            if forecast_icon_img_separate:
                forecast_icon_arr = np.array(forecast_icon_img_separate).astype(np.uint8)
                forecast_icon_clip = ImageClip(forecast_icon_arr).with_duration(visible_duration)
                try:
                    forecast_icon_clip = crossfade_effect.apply(forecast_icon_clip)
                except Exception as e:
                    print(f"Warning: Forecast icon CrossFadeIn failed: {e}")
                forecast_icon_clip = forecast_icon_clip.with_start(fade_start + stagger_delay).with_position((forecast_overlay_x, forecast_overlay_y))
                forecast_overlay_clips.append(forecast_icon_clip)
            
            # Temperature clip
            forecast_temp_arr = np.array(forecast_temp_img).astype(np.uint8)
            forecast_temp_clip = ImageClip(forecast_temp_arr).with_duration(visible_duration)
            try:
                forecast_temp_clip = crossfade_effect.apply(forecast_temp_clip)
            except Exception as e:
                print(f"Warning: Forecast temp CrossFadeIn failed: {e}")
            forecast_temp_clip = forecast_temp_clip.with_start(fade_start + 2 * stagger_delay).with_position((forecast_overlay_x, forecast_overlay_y))
            forecast_overlay_clips.append(forecast_temp_clip)
            
        except Exception as e:
            print(f"Warning: Could not create forecast overlay: {e}")
            forecast_overlay_clips = None

    # --- Composite video ---
    video_layers = [bg_clip]
    if avatar_clip is not None:
        video_layers.append(avatar_clip)
    # Add temp overlay clips directly (CrossFadeIn works better when composited over background)
    if temp_overlay_clips:
        video_layers.extend(temp_overlay_clips)
    elif temp_overlay is not None:
        video_layers.append(temp_overlay)
    # Add forecast overlay clips
    if forecast_overlay_clips:
        video_layers.extend(forecast_overlay_clips)
    if subtitle_clips:
        video_layers.extend(subtitle_clips)

    final_video = CompositeVideoClip(video_layers)
    final_video = final_video.with_audio(final_audio)
    
    # Cut last 8 frames to prevent weird stuff at the end (8 frames at 24fps = 0.333 seconds)
    fps = 24
    frames_to_cut = 8
    cut_duration = frames_to_cut / fps
    
    # Get actual video duration and trim if needed
    # Use total_duration as the source of truth since that's what we calculated
    actual_duration = total_duration
    if actual_duration and actual_duration > cut_duration:
        new_end_time = actual_duration - cut_duration
        # Ensure we don't go negative or create invalid range
        if new_end_time > 0 and new_end_time < actual_duration:
            try:
                final_video = final_video.subclipped(0, new_end_time)
            except ValueError as e:
                # If subclipped fails, try using the actual duration from the clip
                clip_duration = final_video.duration
                if clip_duration and clip_duration > cut_duration:
                    safe_end = clip_duration - cut_duration
                    if safe_end > 0:
                        final_video = final_video.subclipped(0, safe_end)
                    else:
                        print(f"Warning: Cannot trim video, duration too short. Skipping trim.")
                else:
                    print(f"Warning: Cannot trim video: {e}. Skipping trim.")
        else:
            print(f"Warning: Invalid trim parameters. Duration: {actual_duration:.2f}s, Cut: {cut_duration:.2f}s. Skipping trim.")

    final_video.write_videofile(out_file, fps=fps, codec="libx264", audio_codec="aac")

    voice.close()
    music_base.close()
    final_video.close()


# ---------- DISCORD POSTING ----------

def post_to_discord(video_path, webhook_url=None):
    """
    Post the generated video to Discord using a webhook.
    
    Args:
        video_path: Path to the video file to upload
        webhook_url: Discord webhook URL (or set DISCORD_WEBHOOK_URL env var)
    
    Returns:
        True if successful, False otherwise
    """
    webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
    
    if not webhook_url:
        print("Warning: No Discord webhook URL provided. Set DISCORD_WEBHOOK_URL environment variable or pass webhook_url parameter.")
        return False
    
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        return False
    
    # Check file size (Discord webhook limit is 25MB)
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if file_size_mb > 25:
        print(f"Warning: Video file is {file_size_mb:.1f}MB, which exceeds Discord webhook limit of 25MB.")
        print("Consider using a Discord bot instead, or compress the video.")
        return False
    
    try:
        print(f"Uploading video to Discord ({file_size_mb:.1f}MB)...")
        
        # Get current time for message
        now = datetime.now()
        time_str = now.strftime("%H:%M")
        
        # Prepare the message
        message = f"🌤️ Weersverwachting - {now.strftime('%d %B %Y')} om {time_str}"
        
        # Upload file to Discord webhook
        with open(video_path, 'rb') as video_file:
            files = {
                'file': (os.path.basename(video_path), video_file, 'video/mp4')
            }
            data = {
                'content': message
            }
            
            response = requests.post(webhook_url, files=files, data=data)
            response.raise_for_status()
        
        print("✅ Video successfully posted to Discord!")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error posting to Discord: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error posting to Discord: {e}")
        return False


# ---------- MAIN ----------

def main(post_to_discord_enabled=True):
    print("Fetching current weather...")
    temp_c, condition_text = get_weather()
    
    print("Fetching forecast...")
    forecast_temp, forecast_condition, forecast_max, forecast_min = get_forecast()
    
    mood_text, music_file = pick_mood_and_music(temp_c, condition_text, forecast_temp, forecast_condition)

    forecast_text = build_forecast_text(CITY, temp_c, condition_text, mood_text, forecast_temp, forecast_condition)
    print("Forecast text:", forecast_text)
    print("Generating gibberish voice...")
    voice_file = create_gibberish_voice(forecast_text)

    print("Creating slide image...")
    slide_img = create_slide(CITY, temp_c, condition_text, mood_text)

    print("Rendering video...")
    video_path = "weer_vandaag.mp4"
    create_video(slide_img, voice_file, music_file, forecast_text, temp_c, condition_text, mood_text, 
                 forecast_temp, forecast_condition, forecast_max, forecast_min, out_file=video_path)

    print("Done! Video saved as 'weer_vandaag.mp4'")
    
    # Post to Discord if enabled
    if post_to_discord_enabled:
        post_to_discord(video_path)

if __name__ == "__main__":
    import sys
    # Allow disabling Discord posting via command line argument
    post_enabled = "--no-discord" not in sys.argv
    main(post_to_discord_enabled=post_enabled)