"""模块一（多模态）纯逻辑单元测试。

覆盖：tools.get_extension 后缀提取、service.py 的 case_id 安全校验正则
（不 import service.py，避免初始化 OpenAI/PaddleX 客户端）。

运行: .venv/bin/python -m unittest tools.test_multimodal -v
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.multimodal.tools import extract_base64_payload, get_extension

# 与 app/core/multimodal/service.py:82 保持一致（case_id 路径安全校验）
CASE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9一-龥\-:()\[\]{}\_.]+$")
CASE_ID_FORBIDDEN = {".", ".."}


def is_valid_case_id(case_id: str) -> bool:
    return bool(CASE_ID_PATTERN.match(case_id)) and case_id.strip() not in CASE_ID_FORBIDDEN


class TestGetExtension(unittest.TestCase):
    def test_data_uri_png(self):
        self.assertEqual(get_extension("image", "data:image/png;base64,xxx"), "png")

    def test_data_uri_jpeg(self):
        self.assertEqual(get_extension("image", "data:image/jpeg;base64,xxx"), "jpeg")

    def test_data_uri_mp4(self):
        self.assertEqual(get_extension("video", "data:video/mp4;base64,xxx"), "mp4")

    def test_data_uri_mp3(self):
        self.assertEqual(get_extension("audio", "data:audio/mpeg;base64,xxx"), "mpeg")

    def test_fallback_image(self):
        self.assertEqual(get_extension("image", "raw_binary_without_prefix"), "jpg")

    def test_fallback_video(self):
        self.assertEqual(get_extension("video", "raw"), "mp4")

    def test_fallback_audio(self):
        self.assertEqual(get_extension("audio", "raw"), "mp3")

    def test_fallback_text(self):
        self.assertEqual(get_extension("text", "hello"), "txt")

    def test_fallback_unknown(self):
        self.assertEqual(get_extension("unknown", "raw"), "bin")

    # ---- 升级后新增的边界场景 ----
    def test_data_uri_svg(self):
        """复合子类型 image/svg+xml 应取 svg（原实现会误回退 jpg）。"""
        self.assertEqual(get_extension("image", "data:image/svg+xml;base64,xxx"), "svg")

    def test_data_uri_uppercase(self):
        """mime 大小写不敏感，返回小写后缀。"""
        self.assertEqual(get_extension("image", "data:IMAGE/PNG;base64,xxx"), "png")
        self.assertEqual(get_extension("image", "data:Image/JPEG;base64,xxx"), "jpeg")

    def test_data_uri_no_comma(self):
        """载荷缺失（无逗号）仍能解析出后缀。"""
        self.assertEqual(get_extension("image", "data:image/webp;base64"), "webp")

    def test_data_uri_webp(self):
        self.assertEqual(get_extension("image", "data:image/webp;base64,xxx"), "webp")

    def test_data_uri_gif(self):
        self.assertEqual(get_extension("image", "data:image/gif;base64,xxx"), "gif")

    def test_data_uri_hyphen_subtype(self):
        """子类型含连字符（如 application/vnd.ms-excel）保留完整子类型。"""
        self.assertEqual(
            get_extension("file", "data:application/vnd.ms-excel;base64,xxx"),
            "vnd.ms-excel",
        )


class TestExtractBase64Payload(unittest.TestCase):
    def test_data_uri(self):
        self.assertEqual(
            extract_base64_payload("data:image/png;base64,aGVsbG8="), "aGVsbG8="
        )

    def test_data_uri_uppercase(self):
        """大写 data URI 前缀也应正确提取（与 get_extension 大小写行为一致）。"""
        self.assertEqual(
            extract_base64_payload("DATA:IMAGE/PNG;base64,aGVsbG8="), "aGVsbG8="
        )

    def test_pure_base64(self):
        self.assertEqual(extract_base64_payload("aGVsbG8="), "aGVsbG8=")

    def test_empty(self):
        self.assertEqual(extract_base64_payload(""), "")

    def test_data_uri_no_payload(self):
        self.assertEqual(extract_base64_payload("data:image/png;base64,"), "")

    def test_data_uri_with_extra_commas(self):
        """载荷本身含逗号不应截断（只按第一个逗号分割）。"""
        self.assertEqual(
            extract_base64_payload("data:text/plain;base64,YWJj,ZGVm"), "YWJj,ZGVm"
        )


class TestCaseIdValidation(unittest.TestCase):
    def test_valid_en(self):
        self.assertTrue(is_valid_case_id("case-001"))

    def test_valid_cn(self):
        self.assertTrue(is_valid_case_id("案件-2024-诈骗"))

    def test_valid_special(self):
        self.assertTrue(is_valid_case_id("test:case(v1)[stage-2]_final"))

    def test_reject_slash(self):
        self.assertFalse(is_valid_case_id("case/001"))

    def test_reject_backslash(self):
        self.assertFalse(is_valid_case_id("case\\001"))

    def test_reject_dot(self):
        self.assertFalse(is_valid_case_id("."))

    def test_reject_double_dot(self):
        self.assertFalse(is_valid_case_id(".."))

    def test_reject_semicolon(self):
        self.assertFalse(is_valid_case_id("case;rm"))

    def test_reject_space(self):
        self.assertFalse(is_valid_case_id("case 001"))

    def test_reject_empty(self):
        self.assertFalse(is_valid_case_id(""))

    def test_reject_newline(self):
        self.assertFalse(is_valid_case_id("case\n001"))


if __name__ == "__main__":
    unittest.main()
