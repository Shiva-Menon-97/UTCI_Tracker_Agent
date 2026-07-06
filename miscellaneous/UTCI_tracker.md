# UTCI Computation & Tracker Documentation

This document describes the computation pipeline for generating the Universal Thermal Climate Index (UTCI) spatial raster for Kerala, India, extracting zonal statistics, ingesting spatial grid points into PostgreSQL, and structuring the data for an MCP-based Text-to-SQL spatial query agent.

---

## 1. Pipeline Overview
The pipeline fetches high-resolution ECMWF IFS forecast data via the Open-Meteo API, estimates missing longwave radiation variables using physics-based empirical approximations, computes the Mean Radiant Temperature (MRT), and calculates the final UTCI using the ECMWF-developed `thermofeel` library.

The spatial output is saved as a GeoTIFF raster, which is then processed by a spatial join script to map every pixel to its corresponding Taluk/District and ingested into PostgreSQL to support standard SQL queries.

```
                    +------------------------------------+
                    |    Open-Meteo API (ECMWF IFS)      |
                    +-----------------+------------------+
                                      |
              [Temp, Dewpoint, Wind, Solar, Cloud Cover]
                                      v
                    +------------------------------------+
                    |    Longwave Radiation Estimations  |
                    |       (Crawford-Duchon 1999)       |
                    +-----------------+------------------+
                                      |
                       [NWP Radiation Variables]
                                      v
                    +------------------------------------+
                    |      thermofeel: MRT & UTCI        |
                    +-----------------+------------------+
                                      |
                       [UTCI Spatial Raster Array]
                                      v
                    +-----------------+------------------+
                    |    Save: NetCDF (.nc) & GeoTIFF    |
                    +-----------------+------------------+
                                      |
                             [GeoTIFF Raster]
                                      v
                    +-----------------+------------------+
                    |      ingest_to_postgres.py         |
                    | (Spatial Join and DB Ingestion)    |
                    +-----------------+------------------+
                                      |
                    +-----------------+------------------+
                    |      PostgreSQL Database           |
                    |        (utci_grid table)           |
                    +-----------------+------------------+
                                      |
                    +-----------------+------------------+
                    |     Agent Text-to-SQL Interface    |
                    |      (Via local MCP Server)        |
                    +------------------------------------+
```

---

## 2. Configuration Parameters

*   **Model Source:** ECMWF IFS (`ecmwf_ifs` on Open-Meteo)
*   **Spatial Resolution:** $0.1^\circ$ (approx. 11 km)
*   **Temporal Step:** Forecast Hour 8 (`FORECAST_STEP = 8`), representing ~13:30 IST (near peak afternoon heat stress)
*   **Region of Interest (Kerala Bounding Box):**
    *   **North Latitude:** $12.8^\circ\text{ N}$
    *   **South Latitude:** $8.2^\circ\text{ N}$
    *   **West Longitude:** $74.8^\circ\text{ E}$
    *   **East Longitude:** $77.5^\circ\text{ E}$
    *   **Grid Dimensions:** $47 \times 28$ (1,316 coordinates total)
*   **Robust Fetch Mechanism:** 
    *   Requests are split into batches of 200/300 locations.
    *   **Progress Caching:** Batches are cached to a local directory (`.utci_cache_{hash}`) to allow resuming runs from failure points.
    *   **Backoff:** Uses exponential backoff with jitter on API rate limits (HTTP 429).

---

## 3. Radiation & Emissivity Approximations

Since public JSON APIs (like Open-Meteo) do not expose downwelling and net longwave thermal radiation variables, we compute them dynamically using the **Stefan-Boltzmann** relationship with cloud and humidity corrections:

### A. Water Vapour Pressure ($e$)
We calculate the actual water vapour pressure in hectopascals (hPa) using relative humidity and temperature:
$$e = e_{sat}(T_{air}) \cdot \frac{RH}{100}$$

### B. Cloud-Adjusted Sky Emissivity ($\varepsilon_{sky}$)
Using the **Crawford and Duchon (1999)** correction:
1.  **Clear-sky Emissivity ($\varepsilon_{clear}$):**
    $$\varepsilon_{clear} = 1.24 \cdot \left(\frac{e}{T_{air}}\right)^{1/7}$$
2.  **Cloud-adjusted Emissivity ($\varepsilon_{sky}$):**
    $$\varepsilon_{sky} = (1 - s) \cdot \varepsilon_{clear} + s$$
    *where $s$ is the cloud cover fraction ($0.0 \le s \le 1.0$).*

### C. Downwelling Longwave Radiation (`strd`)
$$\text{strd} = \varepsilon_{sky} \cdot \sigma \cdot T_{air}^4$$
*where $\sigma = 5.670374 \times 10^{-8} \text{ W m}^{-2}\text{ K}^{-4}$ (Stefan-Boltzmann constant).*

