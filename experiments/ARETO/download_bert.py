"""Download official cased BERT once; training uses the local directory."""
import argparse
import os
from pathlib import Path

os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '60')
os.environ.setdefault('HF_HUB_ETAG_TIMEOUT', '30')
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
from huggingface_hub import snapshot_download

parser = argparse.ArgumentParser()
parser.add_argument('--local_dir', default=str(Path(__file__).resolve().parent / 'models' / 'bert-base-cased'))
args = parser.parse_args()
path = snapshot_download(
    repo_id='google-bert/bert-base-cased',
    local_dir=args.local_dir,
    allow_patterns=['config.json', 'vocab.txt', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors'],
    max_workers=2,
)
print(f'BERT saved to {path}', flush=True)
