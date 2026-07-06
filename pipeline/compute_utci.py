"""Script to compute the Universal Thermal Climate Index (UTCI) using Open-Meteo.

This script retrieves forecast data (2m temperature, 2m dew point, 10m wind speed, and
surface radiation fluxes) from the Open-Meteo API (using the high-resolution ECMWF IFS 0.1° model),
computes the Mean Radiant Temperature (MRT) with Stefan-Boltzmann approximations for
longwave radiation variables, and calculates the UTCI using the 'thermofeel' library.
The output is saved as a CF-compliant NetCDF spatial raster.

Notes on radiation variable mapping (thermofeel / ECMWF convention, all W/m2 on a
horizontal surface unless stated):
    - ssrd  : global downward shortwave (GHI)          <- Open-Meteo shortwave_radiation
    - fdir  : DIRECT component of downward shortwave    <- Open-Meteo direct_radiation
              (direct-on-horizontal; ssrd - fdir = diffuse, must stay >= 0)
    - dsrp  : direct beam radiation from the sun (I*)   <- Open-Meteo direct_normal_irradiance (DNI)
Getting fdir and dsrp the right way round matters: thermofeel internally computes
diffuse shortwave as (ssrd - fdir). Feeding DNI into fdir makes this negative and
inflates MRT/UTCI.
"""

import hashlib
import json
import logging
import os
import random
import sys
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import xarray as xr
import rioxarray

# Fix PROJ_LIB conflict on Windows if PostGIS sets a system-wide path
os.environ.pop("PROJ_LIB", None)

import requests
import thermofeel as tf
from earthkit.meteo.solar.array import cos_solar_zenith_angle

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Constants for Region of Interest (Kerala Bounding Box)
LAT_NORTH = 12.8
LAT_SOUTH = 8.2
LON_WEST = 74.8
LON_EAST = 77.5
RESOLUTION = 0.1  # degrees (~11 km resolution)

# Forecast hour index. Open-Meteo hourly series with timezone=UTC starts at 00:00 UTC.
# IST = UTC + 5:30, so to target ~14:00 IST (near peak heat stress) use step 8 (08:00 UTC).
FORECAST_STEP = 8
SCRIPT_DIR = Path(__file__).parent.resolve()
OUTPUT_NC_FILE = str(SCRIPT_DIR / "utci_kerala.nc")

# Surface / physical assumptions
LAND_ALBEDO = 0.20  # tropical land surface albedo (Kerala)
SURFACE_EMISSIVITY = 0.97  # land/vegetation longwave emissivity
SIGMA = 5.670374419e-8  # Stefan-Boltzmann constant, W / (m2 K4)


