import os
from unittest.mock import patch, MagicMock
import numpy as np
import pytest
import xarray as xr
from compute_utci import main, LAT_NORTH, LAT_SOUTH, LON_WEST, LON_EAST, RESOLUTION

@pytest.fixture
def mock_open_meteo():
    # Generate mock response JSON
    # Number of points on grid
    lats = np.arange(LAT_NORTH, LAT_SOUTH - 0.01, -RESOLUTION)
    lons = np.arange(LON_WEST, LON_EAST + 0.01, RESOLUTION)
    num_points = len(lats) * len(lons)
    
    # 8 days * 24 hours = 192 hours
    times = [f"2026-07-06T{hr:02d}:00" for d in range(8) for hr in range(24)]
    
    mock_hourly_data = {
        "time": times,
        "temperature_2m": [30.0] * 192,
        "dew_point_2m": [22.0] * 192,
        "wind_speed_10m": [2.0] * 192,
        "shortwave_radiation": [500.0] * 192,
        "direct_radiation": [300.0] * 192,
        "direct_normal_irradiance": [400.0] * 192,
        "cloud_cover": [50.0] * 192,
    }
    
    return mock_hourly_data

@patch("compute_utci.requests.get")
@patch("compute_utci.datetime")
def test_compute_utci_pipeline(mock_datetime, mock_get, tmp_path):
    # Mock current time to 2026-07-06 13:00:00 (so UTC indices 8 of today are in the past)
    import datetime as dt_module
    mock_datetime.utcnow.return_value = dt_module.datetime(2026, 7, 6, 13, 0, 0)
    mock_datetime.strptime = dt_module.datetime.strptime
    
    # Grid setup
    lats = np.arange(LAT_NORTH, LAT_SOUTH - 0.01, -RESOLUTION)
    lons = np.arange(LON_WEST, LON_EAST + 0.01, RESOLUTION)
    num_points = len(lats) * len(lons)
    
    # Mock Open-Meteo response
    # It sends batched request, so we need to return data for the size of the batch.
    times = [f"2026-07-06T{hr:02d}:00" for d in range(8) for hr in range(24)]
    single_location_data = {
        "hourly": {
            "time": times,
            "temperature_2m": [30.0] * 192,
            "dew_point_2m": [22.0] * 192,
            "wind_speed_10m": [2.0] * 192,
            "shortwave_radiation": [500.0] * 192,
            "direct_radiation": [300.0] * 192,
            "direct_normal_irradiance": [400.0] * 192,
            "cloud_cover": [50.0] * 192,
        }
    }
    
    def side_effect(url, params, timeout=60):
        # Determine batch size from the query params
        batch_lats = params["latitude"].split(",")
        mock_res = MagicMock()
        mock_res.status_code = 200
        if len(batch_lats) > 1:
            mock_res.json.return_value = [single_location_data] * len(batch_lats)
        else:
            mock_res.json.return_value = single_location_data
        return mock_res
        
    mock_get.side_effect = side_effect
    
    # Mock output file to tmp_path
    output_file = os.path.join(tmp_path, "utci_kerala_test.nc")
    
    with patch("compute_utci.OUTPUT_NC_FILE", output_file):
        with patch("compute_utci.Path.mkdir"), patch("compute_utci.Path.write_text"), patch("compute_utci.Path.exists", return_value=False):
            main()
            
    # Check if NetCDF file was created and contains valid structure
    assert os.path.exists(output_file)
    ds = xr.open_dataset(output_file)
    assert "utci" in ds.variables
    assert ds.utci.shape[1] == len(lats)
    assert ds.utci.shape[2] == len(lons)
    assert ds.utci.shape[0] > 0
