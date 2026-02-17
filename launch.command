#!/bin/bash

# GLM-OCR Studio Launcher for macOS
# This script starts the MLX server and the Web UI.

# Get the directory of the script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "--------------------------------------------------"
echo "GLM-OCR Initialization"
echo "--------------------------------------------------"

# 0. Check for Git and Library
if [ ! -d "glm-ocr" ]; then
    echo "GLM-OCR not found. Checking for Git..."
    if ! command -v git &> /dev/null; then
        echo "Error: Git is not installed."
        echo "Please install Git or manually download the glm-ocr repo into this folder."
        exit 1
    fi
    echo "Cloning GLM-OCR library..."
    git clone https://github.com/zai-org/GLM-OCR glm-ocr
    if [ $? -ne 0 ]; then
        echo "Error: Failed to clone GLM-OCR library."
        exit 1
    fi
fi

# 1. Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 not found."
    echo "Please install Python from https://www.python.org/ or via Homebrew."
    exit 1
fi

# 2. Setup Virtual Environment if missing
if [ ! -d ".venv" ]; then
    echo "First run detected! Setting up virtual environment..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment."
        exit 1
    fi
    source .venv/bin/activate
    echo "Installing dependencies (this may take a few minutes)..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    # Activate existing virtual environment
    source .venv/bin/activate
    
    # 3. Sync dependencies if requirements.txt changed
    if [ "requirements.txt" -nt ".venv/bin/activate" ]; then
        echo "Updating dependencies..."
        pip install -r requirements.txt
    fi
fi

# 4. Ensure models are downloaded and verified
echo "Verifying model weights..."
python download_weights.py
if [ $? -ne 0 ]; then
    echo "Error: Model verification failed. Please check your internet connection."
    exit 1
fi

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
    MAX_RETRIES=60
    RETRY_COUNT=0
    # Use nc -z to check if the port is open (more reliable than curl for health checks)
    while ! nc -z localhost 8080 > /dev/null; do
        # Check if process is still alive
        if ! kill -0 $MLX_PID 2>/dev/null; then
            echo "Error: MLX Server process died unexpectedly."
            exit 1
        fi
        sleep 1
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
            echo "Error: MLX Server failed to start within 60 seconds."
            kill $MLX_PID 2>/dev/null
            exit 1
        fi
    done
    echo "MLX Server is ready."
fi

# 6. Start Flask App
echo "Starting Web Interface..."
python app.py &
APP_PID=$!

# 7. Wait for Server & Launch browser
echo "Waiting for Web Interface to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0
while ! curl -s http://localhost:5003 > /dev/null; do
    sleep 1
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "Error: Web Interface failed to start within 30 seconds."
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

# Capture termination signals
trap "kill $MLX_PID $APP_PID; echo 'Servers stopped.'; exit" INT TERM
wait