def main() -> None:
    """Orchestrates data fetching, variable preprocessing, MRT & UTCI calculation."""
    logging.info("Starting Open-Meteo ECMWF UTCI Computation Script...")

    # 1. Define coordinate grid (descending latitude matches typical GRIB layouts)
    lats = np.arange(LAT_NORTH, LAT_SOUTH - 0.01, -RESOLUTION)
    lons = np.arange(LON_WEST, LON_EAST + 0.01, RESOLUTION)
    n_lat, n_lon = len(lats), len(lons)

    lon_grid, lat_grid = np.meshgrid(lons, lats)
    flat_lats = lat_grid.flatten()
    flat_lons = lon_grid.flatten()
    num_points = len(flat_lats)

    logging.info(
        "Generated grid: %d latitude steps, %d longitude steps (%d total points)",
        n_lat,
        n_lon,
        num_points,
    )

    # 2. Fetch Data from Open-Meteo with per-batch disk caching.
    #    Each successfully fetched batch is saved to a JSON file so that re-runs after
    #    a rate-limit failure resume from where they left off instead of restarting.
    #    The cache auto-invalidates when the grid configuration changes or the forecast
    #    date rolls over (Open-Meteo forecasts start from the current UTC date).
    batch_size = 200
    url = "https://api.open-meteo.com/v1/forecast"
    n_batches = int(np.ceil(num_points / batch_size))
    hourly_vars = (
        "temperature_2m,dew_point_2m,wind_speed_10m,shortwave_radiation,"
        "direct_radiation,direct_normal_irradiance,cloud_cover"
    )

    # --- Cache directory setup ---
    # Fingerprint = hash of the grid extents, resolution, and today's UTC date so the
    # cache is invalidated whenever any of these change.
    cache_key_src = f"{LAT_NORTH}_{LAT_SOUTH}_{LON_WEST}_{LON_EAST}_{RESOLUTION}_{date.today().isoformat()}_past7"
    cache_fingerprint = hashlib.md5(cache_key_src.encode()).hexdigest()[:10]
    cache_dir = Path(f".utci_cache_{cache_fingerprint}")
    cache_dir.mkdir(exist_ok=True)
    logging.info(
        "Batch cache directory: %s  (fingerprint: %s)", cache_dir, cache_fingerprint
    )

    # Clean up stale cache directories from previous days / configs
    for old_dir in Path(".").glob(".utci_cache_*"):
        if old_dir.is_dir() and old_dir != cache_dir:
            logging.info("Removing stale cache: %s", old_dir)
            for f in old_dir.iterdir():
                f.unlink()
            old_dir.rmdir()

    # --- Fetch loop with caching + exponential backoff ---
    records = []  # one dict (with "hourly") per grid point, in grid order
    max_retries = 8
    base_delay = 5.0  # seconds between successful batches
    initial_backoff = 30.0  # seconds for first 429 retry

    for b, i in enumerate(range(0, num_points, batch_size), start=1):
        cache_file = cache_dir / f"batch_{b:03d}.json"

        # Try loading from cache first
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                if isinstance(cached, dict):
                    records.append(cached)
                else:
                    records.extend(cached)
                logging.info("Batch %d/%d loaded from cache.", b, n_batches)
                continue
            except (json.JSONDecodeError, OSError):
                logging.warning("Corrupt cache for batch %d; re-fetching.", b)
                cache_file.unlink(missing_ok=True)

        batch_lats = flat_lats[i : i + batch_size]
        batch_lons = flat_lons[i : i + batch_size]

        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat in batch_lats),
            "longitude": ",".join(f"{lon:.4f}" for lon in batch_lons),
            "hourly": hourly_vars,
            "models": "ecmwf_ifs",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "past_days": 7,
        }

        logging.info(
            "Querying Open-Meteo for batch %d/%d (%d locations)...",
            b,
            n_batches,
            len(batch_lats),
        )

        success = False
        for attempt in range(1, max_retries + 1):
            try:
                res = requests.get(url, params=params, timeout=60)

                if res.status_code == 429:
                    # Exponential backoff: 30s, 60s, 120s, ... capped at 5 min, plus jitter
                    backoff = min(initial_backoff * (2 ** (attempt - 1)), 300.0)
                    jitter = random.uniform(0, backoff * 0.25)
                    wait = backoff + jitter
                    logging.warning(
                        "Rate limit hit (429). Backing off %.0fs (attempt %d/%d)...",
                        wait,
                        attempt,
                        max_retries,
                    )
                    time.sleep(wait)
                    continue

                if res.status_code != 200:
                    logging.error(
                        "Open-Meteo API error: %d - %s", res.status_code, res.text
                    )
                    sys.exit(1)

                batch_json = res.json()

                # Persist to cache before appending (crash-safe progress)
                cache_file.write_text(json.dumps(batch_json))

                if isinstance(batch_json, dict):
                    records.append(batch_json)
                else:
                    records.extend(batch_json)

                success = True
                logging.info("Batch %d/%d fetched and cached.", b, n_batches)

                # Polite inter-batch delay to stay under the per-minute ceiling
                if b < n_batches:
                    time.sleep(base_delay)
                break

            except requests.exceptions.Timeout:
                logging.warning(
                    "Request timed out (attempt %d/%d).", attempt, max_retries
                )
                time.sleep(base_delay)
            except Exception as e:
                logging.error(
                    "Request failed: %s (attempt %d/%d)", e, attempt, max_retries
                )
                time.sleep(base_delay)

        if not success:
            logging.error(
                "Failed to fetch batch %d after %d attempts. "
                "Re-run the script to resume from batch %d (batches 1-%d are cached).",
                b,
                max_retries,
                b,
                b - 1,
            )
            sys.exit(1)

    if len(records) != num_points:
        logging.error(
            "Expected %d point records but received %d; aborting.",
            num_points,
            len(records),
        )
        sys.exit(1)

    logging.info("All %d batches ready (%d point records).", n_batches, len(records))

    # 3. Extract and Prepare Variables
    logging.info(
        "Extracting data and performing variable conversions for 8 days (past 7 days + today)..."
    )
    try:
        # Get valid times from first record
        time_strs = records[0]["hourly"]["time"]

        # We extract 08:00 UTC (1:30 PM IST) and 17:00 UTC (10:30 PM IST) for each of the 8 days.
        num_days = 8
        target_indices_raw = [d * 24 + hr for d in range(num_days) for hr in [8, 17]]
        now_utc = datetime.utcnow()
        
        target_indices = []
        valid_times_dt = []
        for idx in target_indices_raw:
            dt = datetime.strptime(time_strs[idx], "%Y-%m-%dT%H:%M")
            # Only process timestamps that have actually occurred
            if dt <= now_utc:
                target_indices.append(idx)
                valid_times_dt.append(dt)

        # Array to hold the 3D UTCI output [time, lat, lon]
        utci_3d = np.zeros((len(target_indices), n_lat, n_lon))

        for t_idx, hour_idx in enumerate(target_indices):
            logging.info("Processing data for date: %s", valid_times_dt[t_idx].date())
            t2m_flat = np.zeros(num_points)
            d2m_flat = np.zeros(num_points)
            wind_flat = np.zeros(num_points)
            ssrd_flat = np.zeros(num_points)
            fdir_flat = np.zeros(num_points)
            dsrp_flat = np.zeros(num_points)
            cloud_flat = np.zeros(num_points)

            for idx, item in enumerate(records):
                hourly = item["hourly"]
                t2m_flat[idx] = hourly["temperature_2m"][hour_idx] + 273.15
                d2m_flat[idx] = hourly["dew_point_2m"][hour_idx] + 273.15
                wind_flat[idx] = hourly["wind_speed_10m"][hour_idx]
                ssrd_flat[idx] = hourly["shortwave_radiation"][hour_idx]
                fdir_flat[idx] = hourly["direct_radiation"][hour_idx]
                dsrp_flat[idx] = hourly["direct_normal_irradiance"][hour_idx]
                cloud_flat[idx] = hourly["cloud_cover"][hour_idx]

            # Reshape
            t2m = t2m_flat.reshape(n_lat, n_lon)
            d2m = d2m_flat.reshape(n_lat, n_lon)
            wind_speed_10m = wind_flat.reshape(n_lat, n_lon)
            ssrd = ssrd_flat.reshape(n_lat, n_lon)
            fdir = fdir_flat.reshape(n_lat, n_lon)
            dsrp = dsrp_flat.reshape(n_lat, n_lon)
            cloud_cover = cloud_flat.reshape(n_lat, n_lon)

            # Masking water
            water_mask = (
                np.isnan(t2m)
                | np.isnan(d2m)
                | np.isnan(wind_speed_10m)
                | np.isnan(ssrd)
                | np.isnan(fdir)
                | np.isnan(dsrp)
                | np.isnan(cloud_cover)
            )

            # Approximations
            rh_pc = tf.calculate_relative_humidity_percent(t2m, d2m)
            ehPa = tf.calculate_saturation_vapour_pressure(t2m) * rh_pc / 100.0
            epsilon_clear = 1.24 * (ehPa / t2m) ** (1.0 / 7.0)
            cloud_fraction = cloud_cover / 100.0
            epsilon_sky = (1.0 - cloud_fraction) * epsilon_clear + cloud_fraction
            strd = epsilon_sky * SIGMA * (t2m**4)
            strr = strd - SURFACE_EMISSIVITY * SIGMA * (t2m**4)
            ssr = ssrd * (1.0 - LAND_ALBEDO)

            # Solar Geometry
            cossza = cos_solar_zenith_angle(valid_times_dt[t_idx], lat_grid, lon_grid)
            cossza = np.asarray(cossza)

            # MRT & UTCI
            mrt_k = tf.calculate_mean_radiant_temperature(
                ssrd=ssrd,
                ssr=ssr,
                dsrp=dsrp,
                strd=strd,
                fdir=fdir,
                strr=strr,
                cossza=cossza,
            )
            utci_k = tf.calculate_utci(
                t2_k=t2m, va=wind_speed_10m, mrt=mrt_k, ehPa=ehPa
            )
            utci_c = utci_k - 273.15
            utci_c = np.where(water_mask, np.nan, utci_c)
            utci_3d[t_idx] = utci_c

        logging.info("Multi-day UTCI computation successful!")

        # Save as a single NetCDF with a time dimension
        ds_out = xr.Dataset(
            {"utci": (["time", "lat", "lon"], utci_3d)},
            coords={
                "time": valid_times_dt,
                "lat": lats,
                "lon": lons,
            },
        )
        ds_out.utci.attrs["units"] = "C"
        ds_out.utci.attrs["long_name"] = "Universal Thermal Climate Index"
        ds_out.utci.attrs["_FillValue"] = np.nan

        utci_da = ds_out.utci
        utci_da = utci_da.rio.set_spatial_dims(x_dim="lon", y_dim="lat")
        utci_da.rio.write_crs("epsg:4326", inplace=True)
        ds_out["utci"] = utci_da
        ds_out = ds_out.sortby("lat")
        ds_out.rio.write_grid_mapping(inplace=True)

        ds_out.to_netcdf(OUTPUT_NC_FILE)
        logging.info(
            "Successfully saved multi-day UTCI NetCDF raster to %s", OUTPUT_NC_FILE
        )

        # No GeoTIFF is saved for multi-day, because we now strictly use the NetCDF.

    except Exception as e:
        logging.error("Failed to calculate or save UTCI: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
