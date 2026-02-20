import logging
import os
import sys
from typing import Optional
from glmocr.config import load_config

# Project root is one level up from this script (utils/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLM_CONFIG_PATH = os.path.join(PROJECT_ROOT, 'glm_config.yaml')

def setup_logging(level: Optional[str] = None, format_string: Optional[str] = None):
    """
    Master orchestrator for project-wide logging.
    Configures both the application (inference.*) and the SDK (glmocr.*).
    """
    # 1. Load configuration from YAML
    try:
        cfg = load_config(GLM_CONFIG_PATH)
        config_level = cfg.logging.level
        config_format = cfg.logging.format
    except Exception:
        # Fallback if config loading fails
        config_level = "INFO"
        config_format = None

    # Overrides
    target_level = level or config_level
    target_format = format_string or config_format or "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    
    level_value = getattr(logging, target_level.upper(), logging.INFO)

    # 2. Configure Root Logger
    # We clear handlers to ensure a fresh setup
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    root_logger.setLevel(level_value)
    
    handler = logging.StreamHandler(sys.stdout)
    # Match SDK format: [YYYY-MM-DD HH:MM:SS,mmm]
    formatter = logging.Formatter(target_format)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # 3. Explicitly configure our main namespaces
    # Inference (Application)
    inference_logger = logging.getLogger("inference")
    inference_logger.setLevel(level_value)
    inference_logger.propagate = True # Let it bubble up to root

    # 4. GLM-OCR SDK Sync
    try:
        from glmocr.utils.logging import configure_logging as sdk_configure
        sdk_configure(level=target_level, format_string=target_format)
    except ImportError:
        pass

    # 5. Silence noisy libraries
    noisy_loggers = [
        "httpx",
        "huggingface_hub",
        "urllib3",
        "huggingface_hub.utils._http",
        "werkzeug"
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    logging.info(f"Logging initialized (Level: {target_level})")

def get_logger(name: str) -> logging.Logger:
    """Returns a logger prefixed with 'inference.'"""
    if name.startswith("inference."):
        return logging.getLogger(name)
    return logging.getLogger(f"inference.{name}")
