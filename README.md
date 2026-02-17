# GLM-OCR Inference

A local, high-fidelity OCR inference tool powered by GLM-4V and MLX. Designed for macOS (M-series) with zero-config setup.

## Quick Start
To launch the application, double-click:
- `launch.command`

This script will automatically:ss
1. Clone the required `glm-ocr` library.
2. Set up a Python virtual environment (`.venv`).
3. Download the necessary model weights.
4. Start the MLX server and Web UI (default: http://localhost:5003).

## Maintenance Scripts
- **`utils/deep_clean.command`**: An utility to reset the project. Use this if you encounter startup issues or want to clear all processed data/weights.
- **`utils/download_weights.py`**: Manually verify or download the model weights from Hugging Face.

## Configuration
All advanced settings (timeouts, worker counts, layout detection) are managed in:
- `glm_config.yaml`

## Files & Directories
- `static/`: Frontend CSS, JS, and assets.
- `templates/`: HTML interface.
- `output/`: Processed OCR results, including raw SDK outputs and UI-optimized packages.
- `weights/`: Local storage for AI models.
- `sessions/`: Job state and history management.