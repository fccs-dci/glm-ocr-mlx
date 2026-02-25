import os
import sys
import argparse

# Get project root (one level up from utils/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from huggingface_hub import snapshot_download
from utils.logger import setup_logging, get_logger

# Initialize logging
setup_logging()
logger = get_logger("weights")

def verify_weights(folder_name):
    """Checks if essential files exist in the model folder."""
    base_dir = os.path.join(PROJECT_ROOT, "weights", folder_name)
    if not os.path.exists(base_dir):
        return False
    found_weights = any(f.endswith('.safetensors') for f in os.listdir(base_dir))
    return found_weights

def download_weights(layout_only=False):
    models = [
        "PaddlePaddle/PP-DocLayoutV3_safetensors",
    ]
    if not layout_only:
        models.insert(0, "mlx-community/GLM-OCR-bf16")

    os.makedirs(os.path.join(PROJECT_ROOT, "weights"), exist_ok=True)

    for repo_id in models:
        folder_name = repo_id.split('/')[-1]
        local_dir = os.path.join(PROJECT_ROOT, "weights", folder_name)

        if verify_weights(folder_name):
            logger.info(f"{folder_name} weights verified.")
            continue

        logger.info(f"Downloading weights for {repo_id}...")
        logger.info(f"Target: {local_dir}")

        try:
            snapshot_download(repo_id=repo_id, local_dir=local_dir)
        except Exception as e:
            logger.error(f"Error downloading {repo_id}: {e}")
            sys.exit(1)

    logger.info("All model weights are ready!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download GLM-OCR model weights.")
    parser.add_argument(
        "--layout-only",
        action="store_true",
        help="Only download the PP-DocLayoutV3 layout model. Use this on Windows "
             "where Ollama manages the GLM-OCR model weights."
    )
    args = parser.parse_args()
    download_weights(layout_only=args.layout_only)

