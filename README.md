# GLM-OCR Inference

> [!WARNING]
> **Ollama users:** Do NOT update Ollama if prompted. Version 0.17.5 is broken with GLM-OCR. Stay on 0.17.0 until further notice.

A local, high-fidelity OCR inference tool powered by GLM-OCR. Supports macOS (M-series via MLX) and Windows (via Ollama).

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

**OCR-ing image and chart regions:**

By default, regions the layout detector labels as `image` or `chart` are skipped (saved as image crops). To OCR them as text instead, move them from `skip` to `text` in both config files:

```yaml
# Before
label_task_mapping:
  skip:
    - image
    - chart
label_visualization_mapping:
  image:
    - image
    - chart
```

```yaml
# After
label_task_mapping:
  skip: []
  text:
    - image
    - chart
    # ... (plus all other text labels)
label_visualization_mapping:
  image: []
  text:
    - image
    - chart
    # ... (plus all other text labels)
```

**Per-class detection thresholds:**

The layout detector uses a global threshold, but individual classes can be overridden. For example, to improve recall on `vertical_text` (class 23):

```yaml
# Default
layout:
  threshold: 0.3
```

```yaml
# With per-class override
layout:
  threshold: 0.3
  threshold_by_class:
    23: 0.2  # vertical_text
```

See `id2label` in the config for the full list of class indices.

## Third-Party Licenses
This project includes a modified distribution of [glm-ocr](https://github.com/zai-org/GLM-OCR) by Zhipu AI, licensed under the [Apache License 2.0](glm-ocr/LICENSE). CLI, server, tests, and documentation have been removed for distribution purposes. No source files were modified.

## Files & Directories
- `static/`: Frontend CSS, JS, and assets.
- `templates/`: HTML interface.
- `output/`: Processed OCR results.
- `weights/`: Local storage for AI models.
- `sessions/`: Job state and history.