### D. Net Longwave Radiation (`strr`)
$$\text{strr} = \text{strd} - \varepsilon_{surface} \cdot \sigma \cdot T_{air}^4$$
*where $\varepsilon_{surface}$ is assumed to be $0.97$ (typical land/vegetation emissivity).*

### E. Net Shortwave Radiation (`ssr`)
$$\text{ssr} = \text{ssrd} \cdot (1 - \alpha)$$
*where $\alpha = 0.20$ is the assumed land surface albedo for the tropical region of Kerala.*

---

## 4. MRT & UTCI Computations (`thermofeel`)

1.  **Mean Radiant Temperature (MRT):**
    Computed using `thermofeel.calculate_mean_radiant_temperature(...)` based on shortwave/longwave variables.
2.  **Relative Humidity and Vapour Pressure:**
    Computed using standard `thermofeel` utility functions.
3.  **UTCI Temperature:**
    Computed using `thermofeel.calculate_utci(t2_k, va, mrt, ehPa)` (returned in Kelvin and converted to Celsius). Ocean pixels are masked using a spatial land-mask filter to avoid false calculations.

---

## 5. Output Specifications

Spatial outputs are saved in WGS 84 (EPSG:4326) CRS coordinates:
*   **NetCDF (`utci_kerala.nc`):** Georeferenced using standard CF-compliant coordinates.
*   **GeoTIFF (`utci_kerala.tif`):** Standard GIS raster format for clean georeferencing, which automatically handles spatial grid mapping without coordinate array issues.

---

## 6. SQL-Based Zonal Statistics (Replacing Python Scripts)
Rather than executing Python scripts (like the legacy `zonal_stats.py` file) at query-time, all zonal statistics are calculated dynamically inside the database using standard SQL aggregation functions (`AVG`, `MIN`, `MAX`, `STDDEV`, `COUNT`, `SUM`).
*   **At Ingestion Time:** The database maps individual pixels to Districts and Taluks once during the ETL load.
*   **At Query Time:** The database dynamically computes stats for any region or threshold in under 1 millisecond.

---

## 7. Database Ingestion & Tabularization (`ingest_to_postgres.py`)

Because the spatial grid size of Kerala is small (~320-350 active pixels at $0.1^\circ$ resolution), we optimize the database for an LLM spatial agent by converting the raster array into a tabular SQL database schema using `ingest_to_postgres.py`.

### A. Spatial Joins & Mapping
Rather than forcing the LLM to write GIS scripts, the ingestion script:
1.  Extracts each valid pixel's coordinates (`longitude`, `latitude`) and `utci_value` from the GeoTIFF.
2.  Pre-calculates the exact area of the pixel (`pixel_area_km2`) adjusting for latitude compression ($\cos(\text{lat})$).
3.  Executes a spatial join in python (`geopandas.sjoin`) with District and Taluk boundaries.
4.  Ingests the data into a SQLite or PostgreSQL database table `utci_grid`.

### B. Table Schema: `utci_grid`
*   `longitude` (FLOAT): Pixel center longitude
*   `latitude` (FLOAT): Pixel center latitude
*   `utci_value` (FLOAT): Estimated UTCI value (Celsius)
*   `pixel_area_km2` (FLOAT): Approximate area of the pixel (approx. 121 sq. km)
*   `district` (TEXT): Name of the parent district
*   `taluk` (TEXT): Name of the parent taluk

---

## 8. Agent Query Architecture
By leveraging the tabularized `utci_grid` table and a local **MCP PostgreSQL server**, the spatial query agent operates on a **Text-to-SQL** design pattern. 

### A. Example Queries Translated by the Agent

*   **Query:** *"Which taluk in Thrissur district is experiencing the most intense thermal discomfort?"*
    ```sql
    SELECT taluk, max(utci_value) as max_utci 
    FROM utci_grid 
    WHERE district = 'Thrissur' 
    GROUP BY taluk 
    ORDER BY max_utci DESC 
    LIMIT 1;
    ```
*   **Query:** *"How many km2 of area in Kerala is experiencing UTCI values above 35 degrees celsius?"*
    ```sql
    SELECT SUM(pixel_area_km2) 
    FROM utci_grid 
    WHERE utci_value > 35.0;
    ```

### B. Why this Architecture is Recommended
*   **Robustness:** Eliminates ad-hoc script generation and execution risks (syntax errors, file system locks).
*   **Speed:** Simple database queries run in milliseconds compared to seconds-long raster math execution.
*   **Portability:** Works entirely offline using standard MCP tooling (e.g., SQLite or PostgreSQL running on localhost).
