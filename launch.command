#!/bin/bash

# GLM-OCR Inference Launcher for macOS
# This script starts the MLX server and the Web UI.

# Get the directory of the script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "--------------------------------------------------"
echo "GLM-OCR Inference Initialization"
echo "--------------------------------------------------"

# 1. Check for Python 3.12+
if ! command -v python3 &> /dev/null || ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" &> /dev/null; then
    echo "Error: Python 3.12 or higher is required."
    echo "Please install the latest Python from https://www.python.org/downloads/macos/"
    exit 1
fi

# 2. Setup Virtual Environment if missing
if [ ! -d ".venv" ]; then
    echo "First run detected! Setting up virtual environment..."
    python3 -m venv .venv || { echo "Error: Failed to create virtual environment."; exit 1; }
    source .venv/bin/activate
    echo "Installing dependencies (this may take a few minutes)..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
else
    # Activate existing virtual environment
    source .venv/bin/activate
    
    # 3. Sync dependencies if requirements.txt changed
    if [ "requirements.txt" -nt ".venv/bin/activate" ]; then
        echo "Updating dependencies..."
        pip install -q -r requirements.txt
    fi
fi

# 4. Ensure models are downloaded and verified
echo "Verifying model weights..."
python utils/download_weights.py || { echo "Error: Model verification failed. Please check your internet connection."; exit 1; }

# 5. Start MLX Server
if lsof -Pi :8080 -sTCP:LISTEN -t >/dev/null ; then
    echo "MLX Server is already running on port 8080."
else
    echo "Starting MLX Server in background..."
    echo "Note: The first scan will be slow while weights load into unified memory."
    mlx_vlm.server --trust-remote-code &
    MLX_PID=$!
    
    # Wait for MLX server to be ready (Port Check)
    echo "Waiting for MLX Server to bind to port 8080..."
    MLX_MAX_RETRIES=60
    MLX_RETRY_COUNT=0
    # Use nc -z to check if the port is open (more reliable than curl for health checks)
    while ! nc -z localhost 8080 > /dev/null; do
        # Check if process is still alive
        if ! kill -0 $MLX_PID 2>/dev/null; then
            echo "Error: MLX Server process died unexpectedly."
            exit 1
        fi
        sleep 1
        MLX_RETRY_COUNT=$((MLX_RETRY_COUNT + 1))
        if [ $MLX_RETRY_COUNT -ge $MLX_MAX_RETRIES ]; then
            echo "Error: MLX Server failed to start within 60 seconds."
            kill $MLX_PID 2>/dev/null
            exit 1
        fi
    done
    echo "MLX Server is ready."
fi

# 6. Start Flask App
echo "Starting Web Interface..."
export GLM_CONFIG="$DIR/config/glm_config_mac.yaml"
python app.py &
APP_PID=$!

# 7. Wait for Server & Launch browser
echo "Waiting for Web Interface to be ready..."
APP_MAX_RETRIES=30
APP_RETRY_COUNT=0
while ! curl -s http://localhost:5003 > /dev/null; do
    sleep 1
    APP_RETRY_COUNT=$((APP_RETRY_COUNT + 1))
    if [ $APP_RETRY_COUNT -ge $APP_MAX_RETRIES ]; then
        echo "Error: Web Interface failed to start within 30 seconds."
        [ -n "$MLX_PID" ] && kill "$MLX_PID" 2>/dev/null
        kill "$APP_PID" 2>/dev/null
        exit 1
    fi
done

echo "Launching browser..."
open http://localhost:5003

echo "--------------------------------------------------"
echo "GLM-OCR is now active!"
echo "URL: http://localhost:5003"
echo "Outputs: $DIR/output"
echo "--------------------------------------------------"
echo "Keep this window open. Press Ctrl+C to stop everything."

# Capture termination signals (single quotes defer variable expansion to signal time)
trap 'kill $MLX_PID $APP_PID 2>/dev/null; echo "Servers stopped."; exit' INT TERM
wait
