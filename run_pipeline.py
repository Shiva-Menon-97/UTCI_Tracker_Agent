import subprocess
import sys
import logging
from pathlib import Path

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

SCRIPT_DIR = Path(__file__).parent.resolve()

def run_pipeline():
    logging.info("Starting UTCI Data Acquisition Pipeline...")
    
    # 1. Run compute_utci.py
    logging.info("Running compute_utci.py to fetch and process NetCDF data...")
    try:
        subprocess.run([sys.executable, str(SCRIPT_DIR / "compute_utci.py")], check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"compute_utci.py failed with exit code {e.returncode}")
        sys.exit(1)
        
    # 2. Run ingest_to_postgres.py
    logging.info("Running ingest_to_postgres.py to upload to Postgres database...")
    try:
        subprocess.run([sys.executable, str(SCRIPT_DIR / "ingest_to_postgres.py")], check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"ingest_to_postgres.py failed with exit code {e.returncode}")
        sys.exit(1)
        
    logging.info("UTCI Data Acquisition Pipeline completed successfully!")

if __name__ == "__main__":
    run_pipeline()
