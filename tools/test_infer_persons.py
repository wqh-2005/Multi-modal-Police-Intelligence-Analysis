"""回归测试：_infer_persons 受害者/嫌疑人推断（纯逻辑，不调 LLM）。

覆盖：投资理财、冒充公检法（含 deepfake）、多人合谋、刷单、双人对话、
第三人称叙述、空输入、仅转账、长角色名等场景，并验证确定性。

运行: .venv/bin/python -m unittest tools.test_infer_persons -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.knowledge_schema import Triplet
from app.core.knowledge.storage_service import _infer_persons, _build_transactions


def T(subject, relation, obj, st="PERSON", ot="PERSON"):
    return Triplet(subject=subject, relation=relation, object=obj,
                   subject_type=st, object_type=ot)


CASES = [
    # (名称, 三元组列表, 期望 victim, 期望 suspect)
    ("manual_r0-投资理财",
     [T("李先生", "扫码", "二维码", ot="OTHER"),
      T("李先生", "加入", "投资-特训营", ot="PLATFORM"),
      T("李先生", "添加", "客服人员"),
      T("客服人员", "冒充", "基金经理"),
      T("客服人员", "发送", "安装包", ot="OTHER"),
      T("李先生", "下载", "投资平台软件", ot="PLATFORM"),
      T("客服人员", "诱导", "李先生"),
      T("李先生", "转账", "平台", ot="PLATFORM")],
     "李先生", "客服人员"),

    ("edge_deepfake-冒充公检法",
     [T("对方", "冒充", "民警"),
      T("对方", "要求转账", "报警人"),
      T("报警人", "转账", "5万元", ot="AMOUNT")],
     "报警人", "对方"),

    ("edge_multiperson-多人合谋",
     [T("张某", "合谋", "李某"),
      T("张某", "合谋", "王某"),
      T("李某", "合谋", "王某"),
      T("张某、李某、王某三人", "骗取", "赵某"),
      T("赵某", "转账", "50万元", ot="AMOUNT")],
     "赵某", "张某"),

    ("edge_short2-单句刷单",
     [T("受害人", "转账", "3000元", ot="AMOUNT"),
      T("骗子", "要求转账", "受害人")],
     "受害人", "骗子"),

    ("fewshot_对话-双人冒充公检法",
     [T("A", "冒充", "XX公安局", ot="ORGANIZATION"),
      T("A", "威胁", "B"),
      T("A", "要求下载", "B"),
      T("B", "质疑", "A"),
      T("B", "下载", "安全核查APP", ot="PLATFORM"),
      T("A", "要求填写", "B"),
      T("B", "填写", "验证码", ot="OTHER")],
     "B", "A"),

    ("fewshot_叙述1-投资电影",
     [T("报案人", "添加", "案犯甲"),
      T("案犯甲", "冒充", "股票专家"),
      T("案犯甲", "拉入群聊", "报案人"),
      T("案犯甲", "诱导", "报案人"),
      T("报案人", "转账", "66000元", ot="AMOUNT")],
     "报案人", "案犯甲"),

    ("fewshot_叙述2-刷单返利",
     [T("受害人", "添加", "支付宝好友", ot="ACCOUNT"),
      T("对方", "拉入群聊", "受害人"),
      T("受害人", "下载", "乐橙APP", ot="PLATFORM"),
      T("对方", "要求转账", "受害人"),
      T("受害人", "转账", "56946元", ot="AMOUNT")],
     "受害人", "对方"),

    ("empty-空输入",
     [], "未知", "未知"),

    ("only_transfer-仅转账",
     [T("报警人", "转账", "5000元", ot="AMOUNT")],
     "报警人", "未知"),

    ("long_suspect_name-长角色名",
     [T("报警人", "点击", "交友网址链接", ot="URL"),
      T("报警人", "下载", "幸福APP", ot="PLATFORM"),
      T("APP内客服人员", "要求", "报警人"),
      T("APP内客服人员", "要求转账", "报警人"),
      T("报警人", "转账", "10410元", ot="AMOUNT")],
     "报警人", "APP内客服人员"),
]


class TestInferPersons(unittest.TestCase):
    def test_cases(self):
        for name, triplets, ev, es in CASES:
            with self.subTest(case=name):
                v, s = _infer_persons(triplets)
                self.assertEqual(v.name, ev, f"{name} victim")
                self.assertEqual(s.name, es, f"{name} suspect")

    def test_deterministic(self):
        for name, triplets, ev, es in CASES:
            with self.subTest(case=name):
                first = tuple((v.name, s.name)
                              for v, s in [_infer_persons(triplets)])
                for _ in range(2):
                    again = tuple((v.name, s.name)
                                  for v, s in [_infer_persons(triplets)])
                    self.assertEqual(again, first, f"{name} 非确定性")

    def test_transaction_amount_wan_unit(self):
        # "5万元" 应换算为 50000 元（Transaction.amount 单位是元）
        triplets = [T("报警人", "转账", "5万元", ot="AMOUNT"),
                    T("对方", "要求转账", "报警人")]
        tx = _build_transactions(triplets)
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx[0].amount, 50000.0)
        self.assertEqual(tx[0].from_entity, "报警人")
        self.assertEqual(tx[0].to_entity, "对方")

    def test_transaction_amount_plain(self):
        # 纯数字金额不换算
        triplets = [T("报警人", "转账", "99元", ot="AMOUNT"),
                    T("对方", "要求转账", "报警人")]
        tx = _build_transactions(triplets)
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx[0].amount, 99.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
