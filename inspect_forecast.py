#!/usr/bin/env python3
"""
Helper script to inspect KNMI forecast NetCDF files and find available variables.
"""

import os
import tempfile
import requests

try:
    import netCDF4
    import numpy as np
except ImportError:
    print("Error: netCDF4 and numpy are required. Install with: pip install netCDF4 numpy")
    exit(1)

# Use the same API key as the main script
KNMI_API_KEY = os.environ.get("KNMI_API_KEY") or "eyJvcmciOiI1ZTU1NGUxOTI3NGE5NjAwMDEyYTNlYjEiLCJpZCI6ImVlNDFjMWI0MjlkODQ2MThiNWI4ZDViZDAyMTM2YTM3IiwiaCI6Im11cm11cjEyOCJ9"

base_url = "https://api.dataplatform.knmi.nl/open-data/v1"
dataset_name = "uwcw_extra_lv_ha43_nl_2km"
dataset_version = "1.0"

print("Fetching most recent forecast file from KNMI...")
list_url = f"{base_url}/datasets/{dataset_name}/versions/{dataset_version}/files"
headers = {"Authorization": KNMI_API_KEY}

params = {
    "maxKeys": 1,
    "sorting": "desc",
    "orderBy": "lastModified"
}

list_resp = requests.get(list_url, headers=headers, params=params)
list_resp.raise_for_status()
list_data = list_resp.json()

if not list_data.get("files"):
    print("Error: No forecast files found")
    exit(1)

filename = list_data["files"][0]["filename"]
print(f"Found file: {filename}")

# Get download URL
download_url_endpoint = f"{base_url}/datasets/{dataset_name}/versions/{dataset_version}/files/{filename}/url"
download_resp = requests.get(download_url_endpoint, headers=headers)
download_resp.raise_for_status()
download_data = download_resp.json()
temp_download_url = download_data["temporaryDownloadUrl"]

# Download and inspect
print("Downloading and inspecting file...")
with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp_file:
    tmp_path = tmp_file.name
    try:
        file_resp = requests.get(temp_download_url, stream=True)
        file_resp.raise_for_status()
        for chunk in file_resp.iter_content(chunk_size=8192):
            tmp_file.write(chunk)
        tmp_file.flush()
        
        with netCDF4.Dataset(tmp_path, 'r') as nc:
            print("\n=== Available Variables ===")
            print(", ".join(sorted(nc.variables.keys())))
            
            print("\n=== Variable Details ===")
            for var_name in sorted(nc.variables.keys()):
                var = nc.variables[var_name]
                print(f"\n{var_name}:")
                print(f"  Shape: {var.shape}")
                print(f"  Dimensions: {var.dimensions}")
                if hasattr(var, 'long_name'):
                    print(f"  Long name: {var.long_name}")
                if hasattr(var, 'units'):
                    print(f"  Units: {var.units}")
            
            # Check for lat/lon
            print("\n=== Coordinate Variables ===")
            for var_name in ['lat', 'latitude', 'LAT', 'LATITUDE', 'lon', 'longitude', 'LON', 'LONGITUDE']:
                if var_name in nc.variables:
                    var = nc.variables[var_name]
                    data = np.array(var[:])
                    print(f"{var_name}: shape={data.shape}, min={np.min(data):.2f}, max={np.max(data):.2f}")
            
            # Check for temperature variables
            print("\n=== Temperature Variables ===")
            for var_name in ['t2m', 'T2M', 'temperature', 'temp', 'ta', 'TA', 'temperature_2m', 'air_temperature']:
                if var_name in nc.variables:
                    var = nc.variables[var_name]
                    data = np.array(var[:])
                    print(f"{var_name}: shape={data.shape}")
                    if data.size > 0:
                        print(f"  Min: {np.min(data):.2f}, Max: {np.max(data):.2f}, Mean: {np.mean(data):.2f}")
            
            # Check for weather code
            print("\n=== Weather Code Variables ===")
            for var_name in ['ww', 'WW', 'weather_code', 'present_weather', 'wmo_ww']:
                if var_name in nc.variables:
                    var = nc.variables[var_name]
                    data = np.array(var[:])
                    print(f"{var_name}: shape={data.shape}")
                    if data.size > 0:
                        print(f"  Min: {np.min(data)}, Max: {np.max(data)}")
            
            # Check dimensions
            print("\n=== Dimensions ===")
            for dim_name in nc.dimensions:
                dim = nc.dimensions[dim_name]
                print(f"{dim_name}: size={dim.size}")
        
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

print("\n=== Inspection Complete ===")
