"""Cased BERT WordPiece adapter for ARETO's three-value tokenizer API.

The public bert4keras Tokenizer.encode returns two values; ARETO expects
token IDs, segment IDs and attention masks. Use Transformers (already needed
by the model), with character offsets for evaluation instead of TensorFlow.
"""
from transformers import BertTokenizerFast
from pathlib import Path


class Tokenizer:
    def __init__(self, vocab_path):
        directory = Path(vocab_path).parent
        if (directory / 'tokenizer_config.json').is_file():
            self.backend = BertTokenizerFast.from_pretrained(str(directory), local_files_only=True)
        else:
            self.backend = BertTokenizerFast(vocab_file=str(vocab_path), do_lower_case=False)

    def _encode(self, text, max_length=None):
        options = {"return_offsets_mapping": True}
        if max_length is not None:
            options.update(truncation=True, max_length=max_length)
        return self.backend(text, **options)

    def encode(self, text, max_length=None):
        encoded = self._encode(text, max_length)
        return encoded["input_ids"], encoded["token_type_ids"], encoded["attention_mask"]

    def tokenize(self, text, max_length=None):
        return self._encode(text, max_length).tokens()

    def rematch(self, text, tokens):
        encoded = self._encode(text, len(tokens))
        if encoded.tokens() != tokens:
            raise ValueError("Token offsets do not match the supplied tokens")
        return [list(range(start, end)) for start, end in encoded["offset_mapping"]]
