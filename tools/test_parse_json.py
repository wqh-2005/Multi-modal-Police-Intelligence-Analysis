"""回归测试：_parse_json LLM 输出解析容错（纯逻辑，不调 LLM）。

覆盖：标准 JSON、代码块包裹、前后附加文字、尾逗号、// 行注释、
单引号包裹、解析失败抛异常。

运行: .venv/bin/python -m unittest tools.test_parse_json -v
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.knowledge.extraction_service import _parse_json

VALID = {"triplets": [{"subject": "报警人", "relation": "转账", "object": "5000元"}],
         "confidence": 0.85}


class TestParseJson(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(_parse_json(json.dumps(VALID, ensure_ascii=False)), VALID)

    def test_code_block(self):
        self.assertEqual(_parse_json("```json\n" + json.dumps(VALID, ensure_ascii=False) + "\n```"), VALID)

    def test_plain_block(self):
        self.assertEqual(_parse_json("```\n" + json.dumps(VALID, ensure_ascii=False) + "\n```"), VALID)

    def test_leading_trailing_text(self):
        self.assertEqual(
            _parse_json("好的，以下是抽取结果：\n" + json.dumps(VALID, ensure_ascii=False) + "\n以上是全部内容。"),
            VALID)

    def test_trailing_comma(self):
        raw = '{"triplets": [{"subject": "报警人", "relation": "转账", "object": "5000元",}], "confidence": 0.85,}'
        self.assertEqual(_parse_json(raw), VALID)

    def test_line_comment(self):
        raw = ('{\n'
               '  "triplets": [\n'
               '    {"subject": "报警人", "relation": "转账", "object": "5000元"}\n'
               '  ],\n'
               '  // 置信度\n'
               '  "confidence": 0.85\n'
               '}')
        self.assertEqual(_parse_json(raw), VALID)

    def test_single_quotes(self):
        raw = "{'triplets': [{'subject': '报警人', 'relation': '转账', 'object': '5000元'}], 'confidence': 0.85}"
        self.assertEqual(_parse_json(raw), VALID)

    def test_comma_bracket_inside_string(self):
        # 字符串值内含 ",}" 的合法 JSON 不应被清洗逻辑破坏
        raw = ('{"triplets": [{"subject": "报警人", "relation": "备注,}", '
               '"object": "5000元"}], "confidence": 0.85}')
        self.assertEqual(_parse_json(raw),
                         {"triplets": [{"subject": "报警人", "relation": "备注,}",
                                        "object": "5000元"}],
                          "confidence": 0.85})

    def test_invalid_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            _parse_json("这不是 JSON")


if __name__ == "__main__":
    unittest.main(verbosity=2)
