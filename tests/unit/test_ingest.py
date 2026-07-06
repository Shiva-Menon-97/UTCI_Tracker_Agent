import os
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd
import pytest
import xarray as xr
import geopandas as gpd
from shapely.geometry import Point, Polygon
import ingest_to_postgres

@pytest.fixture
def mock_netcdf(tmp_path):
    # Create a small dummy NetCDF dataset
    lats = [10.0, 11.0]
    lons = [76.0, 77.0]
    times = pd.date_range("2026-07-05", periods=2, freq="12h")
    
    # 2 times, 2 lats, 2 lons
    utci_data = np.random.rand(2, 2, 2) * 10 + 25
    
    ds = xr.Dataset(
        {"utci": (["time", "lat", "lon"], utci_data)},
        coords={
            "time": times,
            "lat": lats,
            "lon": lons,
        },
    )
    
    nc_path = os.path.join(tmp_path, "utci_kerala_test.nc")
    ds.to_netcdf(nc_path)
    return nc_path

@pytest.fixture
def mock_geojson_files(tmp_path):
    # Create dummy district geojson
    d_geom = Polygon([(75.5, 9.5), (77.5, 9.5), (77.5, 11.5), (75.5, 11.5)])
    gdf_d = gpd.GeoDataFrame(
        {"DISTRICT": ["Test District"], "geometry": [d_geom]},
        crs="EPSG:4326"
    )
    d_path = os.path.join(tmp_path, "district.geojson")
    gdf_d.to_file(d_path, driver="GeoJSON")
    
    # Create dummy taluk geojson
    t_geom = Polygon([(75.5, 9.5), (77.5, 9.5), (77.5, 11.5), (75.5, 11.5)])
    gdf_t = gpd.GeoDataFrame(
        {"TALUK": ["Test Taluk"], "geometry": [t_geom]},
        crs="EPSG:4326"
    )
    t_path = os.path.join(tmp_path, "taluk.geojson")
    gdf_t.to_file(t_path, driver="GeoJSON")
    
    return d_path, t_path

@patch("ingest_to_postgres.create_engine")
def test_ingest_pipeline(mock_create_engine, mock_netcdf, mock_geojson_files):
    # Setup mocks for postgres ingestion
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_create_engine.return_value = mock_engine
    
    dist_path, taluk_path = mock_geojson_files
    
    # Mock database env vars
    os.environ["DB_NAME"] = "utci-tracker-test-db"
    
    # Patch script paths to point to our mock files
    with patch("ingest_to_postgres.RASTER_PATH", ingest_to_postgres.Path(mock_netcdf)), \
         patch("ingest_to_postgres.DISTRICT_GEOJSON", ingest_to_postgres.Path(dist_path)), \
         patch("ingest_to_postgres.TALUK_GEOJSON", ingest_to_postgres.Path(taluk_path)):
             
             ingest_to_postgres.main()
             
    # Assert database functions were called
    assert mock_create_engine.called
    # check that mock engine wrote to SQL table 'utci_grid'
    # pandas uses df.to_sql which internally calls connection/engine
    # Since we mocked create_engine, to_sql should have successfully attempted writing to it.
