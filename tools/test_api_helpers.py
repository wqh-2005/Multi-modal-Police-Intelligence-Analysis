"""API 层辅助函数单元测试：_merge_texts / _check_deepfake / _input_stats。

运行: .venv/bin/python -m unittest tools.test_api_helpers -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.knowledge import _check_deepfake, _input_stats, _merge_texts
from app.models.knowledge_schema import BatchMultimodalResponse, OutputItem


def make_response(*items) -> BatchMultimodalResponse:
    return BatchMultimodalResponse(case_id="c1", outputs=list(items))


class TestMergeTexts(unittest.TestCase):
    def test_merge_done_outputs(self):
        data = make_response(
            OutputItem(type="text", text="第一条消息", status="done"),
            OutputItem(type="image", text="OCR识别结果", status="done"),
        )
        self.assertEqual(_merge_texts(data), "第一条消息\nOCR识别结果")

    def test_skip_error_status(self):
        data = make_response(
            OutputItem(type="text", text="有效", status="done"),
            OutputItem(type="audio", text="失败内容", status="error"),
        )
        self.assertEqual(_merge_texts(data), "有效")

    def test_skip_empty_placeholder(self):
        data = make_response(
            OutputItem(type="text", text="此文本为空", status="done"),
            OutputItem(type="image", text="有效OCR", status="done"),
        )
        self.assertEqual(_merge_texts(data), "有效OCR")

    def test_all_empty(self):
        data = make_response(
            OutputItem(type="text", text="此文本为空", status="done"),
        )
        self.assertEqual(_merge_texts(data), "")

    def test_success_status_compat(self):
        """早期数据集使用 status="success"，应兼容。"""
        data = make_response(
            OutputItem(type="text", text="兼容旧状态", status="success"),
        )
        self.assertEqual(_merge_texts(data), "兼容旧状态")


class TestCheckDeepfake(unittest.TestCase):
    def test_no_deepfake(self):
        data = make_response(
            OutputItem(type="video", deepfake_result=False, status="done"),
        )
        self.assertFalse(_check_deepfake(data))

    def test_has_deepfake(self):
        data = make_response(
            OutputItem(type="video", deepfake_result=True, status="done"),
        )
        self.assertTrue(_check_deepfake(data))

    def test_none_is_not_deepfake(self):
        data = make_response(
            OutputItem(type="text", deepfake_result=None, status="done"),
        )
        self.assertFalse(_check_deepfake(data))


class TestInputStats(unittest.TestCase):
    def test_stats(self):
        data = make_response(
            OutputItem(type="text", status="done"),
            OutputItem(type="image", status="success"),
            OutputItem(type="audio", status="error"),
        )
        stats = _input_stats(data)
        self.assertEqual(stats["total_outputs"], 3)
        # success_count 按 status == "success" 统计（非 "done"）
        self.assertEqual(stats["success_count"], 1)
        self.assertEqual(stats["error_count"], 1)
        self.assertEqual(set(stats["modality_types"]), {"text", "image", "audio"})


if __name__ == "__main__":
    unittest.main()
