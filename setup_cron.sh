#!/bin/bash
# Setup cron job for daily GCP billing email report at 9:30 AM

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "⏰ Setting up cron job for daily email report at 9:30 AM..."
echo ""

# Get the full path to the script
SCRIPT_PATH="$SCRIPT_DIR/send_gcp_billing_report.py"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"

# Check if virtual environment exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Virtual environment not found. Please run ./setup_email_report.sh first."
    exit 1
fi

# Check if .env file exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "❌ .env file not found. Please create it from .env.example"
    exit 1
fi

# Create a wrapper script that sets up the environment
WRAPPER_SCRIPT="$SCRIPT_DIR/run_email_report.sh"
cat > "$WRAPPER_SCRIPT" << 'WRAPPER_EOF'
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
WRAPPER_EOF

chmod +x "$WRAPPER_SCRIPT"

# Create cron entry (runs daily at 9:30 AM)
CRON_ENTRY="30 9 * * * $WRAPPER_SCRIPT"

# Check if cron entry already exists
if crontab -l 2>/dev/null | grep -q "$WRAPPER_SCRIPT"; then
    echo "⚠️  Cron job already exists. Removing old entry..."
    crontab -l 2>/dev/null | grep -v "$WRAPPER_SCRIPT" | crontab -
fi

# Add new cron entry
(crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -

echo "✅ Cron job installed successfully!"
echo ""
echo "Schedule: Daily at 9:30 AM"
echo "Script: $WRAPPER_SCRIPT"
echo ""
echo "To view your cron jobs:"
echo "  crontab -l"
echo ""
echo "To remove the cron job:"
echo "  crontab -l | grep -v '$WRAPPER_SCRIPT' | crontab -"
echo ""
echo "Logs will be written to: $SCRIPT_DIR/email_report.log"
