# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.genai import types

import os
from pathlib import Path

# Load .env file manually
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.strip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip("'\"")

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"


def query_utci_database(sql_query: str) -> dict:
    """Executes a read-only SQL query on the local UTCI database and returns the results.

    Use this tool to fetch spatial and temporal UTCI data, compute statistics (AVG, MAX, SUM),
    and answer user queries about thermal comfort and heat stress in Kerala.

    Args:
        sql_query: A valid, read-only SELECT SQL query to execute.

    Returns:
        A dictionary containing the status of the query and the resulting rows or error message.
    """
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ.get("DB_PASSWORD", "postgres")
    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "utci-tracker-db")

    connection_string = (
        f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )

    clean_query = sql_query.strip().lower()
    if not clean_query.startswith("select"):
        return {"status": "error", "error": "Only SELECT queries are allowed."}

    try:
        from sqlalchemy import create_engine

        engine = create_engine(connection_string)
        with engine.connect() as conn:
            from sqlalchemy import text

            result = conn.execute(text(sql_query))
            rows = [dict(row._mapping) for row in result]
            # Convert dates to string for JSON compliance
            for row in rows:
                for k, v in row.items():
                    if isinstance(v, (datetime.date, datetime.datetime)):
                        row[k] = v.isoformat()
            return {"status": "success", "results": rows}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# MCP toolset for chart generation via mcp-server-chart
chart_toolset = MCPToolset(
    connection_params=StdioConnectionParams(
        server_params={
            "command": "npx",
            "args": ["-y", "@antv/mcp-server-chart"],
        }
    )
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-3.1-flash-lite",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are a UTCI (Universal Thermal Climate Index) tracking assistant for Kerala, India.
Your goal is to answer user queries about spatial and temporal thermal comfort and heat stress in Kerala using the local PostgreSQL database.

You have access to a table named `utci_grid` with the following schema:
- `observation_timestamp` (TIMESTAMP): The date and time of the observation in UTC. The database contains 8 days of data (the last 7 days + today), with two readings per day (08:00 UTC for 1:30 PM IST peak heat, and 17:00 UTC for 10:30 PM IST night cooling).
- `longitude` (DOUBLE PRECISION): Longitude of the pixel center.
- `latitude` (DOUBLE PRECISION): Latitude of the pixel center.
- `utci_value` (DOUBLE PRECISION): Universal Thermal Climate Index value in Celsius.
- `pixel_area_km2` (DOUBLE PRECISION): Surface area of the pixel in square kilometers (approx. 120-121 km2).
- `district` (TEXT): Name of the parent district in Kerala (e.g. 'Thrissur', 'Ernakulam', 'Thiruvananthapuram', 'Palakkad', etc. If outside the state boundary, it is 'Outside Boundary').
- `taluk` (TEXT): Name of the parent taluk in Kerala (or 'Outside Boundary').

UTCI Heat Stress Categories (Celsius):
- Above 46: Extreme heat stress
- 38 to 46: Very strong heat stress
- 32 to 38: Strong heat stress
- 26 to 32: Moderate heat stress
- 9 to 26: No thermal stress (Comfort)
- Below 9: Cold stress categories

Rules for generating SQL queries:
1. Always write standard read-only SELECT queries. Never write write-queries (INSERT, UPDATE, DELETE, CREATE, DROP).
2. To find the most recent observations, query `(SELECT MAX(observation_timestamp) FROM utci_grid)`. E.g. `WHERE observation_timestamp = (SELECT MAX(observation_timestamp) FROM utci_grid)`. To get both afternoon and night readings for the most recent day, query using `DATE(observation_timestamp)`.
3. To calculate the total area (in km2) experiencing a condition, use `SUM(pixel_area_km2)`.
4. When comparing district or taluk names, use exact matches or `LIKE` with case-insensitivity if unsure. Example district names: 'Thrissur', 'Ernakulam', 'Thiruvananthapuram', 'Palakkad', 'Kozhikode', 'Kollam', 'Malappuram', 'Kannur', 'Alappuzha', 'Kottayam', 'Idukki', 'Wayanad', 'Pathanamthitta', 'Kasaragod'.
5. When presenting results, format dates clearly, convert UTCI values to one decimal place, and translate numerical heat stress values into their corresponding descriptive categories. Present area figures in square kilometers rounded to one decimal place.

Visualization Instructions:
If the user explicitly asks for a chart, graph, plot, or visualization:
1. First, call `query_utci_database` to fetch the relevant data rows needed for the chart.
2. Once the data is returned successfully, transform the rows into the data format required by the appropriate chart tool and call it directly:
   - Use `generate_line_chart` for UTCI trends over time (data format: `[{time, value, group?}]`).
   - Use `generate_column_chart` for comparing UTCI values across taluks or districts on a single date (data format: `[{category, value, group?}]`).
   - Use `generate_bar_chart` for horizontal comparisons across many categories (data format: `[{category, value, group?}]`).
   - Use `generate_scatter_chart` for correlations between two numeric variables (data format: `[{x, y, group?}]`).
3. Always set a descriptive `title` and appropriate `axisXTitle` / `axisYTitle` on the chart.
4. Use the `"dark"` theme for all charts.
5. Keep your data clean! Do NOT append the value to the category or time string (e.g. use "Thrissur", not "Thrissur (34)"). Keep the numbers only on the axes; do not attempt to force data labels onto the lines or bars themselves.
6. CRITICAL: When the chart tool returns the generated image URL, you MUST embed it in your final response using strict Markdown image syntax: `![Chart Description](https://...)`. If you only output the URL text, the frontend will fail to render the image.
7. CRITICAL: If the user asks for a table or spreadsheet, DO NOT use any chart tools (e.g., do not call generate_spreadsheet). You must simply output a standard Markdown table in your text response.
8. Do NOT delegate to any sub-agent for visualization. Call the chart tool directly yourself.
""",
    tools=[query_utci_database, chart_toolset],
)

app = App(
    root_agent=root_agent,
    name="app",
)
