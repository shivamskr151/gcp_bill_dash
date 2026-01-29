#!/bin/bash
# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
source venv/bin/activate

# Load environment variables from .env file
export $(grep -v '^#' .env | xargs)

# Run the Python script and log output
python3 send_gcp_billing_report.py >> email_report.log 2>&1
