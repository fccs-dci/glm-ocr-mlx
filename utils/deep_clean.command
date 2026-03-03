#!/bin/bash

# GLM-OCR Inference Deep Clean / Reset Utility
# This script allows you to selectively restore the project to a clean state.

# Get the directory of the script and its parent (project root)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( dirname "$DIR" )"
cd "$ROOT_DIR"

echo "--------------------------------------------------"
echo "GLM-OCR Inference System Reset: Deep Clean"
echo "--------------------------------------------------"
echo "Select which components you want to reset."
echo ""

# 1. Kill Stale Processes
read -p "Stop stale server processes (port 8080 & 5003)? (y/N): " clean_processes
if [[ "$clean_processes" =~ ^[Yy]$ ]]; then
    echo "Stopping processes..."
    # Kill MLX Server on 8080
    MLX_PIDS=$(lsof -t -i:8080)
    if [ ! -z "$MLX_PIDS" ]; then
        echo "Stopping stale MLX processes: $MLX_PIDS"
        kill -9 $MLX_PIDS 2>/dev/null
    fi
    # Kill Flask App on 5003
    APP_PIDS=$(lsof -t -i:5003)
    if [ ! -z "$APP_PIDS" ]; then
        echo "Stopping stale App processes: $APP_PIDS"
        kill -9 $APP_PIDS 2>/dev/null
    fi
fi

# 2. Remove Virtual Environment
echo ""
read -p "Remove Python virtual environment (.venv)? (y/N): " clean_venv
if [[ "$clean_venv" =~ ^[Yy]$ ]]; then
    echo "Removing .venv..."
    rm -rf .venv
fi

# 3. Clear Temporary Data
echo ""
read -p "Clear all uploads, sessions, and outputs? (y/N): " clean_data
if [[ "$clean_data" =~ ^[Yy]$ ]]; then
    echo "Cleaning data directories..."
    rm -rf static/uploads
    rm -rf sessions
    rm -rf output
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
fi

# 4. Weights Cleanup
echo ""
read -p "Delete AI model weights (20GB+)? (y/N): " clean_weights
if [[ "$clean_weights" =~ ^[Yy]$ ]]; then
    echo "Cleaning weights directory..."
    rm -rf weights/*
    echo "Weights deleted. You will need to redownload on next launch."
else
    echo "Preserved weights."
fi

echo ""
echo "--------------------------------------------------"
echo "CLEANUP COMPLETE."
echo "Run './launch.command' to resume."
echo "--------------------------------------------------"
read -p "Press Enter to exit..."
