# GLM-OCR Inference

> [!WARNING]
> **Ollama users:** Do NOT update Ollama if prompted. Version 0.17.5 is broken with GLM-OCR. Stay on 0.17.0 until further notice.

A local, high-fidelity OCR inference tool powered by GLM-4V. Supports macOS (M-series via MLX) and Windows (via Ollama).

## Quick Start

Double-click the launcher for your platform:
- **macOS**: `launch.command`
- **Windows**: `launch.bat`

This script will automatically:
1. Set up a Python virtual environment (`.venv`).
2. Download the necessary model weights.
3. Start the inference server and Web UI (default: http://localhost:5003).

> **Windows note:** Ollama is required and will be installed automatically if not present.

## Maintenance Scripts
- **`utils/deep_clean.command`**: Resets the project. Use this if you encounter startup issues or want to clear all processed data/weights.
- **`utils/download_weights.py`**: Manually verify or download the model weights from Hugging Face.

## Configuration
All advanced settings (timeouts, worker counts, layout detection) are managed in the `config/` directory:
- `config/glm_config_mac.yaml` — macOS (MLX / Apple Silicon)
- `config/glm_config_windows.yaml` — Windows (Ollama)

## Files & Directories
- `static/`: Frontend CSS, JS, and assets.
- `templates/`: HTML interface.
- `output/`: Processed OCR results.
- `weights/`: Local storage for AI models.
- `sessions/`: Job state and history.
