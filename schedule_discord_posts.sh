#!/bin/bash
# Script to generate and post weather video to Discord
# This can be scheduled to run twice daily

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Activate virtual environment if it exists (optional)
# source venv/bin/activate

# Run the weather video script
python3 weather_video.py

# Log the execution
echo "$(date): Weather video generated and posted" >> "$SCRIPT_DIR/schedule.log"
