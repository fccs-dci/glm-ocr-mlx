@echo off
setlocal EnableDelayedExpansion

:: GLM-OCR Inference Launcher for Windows

set "DIR=%~dp0"
cd /d "%DIR%"

echo --------------------------------------------------
echo GLM-OCR Inference Initialization
echo --------------------------------------------------

:: -------------------------------------------------------
:: 1. Check for Python 3.12+
:: -------------------------------------------------------
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed.
    echo Please install Python 3.12 or higher from https://www.python.org/downloads/
    pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python 3.12 or higher is required.
    echo Please install the latest Python from https://www.python.org/downloads/
    pause & exit /b 1
)

:: -------------------------------------------------------
:: 2. Install Ollama if not present (no UAC — installs to
::    %LOCALAPPDATA%, auto-starts as a tray service)
:: -------------------------------------------------------
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo Ollama is required to run GLM-OCR. It provides local AI inference
    echo and will be installed to %LOCALAPPDATA%\Programs\Ollama.
    echo.
    choice /c YN /m "Install Ollama now?"
    if !errorlevel! equ 2 (
        echo Installation cancelled. Please install Ollama manually from https://ollama.com/download
        pause & exit /b 1
    )
    echo Installing Ollama v0.17.0...
    powershell -Command "$env:OLLAMA_VERSION='0.17.0'; irm https://ollama.com/install.ps1 | iex"
    if !errorlevel! neq 0 (
        echo Error: Failed to install Ollama.
        echo Please install manually from https://ollama.com/download
        pause & exit /b 1
    )
    echo Ollama installed.
)
:: Ensure ollama is on PATH in this session (installer adds it, but current
:: CMD session won't see it until we refresh PATH manually).
set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"

:: -------------------------------------------------------
:: 3. Ensure Ollama service is running on port 11434
:: -------------------------------------------------------
netstat -ano | findstr ":11434" | findstr "LISTENING" >nul 2>&1
if %errorlevel% neq 0 (
    echo Starting Ollama service...
    start /B ollama serve

    echo Waiting for Ollama to bind to port 11434...
    set "OLLAMA_RETRIES=0"
    :ollama_wait
        timeout /t 1 /nobreak >nul
        netstat -ano | findstr ":11434" | findstr "LISTENING" >nul 2>&1
        if !errorlevel! equ 0 goto ollama_ready
        set /a OLLAMA_RETRIES+=1
        if !OLLAMA_RETRIES! lss 30 goto ollama_wait
    echo Error: Ollama failed to start within 30 seconds.
    pause & exit /b 1
) else (
    echo Ollama service is already running.
)
:ollama_ready

:: -------------------------------------------------------
:: 4. Pull GLM-OCR model if not already present
:: -------------------------------------------------------
echo Verifying GLM-OCR model...
ollama list 2>nul | findstr /I "glm-ocr" >nul 2>&1
if %errorlevel% neq 0 (
    echo Pulling glm-ocr:latest ^(first run - this may take several minutes^)...
    ollama pull glm-ocr:latest
    if !errorlevel! neq 0 (
        echo Error: Failed to pull GLM-OCR model. Check your internet connection.
        pause & exit /b 1
    )
)
echo GLM-OCR model is ready.

:: -------------------------------------------------------
:: 5. Setup Python virtual environment
:: -------------------------------------------------------
if not exist ".venv" (
    echo First run detected! Setting up virtual environment...
    python -m venv .venv
    if !errorlevel! neq 0 (
        echo Error: Failed to create virtual environment.
        pause & exit /b 1
    )
    call .venv\Scripts\activate.bat
    python -m pip install -q --upgrade pip
    echo Installing dependencies ^(this may take a few minutes^)...
    pip install -q -r requirements.txt
    if !errorlevel! neq 0 (
        echo Error: Failed to install dependencies.
        pause & exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat

    REM Re-sync if requirements.txt was updated since the venv was created
    for %%F in (requirements.txt) do (
        for %%V in (.venv\Scripts\activate.bat) do (
            if "%%~tF" gtr "%%~tV" (
                echo Updating dependencies...
                pip install -q -r requirements.txt
            )
        )
    )
)

:: -------------------------------------------------------
:: 6. Download layout model weights (PP-DocLayoutV3 only —
::    GLM-OCR weights are managed by Ollama)
:: -------------------------------------------------------
echo Verifying model weights...
python utils/download_weights.py --layout-only
if %errorlevel% neq 0 (
    echo Error: Failed to download model weights. Check your internet connection.
    pause & exit /b 1
)

:: -------------------------------------------------------
:: 7. Start Flask web interface
:: -------------------------------------------------------
echo Starting Web Interface...
set "GLM_CONFIG=%DIR%config\glm_config_windows.yaml"
start /B python app.py

:: -------------------------------------------------------
:: 8. Wait for Flask, then open browser
:: -------------------------------------------------------
echo Waiting for Web Interface to be ready...
set "APP_RETRIES=0"
:app_wait
    timeout /t 1 /nobreak >nul
    curl -s http://localhost:5003 >nul 2>&1
    if !errorlevel! equ 0 goto app_ready
    set /a APP_RETRIES+=1
    if !APP_RETRIES! lss 30 goto app_wait
echo Error: Web Interface failed to start within 30 seconds.
pause & exit /b 1

:app_ready
echo Launching browser...
start http://localhost:5003

echo --------------------------------------------------
echo GLM-OCR is now active!
echo URL:     http://localhost:5003
echo Outputs: %DIR%output
echo --------------------------------------------------
echo Close this window ^(or press Ctrl+C^) to stop GLM-OCR.
echo.
:keepalive
timeout /t 30 /nobreak >nul
goto keepalive
