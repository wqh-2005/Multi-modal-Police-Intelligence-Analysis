import argparse
from pathlib import Path
from main import *

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description='Model Controller')
parser.add_argument('--cuda_id', default="0", type=str)
parser.add_argument('--seed', default=2026, type=int)
parser.add_argument('--base_path', default=str(ROOT / 'output'), type=str)
parser.add_argument('--data_path', default=str(ROOT / 'datasets'), type=str)
parser.add_argument('--dataset', default='WebNLG_star', type=str)
parser.add_argument('--device', default='auto', choices=['auto', 'cuda', 'cpu'])
parser.add_argument('--train_samples', default=0, type=int, help='0 uses all training samples')
parser.add_argument('--eval_samples', default=0, type=int, help='0 uses all dev/test samples')
parser.add_argument('--bert_dir', default=str(ROOT / 'models' / 'bert-base-cased'))
parser.add_argument('--train', default="train", type=str)
parser.add_argument('--bert_learning_rate', default=3e-5, type=float)
parser.add_argument('--other_learning_rate', default=(3e-5)*5, type=float)
parser.add_argument('--num_train_epochs', default= 100, type=int)
parser.add_argument('--file_id', default='2', type=str)
parser.add_argument('--batch_size', default=2, type=int)
parser.add_argument('--max_len', default=100, type=int)
parser.add_argument('--warmup', default=0.0, type=float)
parser.add_argument('--weight_decay', default=0.0, type=float)
parser.add_argument('--max_grad_norm', default=1.0, type=float)
parser.add_argument('--min_num', default=1e-7, type=float)

# 路径更换成功
parser.add_argument('--bert_vocab_path', default=None, type=str)
parser.add_argument('--bert_config_path', default=None, type=str)
parser.add_argument('--bert_model_path', default=None, type=str)

args = parser.parse_args()
args.device = ('cuda' if torch.cuda.is_available() else 'cpu') if args.device == 'auto' else args.device
if args.device == 'cuda':
    if not torch.cuda.is_available():
        parser.error('CUDA is unavailable; use --device cpu')
    torch.cuda.set_device(int(args.cuda_id))
args.bert_vocab_path = args.bert_vocab_path or str(Path(args.bert_dir) / 'vocab.txt')
args.bert_config_path = args.bert_config_path or str(Path(args.bert_dir) / 'config.json')
# Load the entire local Hugging Face model directory, including safetensors.
args.bert_model_path = args.bert_model_path or args.bert_dir
if args.batch_size <= 0 or args.num_train_epochs <= 0 or args.train_samples < 0 or args.eval_samples < 0:
    parser.error('Batch size/epochs must be positive and sample limits nonnegative')
for filename in ('train.json', 'dev.json', 'test.json', 'rel2id.json'):
    if not (Path(args.data_path) / args.dataset / filename).is_file():
        parser.error(f'Missing dataset file: {Path(args.data_path) / args.dataset / filename}')
if not Path(args.bert_vocab_path).is_file() or not Path(args.bert_config_path).is_file():
    parser.error('Local BERT files missing. Run download_bert.py or pass --bert_dir first.')

if args.seed != 0:
    set_seed(args.seed)

if args.train == "train":
    train(args)
else:
    test(args)
