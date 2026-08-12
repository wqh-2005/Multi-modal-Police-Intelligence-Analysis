"""回归测试：Cypher 标识符白名单（防 prompt injection → Cypher 注入）。

运行: .venv/bin/python -m unittest tools.test_cypher_safety -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.knowledge.storage_service import _safe_cypher_identifier, _infer_entity_type


class TestCypherSafety(unittest.TestCase):
    def test_valid_chinese(self):
        self.assertEqual(_safe_cypher_identifier("转账", "OTHER"), "转账")
        self.assertEqual(_safe_cypher_identifier("要求转账", "OTHER"), "要求转账")
        self.assertEqual(_safe_cypher_identifier("PERSON", "OTHER"), "PERSON")

    def test_valid_alnum_underscore(self):
        self.assertEqual(_safe_cypher_identifier("PLATFORM", "OTHER"), "PLATFORM")
        self.assertEqual(_safe_cypher_identifier("app_v2", "OTHER"), "app_v2")

    def test_backtick_rejected(self):
        # 反引号可闭合标签做 Cypher 注入，必须拒绝
        malicious = "威胁`)-[:X]->(n) DETACH DELETE (n)`"
        self.assertIsNone(_safe_cypher_identifier(malicious, None))
        self.assertEqual(_safe_cypher_identifier(malicious, "OTHER"), "OTHER")

    def test_injection_fragments_rejected(self):
        for bad in ("n` DETACH", "`} SET n.x=1", ");DROP;", "a]-(n)"):
            self.assertIsNone(_safe_cypher_identifier(bad, None), bad)

    def test_empty_and_none_rejected(self):
        self.assertIsNone(_safe_cypher_identifier("", None))
        self.assertIsNone(_safe_cypher_identifier(None, None))

    def test_too_long_rejected(self):
        self.assertIsNone(_safe_cypher_identifier("中" * 21, None))
        self.assertEqual(_safe_cypher_identifier("中" * 20, "OTHER"), "中" * 20)

    def test_space_normalized_relation(self):
        # _create_relations 先 replace(" ", "_") 再校验
        rel = "要求 转账".replace(" ", "_")
        self.assertEqual(_safe_cypher_identifier(rel, None), "要求_转账")

    def test_infer_entity_type_url_prefix(self):
        # URL 为前缀语义：完整域名应识别为 URL，不回落到 OTHER
        self.assertEqual(_infer_entity_type("https://example.com/a?b=1"), "URL")
        self.assertEqual(_infer_entity_type("http://x.cn"), "URL")

    def test_infer_entity_type_phone_idcard(self):
        self.assertEqual(_infer_entity_type("13800138000"), "PHONE")
        self.assertEqual(_infer_entity_type("11010119900307721X"), "ID_CARD")
        self.assertEqual(_infer_entity_type("1380****8000"), "PHONE")
        self.assertEqual(_infer_entity_type("110101********0021"), "ID_CARD")
        self.assertEqual(_infer_entity_type("普通文本"), "OTHER")


if __name__ == "__main__":
    unittest.main(verbosity=2)
