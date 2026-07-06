import time
import schedule
import subprocess
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

def run_data_pipeline():
    logging.info("Triggering UTCI Data Acquisition Pipeline...")
    try:
        import sys
        from pathlib import Path
        script_dir = Path(__file__).parent.resolve()
        pipeline_script = script_dir / "run_pipeline.py"
        # Run the pipeline script using uv/python dynamically
        result = subprocess.run(
            [sys.executable, str(pipeline_script)],
            capture_output=True,
            text=True,
            check=True
        )
        logging.info("Pipeline execution completed successfully.")
        logging.debug("Pipeline output:\n%s", result.stdout)
    except subprocess.CalledProcessError as e:
        logging.error("Pipeline execution failed with exit code %d", e.returncode)
        logging.error("Pipeline error output:\n%s", e.stderr)
    except Exception as e:
        logging.error("An unexpected error occurred while running the pipeline: %s", e)

def main():
    logging.info("Starting UTCI Automated Pipeline Scheduler...")
    
    # Schedule the job to run daily at 1:45 PM and 10:45 PM
    # (Setting it 15 minutes past the hour to ensure Open-Meteo has generated the data)
    schedule.every().day.at("13:45").do(run_data_pipeline)
    schedule.every().day.at("22:45").do(run_data_pipeline)
    
    logging.info("Scheduled to run daily at 13:45 and 22:45 local time.")
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
