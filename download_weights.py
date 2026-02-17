import os
import sys
from huggingface_hub import snapshot_download

def verify_weights(folder_name):
    """Checks if essential files exist in the model folder."""
    base_dir = os.path.join(os.getcwd(), "weights", folder_name)
    if not os.path.exists(base_dir):
        return False
    
    found_weights = any(f.endswith('.safetensors') or f.endswith('.bin') or f.endswith('.model') for f in os.listdir(base_dir))
    return found_weights

def download_weights():
    models = [
        ("mlx-community/GLM-OCR-bf16", "GLM-OCR-bf16"),
        ("PaddlePaddle/PP-DocLayoutV3_safetensors", "PP-DocLayoutV3_safetensors")
    ]
    
    os.makedirs("weights", exist_ok=True)
    
    for repo_id, folder_name in models:
        local_dir = os.path.join(os.getcwd(), "weights", folder_name)
        
        if verify_weights(folder_name):
            print(f"{folder_name} weights verified.")
            continue
            
        print(f"Downloading weights for {repo_id}...")
        print(f"Target: {local_dir}")
        
        try:
            snapshot_download(
                repo_id=repo_id,
                local_dir=local_dir,
                allow_patterns=[
                    "*.json",
                    "*.safetensors",
                    "*.py",
                    "*.model",
                    "*.tiktoken",
                    "*.txt",
                    "*.jinja",
                    "*.bin"
                ]
            )
        except Exception as e:
            print(f"Error downloading {repo_id}: {e}")
            sys.exit(1)
    
    print("\nAll model weights are ready!")

if __name__ == "__main__":
    download_weights()
