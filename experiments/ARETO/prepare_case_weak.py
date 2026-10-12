"""Build a reproducible, literal-span-only weak-label pilot dataset.

LLM labels are not human gold. Split whole documents (including exact text
duplicates) before windowing. Do not invent aliases or resolve coreference.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
from tokenizer import Tokenizer
from dataloader import search

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=2026)
    parser.add_argument('--max_len', type=int, default=256)
    parser.add_argument('--stride', type=int, default=128)
    parser.add_argument('--dataset', default='case_weak_v1')
    parser.add_argument('--bert_dir', default=str(ROOT / 'models' / 'chinese-macbert-base'))
    args = parser.parse_args()
    if not 0 < args.stride <= args.max_len - 2 <= 510 or args.limit <= 0:
        parser.error('Invalid window size, stride or limit')
    out = ROOT / 'datasets' / args.dataset
    if out.exists():
        parser.error(f'Dataset already exists: {out}; choose a different --dataset')
    raw_path = PROJECT / 'data' / 'raw' / '诈骗案例数据集_带编号.json'
    pred_path = PROJECT / 'app' / 'relationextraction' / 'output' / 'case' / 'triples.jsonl'
    schema_path = PROJECT / 'app' / 'relationextraction' / 'schemas' / '关系类型体系_v0.1_中文可读版.json'
    raw_bytes = raw_path.read_bytes()
    records = json.loads(raw_bytes.decode('utf-8-sig'))[:args.limit]
    documents = {}
    for i, row in enumerate(records, 1):
        sid = str(row.get('id') or row.get('source_id') or f'case_{i:06d}')
        if sid in documents:
            raise ValueError(f'Duplicate source_id: {sid}')
        documents[sid] = str(row.get('案情描述') or row.get('text') or '').strip()
    schema = json.loads(schema_path.read_text(encoding='utf-8-sig'))
    labels = [row['关系名称'] for row in schema['关系类型']]
    predicate2id = {name: i for i, name in enumerate(labels)}
    id2predicate = {str(i): name for name, i in predicate2id.items()}
    predictions = defaultdict(list)
    consumed_hash = hashlib.sha256()
    with pred_path.open('rb') as f:
        for line in f:
            consumed_hash.update(line)
            if line.strip():
                row = json.loads(line.decode('utf-8-sig'))
                if row['source_id'] in documents:
                    predictions[row['source_id']].append(row)
    groups = defaultdict(list)
    for sid, text in documents.items():
        digest = hashlib.sha256(re.sub(r'\s+', '', text).encode('utf-8')).hexdigest()
        groups[digest].append(sid)
    keys = sorted(groups)
    random.Random(args.seed).shuffle(keys)
    a, b = int(len(keys) * .8), int(len(keys) * .9)
    assignments = {}
    for split, subset in [('train', keys[:a]), ('dev', keys[a:b]), ('test', keys[b:])]:
        for key in subset:
            for sid in groups[key]:
                assignments[sid] = split
    tokenizer = Tokenizer(Path(args.bert_dir) / 'vocab.txt')
    # Full text is tokenized only to determine window boundaries, never fed
    # to BERT without windowing.
    tokenizer.backend.model_max_length = 10**9
    counts = Counter()
    factuality = Counter()
    prepared = {split: [] for split in ['train', 'dev', 'test']}
    observed = {split: Counter() for split in prepared}
    out.mkdir(parents=True)
    with (out / 'excluded_triples.jsonl').open('w', encoding='utf-8') as excluded:
        def reject(row, reason):
            counts['excluded_' + reason] += 1
            excluded.write(json.dumps({'source_id': row['source_id'],
                'triple_id': row.get('triple_id'), 'head': row['head']['name'],
                'relation': row['relation'], 'tail': row['tail']['name'],
                'reason': reason}, ensure_ascii=False) + '\n')

        for sid, text in documents.items():
            encoded = tokenizer.backend(text, add_special_tokens=False, return_offsets_mapping=True)
            offsets = encoded['offset_mapping']
            windows = []
            for start in range(0, len(offsets), args.stride):
                end = min(start + args.max_len - 2, len(offsets))
                left, right = offsets[start][0], offsets[end - 1][1]
                chunk = text[left:right]
                while len(tokenizer.encode(chunk)[0]) > args.max_len and end > start + 1:
                    end -= 1
                    right = offsets[end - 1][1]
                    chunk = text[left:right]
                ids = tokenizer.encode(chunk)[0]
                windows.append({'source_id': sid, 'window_id': f'{sid}_w{start:06d}',
                    'char_start': left, 'char_end': right, 'text': chunk,
                    'triple_list': [], '_ids': ids})
                if end == len(offsets):
                    break
            doc_retained = set()
            for row in predictions[sid]:
                counts['input_triples'] += 1
                factuality[row.get('factuality', '')] += 1
                s, p, o = row['head']['name'], row['relation'], row['tail']['name']
                if p not in predicate2id:
                    reject(row, 'unknown_relation'); continue
                if not s or not o or s not in text or o not in text:
                    reject(row, 'entity_not_literal'); continue
                s_ids, o_ids = tokenizer.encode(s)[0][1:-1], tokenizer.encode(o)[0][1:-1]
                assigned = False
                for window in windows:
                    # Also enforce the exact token matching used by dataloader.
                    if s in window['text'] and o in window['text'] and s_ids and o_ids and \
                            search(s_ids, window['_ids']) != -1 and search(o_ids, window['_ids']) != -1:
                        triple = [s, p, o]
                        if triple not in window['triple_list']:
                            window['triple_list'].append(triple)
                        assigned = True
                if assigned:
                    counts['retained_input_triples'] += 1
                    doc_retained.add((s, p, o))
                else:
                    reject(row, 'not_aligned_in_window')
            split = assignments[sid]
            if doc_retained:
                counts[f'{split}_documents_with_labels'] += 1
                for _, p, _ in doc_retained:
                    observed[split][p] += 1
            for window in windows:
                del window['_ids']
                if window['triple_list']:
                    window['label_source'] = 'LLM_unreviewed_literal_subset'
                    prepared[split].append(window)
                else:
                    counts['omitted_windows_without_retained_labels'] += 1
    for split, rows in prepared.items():
        if not rows:
            raise RuntimeError(f'{split} has no usable windows')
        save(out / f'{split}.json', rows)
    save(out / 'rel2id.json', [id2predicate, predicate2id])
    save(out / 'split_manifest.json', {
        'seed': args.seed, 'split_method': '80/10/10 of exact normalized-text groups',
        'splits': {split: sorted(sid for sid, value in assignments.items() if value == split)
                   for split in prepared},
    })
    report = {
        'label_source': 'LLM predictions, NOT human gold',
        'scope': 'positive windows with literal, token-alignable entities only',
        'evaluation_meaning': 'agreement with weak labels on a filtered window subset; not full-document accuracy',
        'source_documents': len(documents), 'exact_text_groups': len(groups),
        'raw_file_sha256': hashlib.sha256(raw_bytes).hexdigest(),
        'consumed_prediction_bytes_sha256': consumed_hash.hexdigest(),
        'bert_dir': args.bert_dir, 'max_len': args.max_len, 'stride': args.stride,
        'counts': dict(counts), 'input_factuality_counts': dict(factuality),
        'windows': {split: len(rows) for split, rows in prepared.items()},
        'window_triples': {split: sum(len(row['triple_list']) for row in rows) for split, rows in prepared.items()},
        'relation_counts_by_document': {split: dict(counter) for split, counter in observed.items()},
        'relations_absent_from_training': [label for label in labels if not observed['train'][label]],
        'notes': ['No coreference resolution or invented aliases.',
                  'Non-literal, cross-window and unalignable triples are reported, not used.',
                  'Overlapping-window triples can repeat; counts and F1 are window-level.',
                  'Factuality is retained in audit counts but not predicted by ARETO.',
                  'Negative/unlabelled windows are omitted, not declared gold negatives.'],
    }
    save(out / 'preparation_report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
