import logging
import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from sqlalchemy import create_engine

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Resolve paths
SCRIPT_DIR = Path(__file__).parent.resolve()
RASTER_PATH = SCRIPT_DIR / "utci_kerala.nc"
DISTRICT_GEOJSON = SCRIPT_DIR / "kerala_geojsons/district.geojson"
TALUK_GEOJSON = SCRIPT_DIR / "kerala_geojsons/taluk.geojson"


def main():
    # Database connection parameters from environment or defaults
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ.get("DB_PASSWORD", "postgres")
    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "utci-tracker-db")

    # Connection string
    connection_string = (
        f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )

    logging.info("Checking local files...")
    if not RASTER_PATH.exists():
        logging.error(
            "UTCI NetCDF file not found: %s. Run compute_utci.py first.", RASTER_PATH
        )
        sys.exit(1)
    if not DISTRICT_GEOJSON.exists() or not TALUK_GEOJSON.exists():
        logging.error("GeoJSON boundary files not found in kerala_geojsons/ directory.")
        sys.exit(1)

    # 1. Read the NetCDF file and extract pixel coordinates and values
    logging.info("Reading NetCDF raster...")

    ds = xr.open_dataset(RASTER_PATH)
    utci_da = ds.utci

    # We want to perform the spatial join ONCE on the unique grid coordinates.
    # Get the unique lon/lats from the grid
    lats = ds.lat.values
    lons = ds.lon.values

    unique_points = []

    for lat in lats:
        for lon in lons:
            # We skip coordinates where the value is all NaNs across time.
            if np.isnan(utci_da.sel(lat=lat, lon=lon).values).all():
                continue

            lat_rad = np.radians(lat)
            # The spatial resolution is ~ 0.1 degrees
            width_km = 0.1 * 111.32 * np.cos(lat_rad)
            height_km = 0.1 * 110.57
            pixel_area = width_km * height_km

            unique_points.append(
                {
                    "longitude": float(lon),
                    "latitude": float(lat),
                    "pixel_area_km2": float(pixel_area),
                }
            )

    logging.info("Extracted %d valid unique grid locations.", len(unique_points))
    df_points = pd.DataFrame(unique_points)

    # Create GeoDataFrame from points
    gdf_points = gpd.GeoDataFrame(
        df_points,
        geometry=gpd.points_from_xy(df_points.longitude, df_points.latitude),
        crs="EPSG:4326",
    )

    # 2. Read administrative boundaries
    logging.info("Reading GeoJSON boundary files...")
    gdf_districts = gpd.read_file(DISTRICT_GEOJSON).to_crs("EPSG:4326")
    gdf_taluks = gpd.read_file(TALUK_GEOJSON).to_crs("EPSG:4326")

    # 3. Spatial Joins to map each pixel to its corresponding district and taluk
    logging.info("Mapping points to administrative boundaries (spatial join)...")

    gdf_mapped = gpd.sjoin(
        gdf_points,
        gdf_districts[["DISTRICT", "geometry"]],
        how="left",
        predicate="within",
    )
    gdf_mapped = gdf_mapped.rename(columns={"DISTRICT": "district"})
    if "index_right" in gdf_mapped.columns:
        gdf_mapped = gdf_mapped.drop(columns=["index_right"])

    gdf_mapped = gpd.sjoin(
        gdf_mapped, gdf_taluks[["TALUK", "geometry"]], how="left", predicate="within"
    )
    gdf_mapped = gdf_mapped.rename(columns={"TALUK": "taluk"})
    if "index_right" in gdf_mapped.columns:
        gdf_mapped = gdf_mapped.drop(columns=["index_right", "geometry"])

    gdf_mapped["district"] = gdf_mapped["district"].fillna("Outside Boundary")
    gdf_mapped["taluk"] = gdf_mapped["taluk"].fillna("Outside Boundary")

    # Now, unfold the time series to create the final data frame
    logging.info("Expanding spatial grid across time dimension...")
    final_rows = []

    for t_val in ds.time.values:
        # Convert numpy datetime64 to python timestamp
        obs_time = pd.to_datetime(t_val)

        # Get the slice for this time
        slice_da = utci_da.sel(time=t_val)

        for _, row in gdf_mapped.iterrows():
            lon = row["longitude"]
            lat = row["latitude"]
            val = float(slice_da.sel(lat=lat, lon=lon, method="nearest").values)

            if np.isnan(val):
                continue

            final_rows.append(
                {
                    "observation_timestamp": obs_time,
                    "longitude": lon,
                    "latitude": lat,
                    "utci_value": val,
                    "pixel_area_km2": row["pixel_area_km2"],
                    "district": row["district"],
                    "taluk": row["taluk"],
                }
            )

    df_final = pd.DataFrame(final_rows)
    logging.info("Generated %d rows of space-time data.", len(df_final))

    # 4. Ingest into PostgreSQL
    # First, ensure the target database exists by connecting to the default 'postgres' DB
    base_connection = (
        f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/postgres"
    )
    try:
        from sqlalchemy import text

        base_engine = create_engine(base_connection, isolation_level="AUTOCOMMIT")
        with base_engine.connect() as base_conn:
            exists = base_conn.execute(
                text(f"SELECT 1 FROM pg_database WHERE datname='{db_name}'")
            ).fetchone()
            if not exists:
                logging.info(
                    "Database '%s' does not exist. Creating database...", db_name
                )
                # PostgreSQL requires CREATE DATABASE to be run outside a transaction block (AUTOCOMMIT)
                base_conn.execute(text(f"CREATE DATABASE {db_name}"))
                logging.info("Database '%s' created successfully.", db_name)
    except Exception as e:
        logging.warning("Could not verify or create database '%s': %s", db_name, e)

    logging.info("Connecting to database %s at %s...", db_name, db_host)
    try:
        # Create connection engine
        engine = create_engine(connection_string)

        # Write to table 'utci_grid'
        df_final.to_sql("utci_grid", engine, if_exists="replace", index=False)
        logging.info(
            "Successfully ingested multi-day UTCI pixel grid into table 'utci_grid'!"
        )

        # Query confirmation
        with engine.connect() as conn:
            from sqlalchemy import text

            result = conn.execute(text("SELECT COUNT(*) FROM utci_grid")).fetchone()
            logging.info(
                "Verified: Ingested %d rows into PostgreSQL database.", result[0]
            )

    except Exception as e:
        logging.error("Failed to ingest data into PostgreSQL: %s", e)
        logging.error(
            "Please ensure PostgreSQL is running, the database '%s' exists, and credentials are correct.",
            db_name,
        )
        logging.error(
            "You can set connection details using: DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME environment variables."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
