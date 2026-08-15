"""storage_service.py 其余纯逻辑函数测试（模块三）。

覆盖：_infer_entity_type / _is_person_candidate / _is_likely_person /
_is_suspect_like / _derive_entities / _build_type_map / _build_relations /
_is_transfer_relation / _truncate_text / _infer_payee / _build_transactions。

运行: .venv/bin/python -m unittest tools.test_storage_extra -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.knowledge.storage_service import (
    _build_relations,
    _build_transactions,
    _build_type_map,
    _derive_entities,
    _infer_entity_type,
    _infer_payee,
    _is_likely_person,
    _is_person_candidate,
    _is_suspect_like,
    _is_transfer_relation,
    _truncate_text,
)
from app.models.knowledge_schema import Triplet


def T(subject, relation, obj, st=None, ot=None):
    return Triplet(subject=subject, relation=relation, object=obj,
                   subject_type=st, object_type=ot)


class TestInferEntityType(unittest.TestCase):
    def test_phone(self):
        self.assertEqual(_infer_entity_type("13800138000"), "PHONE")

    def test_phone_masked(self):
        self.assertEqual(_infer_entity_type("1380****8000"), "PHONE")

    def test_id_card(self):
        self.assertEqual(_infer_entity_type("11010119900307721X"), "ID_CARD")

    def test_id_card_masked(self):
        self.assertEqual(_infer_entity_type("110101********0721"), "ID_CARD")

    def test_url_https(self):
        self.assertEqual(_infer_entity_type("https://example.com"), "URL")

    def test_url_http(self):
        self.assertEqual(_infer_entity_type("http://x.cn/a"), "URL")

    def test_other(self):
        self.assertEqual(_infer_entity_type("张三"), "OTHER")


class TestPersonFilters(unittest.TestCase):
    def test_normal_person(self):
        self.assertTrue(_is_person_candidate("张三"))

    def test_non_person_word(self):
        self.assertFalse(_is_person_candidate("验证码"))

    def test_too_long(self):
        self.assertFalse(_is_person_candidate("超长的人名字符串啊啊"))

    def test_likely_person_with_suffix(self):
        self.assertTrue(_is_likely_person("王先生"))

    def test_likely_person_role(self):
        self.assertTrue(_is_likely_person("报警人"))

    def test_likely_false(self):
        self.assertFalse(_is_likely_person("验证码"))

    def test_suspect_like_kefu(self):
        self.assertTrue(_is_suspect_like("客服人员"))

    def test_suspect_like_duifang(self):
        self.assertTrue(_is_suspect_like("对方"))

    def test_suspect_like_long_role(self):
        self.assertTrue(_is_suspect_like("APP内客服人员"))

    def test_suspect_like_too_long(self):
        self.assertFalse(_is_suspect_like("这是一个超过十二个字的超长角色名称"))


class TestDeriveEntities(unittest.TestCase):
    def test_basic(self):
        t = [T("张三", "转账", "5000元", "PERSON", "AMOUNT")]
        entities = _derive_entities(t)
        names = {e["name"] for e in entities}
        self.assertIn("张三", names)
        self.assertIn("5000元", names)
        by_name = {e["name"]: e["type"] for e in entities}
        self.assertEqual(by_name["张三"], "PERSON")
        self.assertEqual(by_name["5000元"], "AMOUNT")


class TestBuildTypeMap(unittest.TestCase):
    def test_map(self):
        t = [T("张三", "转账", "5000元", "PERSON", "AMOUNT")]
        m = _build_type_map(t)
        self.assertEqual(m["张三"], "PERSON")
        self.assertEqual(m["5000元"], "AMOUNT")


class TestBuildRelations(unittest.TestCase):
    def test_build(self):
        t = [T("张三", "转账", "5000元")]
        rels = _build_relations(t)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0].from_entity, "张三")
        self.assertEqual(rels[0].type, "转账")
        self.assertEqual(rels[0].to_entity, "5000元")


class TestIsTransferRelation(unittest.TestCase):
    def test_transfer(self):
        self.assertTrue(_is_transfer_relation("转账"))

    def test_recharge(self):
        self.assertTrue(_is_transfer_relation("充值"))

    def test_require_not_transfer(self):
        self.assertFalse(_is_transfer_relation("要求转账"))

    def test_normal_relation(self):
        self.assertFalse(_is_transfer_relation("冒充"))


class TestTruncateText(unittest.TestCase):
    def test_short_text(self):
        self.assertEqual(_truncate_text("短文本", max_len=100), "短文本")

    def test_long_text_truncated(self):
        long_text = "这是测试。" * 200
        self.assertLessEqual(len(_truncate_text(long_text, max_len=200)), 200)

    def test_default_max_len(self):
        long_text = "这是测试。" * 300
        self.assertLessEqual(len(_truncate_text(long_text)), 800)


class TestInferPayee(unittest.TestCase):
    def test_demand_relation(self):
        t = [T("对方", "要求转账", "报警人")]
        self.assertEqual(_infer_payee(t, "报警人"), "对方")

    def test_unknown(self):
        t = [T("报警人", "转账", "5000元")]
        self.assertEqual(_infer_payee(t, "报警人"), "未知收款方")


class TestBuildTransactions(unittest.TestCase):
    def test_transfer_with_amount(self):
        t = [T("报案人", "转账", "66000元")]
        txns = _build_transactions(t)
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0].from_entity, "报案人")
        self.assertEqual(txns[0].amount, 66000.0)

    def test_transfer_with_wan_unit(self):
        t = [T("报案人", "转账", "5万元")]
        txns = _build_transactions(t)
        self.assertEqual(txns[0].amount, 50000.0)

    def test_non_transfer_skipped(self):
        t = [T("案犯甲", "冒充", "民警")]
        txns = _build_transactions(t)
        self.assertEqual(len(txns), 0)


if __name__ == "__main__":
    unittest.main()
