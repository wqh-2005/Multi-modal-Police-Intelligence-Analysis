"""回归测试：app/models/output 四个模型文件（队友提交版，适配其契约）。

覆盖：
- FraudJudgment（字段必填 + confidence_score 范围校验，llmclient 结构化输出契约）
- JudgmentResult / JudgmentError / SimilarCase / fallback（judger 契约）
- Alert / AlertTemplate（alertoutput 契约）

运行: .venv/bin/python -m unittest tools.test_output_models -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from app.models.output.alert import Alert
from app.models.output.alerttmplates import AlertTemplate
from app.models.output.judgeroutput import JudgmentError, JudgmentResult, SimilarCase
from app.models.output.llmoutput import FraudJudgment


class TestFraudJudgment(unittest.TestCase):
    def test_full_construction(self):
        j = FraudJudgment(
            is_fraud=True, fraud_type="冒充公检法类诈骗", confidence="高",
            confidence_score=0.95, reason="符合典型特征", warning="立即报警",
        )
        self.assertTrue(j.is_fraud)
        self.assertEqual(j.fraud_type, "冒充公检法类诈骗")
        self.assertEqual(j.confidence_score, 0.95)

    def test_fields_required(self):
        """队友版契约：所有字段必填（LLM 结构化输出必须完整返回）。"""
        with self.assertRaises(ValidationError):
            FraudJudgment()

    def test_confidence_score_range(self):
        with self.assertRaises(ValidationError):
            FraudJudgment(
                is_fraud=False, fraud_type="", confidence="低",
                confidence_score=1.5, reason="r", warning="w",
            )


class TestJudgmentResult(unittest.TestCase):
    def test_full_construction_matches_judger(self):
        """对齐 judger.py 构造参数。"""
        r = JudgmentResult(
            case_id="c", is_fraud=True, fraud_type="刷单类诈骗",
            confidence="高", confidence_score=0.9, reason="r",
            warning="w", deepfake_alert=True,
            similar_cases=[{"content": "x", "fraud_type": "y", "score": 0.7}],
            timestamp="2026-01-01T00:00:00",
        )
        self.assertTrue(r.deepfake_alert)
        self.assertEqual(len(r.similar_cases), 1)
        self.assertIsNone(r.error)

    def test_timestamp_required(self):
        with self.assertRaises(ValidationError):
            JudgmentResult(case_id="c")

    def test_fallback(self):
        r = JudgmentResult.fallback(case_id="c1", error_msg="LLM 超时", error_source="llm_client")
        self.assertEqual(r.case_id, "c1")
        self.assertFalse(r.is_fraud)
        self.assertIsNotNone(r.error)
        self.assertTrue(r.error.occurred)
        self.assertEqual(r.error.message, "LLM 超时")
        self.assertEqual(r.error.source, "llm_client")

    def test_error_field(self):
        r = JudgmentResult(
            case_id="c", timestamp="t",
            error=JudgmentError(occurred=True, source="rag_engine", message="boom"),
        )
        self.assertEqual(r.error.source, "rag_engine")
        self.assertEqual(r.error.message, "boom")


class TestJudgmentError(unittest.TestCase):
    def test_is_basemodel_with_defaults(self):
        """队友版契约：JudgmentError 是 pydantic 模型（非 Exception），字段有默认值。"""
        e = JudgmentError()
        self.assertFalse(e.occurred)
        self.assertEqual(e.source, "")
        self.assertEqual(e.message, "")


class TestSimilarCase(unittest.TestCase):
    def test_construction(self):
        s = SimilarCase(content="案例", fraud_type="刷单", score=0.8)
        self.assertEqual(s.fraud_type, "刷单")

    def test_score_range(self):
        with self.assertRaises(ValidationError):
            SimilarCase(content="x", fraud_type="y", score=1.2)


class TestAlert(unittest.TestCase):
    def test_fields_required(self):
        """队友版契约：type/level/title/message 必填，warning/reason 可选。"""
        with self.assertRaises(ValidationError):
            Alert()

    def test_alert_matches_alertoutput_construction(self):
        """对齐 alertoutput.py 的 Alert 构造参数。"""
        a = Alert(
            type="service_error", level="高", title="研判服务异常",
            message="服务暂不可用", warning="请人工介入", reason="error msg",
        )
        self.assertEqual(a.type, "service_error")
        self.assertEqual(a.model_dump()["reason"], "error msg")

    def test_warning_reason_optional(self):
        a = Alert(type="safe_notice", level="低", title="t", message="m")
        self.assertIsNone(a.warning)
        self.assertIsNone(a.reason)


class TestAlertTemplate(unittest.TestCase):
    def test_template_deepfake(self):
        a = AlertTemplate.deepfake_alert()
        self.assertEqual(a.type, "deepfake_warning")
        self.assertEqual(a.level, "高")

    def test_template_fraud(self):
        a = AlertTemplate.fraud_alert("冒充公检法类诈骗", "高", "立即报警", "依据")
        self.assertEqual(a.type, "fraud_warning")
        self.assertEqual(a.level, "高")
        self.assertEqual(a.title, "冒充公检法类诈骗")

    def test_template_safe(self):
        a = AlertTemplate.safe_alert()
        self.assertEqual(a.type, "safe_notice")
        self.assertEqual(a.level, "低")


if __name__ == "__main__":
    unittest.main()
