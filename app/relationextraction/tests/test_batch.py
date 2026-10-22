from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.relationextraction.code.llmclient import (
    EntityDraft,
    ExtractionCall,
    ExtractionResult,
    TripleDraft,
)
from app.relationextraction.code.llmuse import (
    build_indexes,
    cached_success,
    document_path,
    export_results,
    load_schemas,
    normalized_text_sha256,
    process_document,
)


class FakeClient:
    model_name = "mock-model"

    def __init__(
        self,
        source_id: str,
        fail_first: bool = False,
        always_fail: bool = False,
        chinese_labels: bool = False,
    ):
        self.source_id = source_id
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.chinese_labels = chinese_labels
        self.calls = 0

    async def extract(self, document: str, entity_schema: str, relation_schema: str) -> ExtractionCall:
        self.calls += 1
        if self.always_fail or (self.fail_first and self.calls == 1):
            raise RuntimeError("mock failure")
        result = ExtractionResult(
            source_id=self.source_id,
            triples=[
                TripleDraft(
                    head=EntityDraft(
                        name="A",
                        type="人员" if self.chinese_labels else "PERSON",
                        role="疑似诈骗者" if self.chinese_labels else "SUSPECT",
                    ),
                    relation="冒充" if self.chinese_labels else "IMPERSONATE",
                    tail=EntityDraft(
                        name="客服",
                        type="身份角色" if self.chinese_labels else "ROLE",
                        role=None,
                    ),
                    factuality="明确发生" if self.chinese_labels else "ASSERTED",
                    evidence_unit_ids=[f"{self.source_id}_turn_001"],
                    confidence=0.9,
                )
            ],
        )
        return ExtractionCall(
            result=result,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


def schema_bundle():
    entity_data, relation_data, entity_text, relation_text = load_schemas()
    indexes = build_indexes(entity_data, relation_data)
    return (entity_text, relation_text, *indexes)


def sample_document(source_id: str = "r_test") -> dict:
    return {
        "source_id": source_id,
        "source_type": "对话",
        "fraud_category": "测试",
        "source_units": [
            {
                "unit_id": f"{source_id}_turn_001",
                "speaker": "A",
                "text": "我是客服。",
            }
        ],
    }


TEST_RESOURCES = {
    "entity_schema_sha256": "test-entity",
    "relation_schema_sha256": "test-relation",
    "prompt_sha256": "test-prompt",
}


def process(client: FakeClient, document: dict, output: Path, retries: int = 1) -> str:
    return asyncio.run(
        process_document(
            document,
            output,
            client,
            schema_bundle(),
            retries,
            4096,
            TEST_RESOURCES,
            False,
        )
    )


class BatchTests(unittest.TestCase):
    def test_text_hash_ignores_lf_crlf_and_utf8_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lf = root / "lf.txt"
            crlf = root / "crlf.txt"
            bom = root / "bom.txt"
            lf.write_bytes("第一行\n第二行\n".encode("utf-8"))
            crlf.write_bytes("第一行\r\n第二行\r\n".encode("utf-8"))
            bom.write_bytes(b"\xef\xbb\xbf" + "第一行\r\n第二行\r\n".encode("utf-8"))
            self.assertEqual(normalized_text_sha256(lf), normalized_text_sha256(crlf))
            self.assertEqual(normalized_text_sha256(lf), normalized_text_sha256(bom))

    def test_retry_atomic_cache_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            document = sample_document()
            client = FakeClient(document["source_id"], fail_first=True)
            outcome = process(client, document, output, retries=3)
            self.assertEqual(outcome, "completed")
            self.assertEqual(client.calls, 2)
            result_file = document_path(output, "by_document", document)
            self.assertTrue(cached_success(result_file, document["source_id"]))
            self.assertFalse(result_file.with_suffix(".json.tmp").exists())
            second = process(client, document, output, retries=3)
            self.assertEqual(second, "skipped")

            failed_document = sample_document("r_failed")
            failed = process(
                FakeClient("r_failed", always_fail=True),
                failed_document,
                output,
                retries=2,
            )
            self.assertEqual(failed, "failed")
            failure_file = document_path(output, "failures", failed_document)
            self.assertEqual(json.loads(failure_file.read_text(encoding="utf-8"))["status"], "failed")

    def test_chinese_schema_labels_are_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            document = sample_document("r_chinese")
            outcome = process(
                FakeClient("r_chinese", chinese_labels=True), document, output
            )
            self.assertEqual(outcome, "completed")
            payload = json.loads(
                document_path(output, "by_document", document).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(len(payload["triples"]), 1)
            triple = payload["triples"][0]
            self.assertEqual(triple["head"]["type"], "人员")
            self.assertEqual(triple["head"]["role"], "疑似诈骗者")
            self.assertEqual(triple["relation"], "冒充")
            self.assertEqual(triple["tail"]["type"], "身份角色")
            self.assertEqual(triple["factuality"], "明确发生")

    def test_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dialogue = root / "dialogue.json"
            case = root / "case.json"
            output = root / "output"
            dialogue.write_text(
                json.dumps(
                    {"items": [{"id": "r_test", "dialogue": "A：我是客服。"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            case.write_text("[]", encoding="utf-8")
            document = sample_document()
            process(FakeClient("r_test"), document, output)
            args = argparse.Namespace(
                dialogue=str(dialogue),
                case=str(case),
                output=str(output),
                source_type="all",
                start_index=0,
                limit=None,
            )
            export_results(args)
            summary = json.loads(
                (output / "dialogue" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["success"], 1)
            self.assertEqual(summary["triples"], 1)
            self.assertEqual(
                len(
                    (output / "dialogue" / "triples.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
