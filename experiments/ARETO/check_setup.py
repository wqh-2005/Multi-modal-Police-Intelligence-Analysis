"""Check data, tokenizer and a GPU forward pass without training."""
import json
import argparse
from pathlib import Path
from types import SimpleNamespace
import torch
from transformers import BertConfig
from dataloader import data_generator
from model import ARETO
from tokenizer import Tokenizer
from util import set_seed

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dataset', default='WebNLG_star')
parser.add_argument('--bert_dir', default=str(root / 'models' / 'bert-base-cased'))
parser.add_argument('--max_len', type=int, default=100)
options = parser.parse_args()
data_dir = root / 'datasets' / options.dataset
bert_dir = Path(options.bert_dir)
device = 'cuda' if torch.cuda.is_available() else 'cpu'
set_seed(2026)
args = SimpleNamespace(batch_size=2, max_len=options.max_len)
data = json.loads((data_dir / 'train.json').read_text(encoding='utf-8'))
id2predicate, predicate2id = json.loads((data_dir / 'rel2id.json').read_text(encoding='utf-8'))
assert all(id2predicate[str(index)] == label for label, index in predicate2id.items())
tokenizer = Tokenizer(bert_dir / 'vocab.txt')
text = data[0]['text']
tokens = tokenizer.tokenize(text, max_length=args.max_len)
mapping = tokenizer.rematch(text, tokens)
assert len(tokens) == len(mapping)
batch = next(iter(data_generator(args, data[:8], tokenizer, predicate2id, id2predicate)))
batch = [torch.tensor(item, device=device) for item in batch]
ids, mask, label_ids, label_mask, s1, s2, o2, s3, o3, relations = batch
config = BertConfig.from_pretrained(str(bert_dir), local_files_only=True)
config.num_p = len(id2predicate)
model = ARETO.from_pretrained(str(bert_dir), config=config, local_files_only=True).to(device)
model.eval()
with torch.no_grad():
    predictions = model(ids, mask, label_ids, label_mask, s2, s3, o3)
assert [p.shape for p in predictions] == [s1.shape, o2.shape, relations.shape]
assert all(torch.isfinite(p).all().item() for p in predictions)
print(json.dumps({
    'status': 'PASS', 'device': device,
    'gpu': torch.cuda.get_device_name(0) if device == 'cuda' else None,
    'relations': len(id2predicate), 'batch_shape': list(ids.shape),
    'prediction_shapes': [list(p.shape) for p in predictions],
    'training_performed': False,
}, indent=2), flush=True)
