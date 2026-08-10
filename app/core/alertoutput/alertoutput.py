'''
最终输出
{
    "total": len(results),
    "results": [
        {
            "case_id": "...",
            "judgment": {
                "case_id":str,
                "is_fraud": true,
                "fraud_type": "冒充公检法类诈骗",
                "confidence": "高",
                "confidence_score": 0.95,
                "reason": "骗子冒充公检法机关，以涉嫌洗钱为由要求受害者转账到所谓的安全账户，符合冒充公检法类诈骗的典型特征。",
                "warning": "⚠️ 立即停止转账！公安机关不会通过电话办案，不会设立安全账户。请拨打 96110 报警。",
                "similar_cases": [
                    {
                        "content": "案例5：刘女士接到公安局电话，称其涉嫌洗钱，需将资金转入安全账户验证，被骗23万元。",
                        "score": 0.712
                    }
                ],
                "timestamp": "2026-07-27T14:35:00+08:00",
            },
            "alerts": [
                {
                    "type": "deepfake_alert",
                    "level": "高",
                    "title": "⚠️ 疑似使用AI换脸技术",
                    "message": "检测到视频内容可能使用了AI换脸技术...",
                    "actions": ["通过电话核实身份", "不要仅凭视频确认"]
                },
                {
                    "type": "fraud_warning",
                    "level": "高",
                    "title": "🔴 诈骗风险预警",
                    "message": "冒充公检法类诈骗...",
                    "actions": ["立即停止转账", "拨打96110"]
                },
                {
                    type="safe_notice",       # ← 值不同
                    level="低",               # ← 值不同
                    title="✅ 安全提示",       # ← 值不同
                    message="暂未发现风险...", # ← 值不同
                    actions=[...]        
                }

            ]
        }
    ]
}
'''

# app/core/alert/models.py
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from pprint import pprint
from app.core.judgment.judger import Judger
from app.models.output.alerttmplates import AlertTemplate
from app.models.output.alert import Alert

class AlertOutput:

    def __init__(self):
        self.judger = Judger()

    def generate(self, neo4j_data_single: dict) -> dict:
        judger_result = self.judger.judge(neo4j_data_single)

        alerts: List[Alert] = []

        if judger_result.get("error"):
            alerts.append(Alert(
                type="service_error",
                level="高",
                title="研判服务异常",
                message="研判服务暂时不可用，请人工介入处理",
                warning="请通过96110反诈专线进行人工咨询",
                reason=judger_result["error"].get("message", "")
                )
            )
        else:
            if judger_result["deepfake_alert"]:
                alerts.append(AlertTemplate.deepfake_alert())

            if judger_result["is_fraud"]:
                alerts.append(AlertTemplate.fraud_alert(
                    judger_result["fraud_type"], 
                    judger_result["confidence"], 
                    judger_result["warning"], 
                    judger_result["reason"])
                    )

            if not alerts:
                alerts.append(AlertTemplate.safe_alert())
        
        return {
            "case_id": judger_result["case_id"],
            "judgment": judger_result,
            "alerts": [alert.model_dump() for alert in alerts],
            "deepfake_detected": judger_result["deepfake_alert"]
        }
    def generator_batch(self, neo4j_data: List[dict]) -> List[dict]:
        cout_to_frontend = {"total": 0,"skipped": 0, "results": []}
        for neo4j_data_single in neo4j_data:
            case_id = neo4j_data_single.get("case_id")
            if not case_id:
                print("该case_id无法识别")
                cout_to_frontend["skipped"] += 1
                continue
            single_result  = self.generate(neo4j_data_single)
            cout_to_frontend["results"].append(single_result)
            cout_to_frontend["total"] += 1

        return cout_to_frontend
        

if __name__ == "__main__":

    print("=" * 60)
    print("🔍 测试预警模块 (AlertOutput)")
    print("=" * 60)

    # 1. 先初始化知识库（只执行一次）
    load_count = Judger.init_knowledge_base()
    print(f"知识库加载完成，新增案例数：{load_count}\n")

    # 2. 创建 AlertOutput 实例
    alert_output = AlertOutput()

    # ============================================================
    # 测试场景1：诈骗 + AI换脸（两个预警都触发）
    # ============================================================
    print("=" * 60)
    print("📌 测试场景1：诈骗 + AI换脸")
    print("=" * 60)

    test_data_1 = {
        "case_id": "TEST-001",
        "victim": {
            "name": "张先生",
            "age": 45,
            "profession": "公司职员"
        },
        "relations": [
            {
                "from": "010-XXXXXXX陌生来电",
                "type": "通话",
                "to": "张先生"
            }
        ],
        "transactions": [
            {
                "from": "张先生银行卡",
                "to": "所谓安全账户",
                "amount": 380000
            }
        ],
        "chat_history": "对方自称北京朝阳公安，称本人身份证被盗用洗钱200万，要求全部资金转入安全账户配合调查，否则追究刑责。",
        "deepfake_alert": True
    }

    result_1 = alert_output.generate(test_data_1)
    print("📊 结果：")
    pprint(result_1, width=130)
    print("\n" + "-" * 60)

    # ============================================================
    # 测试场景2：仅诈骗（无AI换脸）
    # ============================================================
    print("=" * 60)
    print("📌 测试场景2：仅诈骗（无AI换脸）")
    print("=" * 60)

    test_data_2 = {
        "case_id": "TEST-002",
        "victim": {
            "name": "李女士",
            "age": 35,
            "profession": "个体商户"
        },
        "relations": [
            {
                "from": "微信好友",
                "type": "诱导",
                "to": "李女士"
            }
        ],
        "transactions": [
            {
                "from": "李女士",
                "to": "骗子账户",
                "amount": 12000
            }
        ],
        "chat_history": "在微信群看到刷单兼职广告，前几单有返利，后面垫付1.2万后无法提现。",
        "deepfake_alert": False  # ❌ AI换脸检测为 False
    }

    result_2 = alert_output.generate(test_data_2)
    print("📊 结果：")
    pprint(result_2, width=130)
    print("\n" + "-" * 60)

    # ============================================================
    # 测试场景3：仅AI换脸（无诈骗）
    # ============================================================
    print("=" * 60)
    print("📌 测试场景3：仅AI换脸（无诈骗）")
    print("=" * 60)

    test_data_3 = {
        "case_id": "TEST-003",
        "victim": {
            "name": "王先生",
            "age": 28,
            "profession": "学生"
        },
        "relations": [],
        "transactions": [],
        "chat_history": "朋友发来视频通话，但对方视频看起来不自然，声音也不太对，怀疑是AI换脸。",
        "deepfake_alert": True  # ✅ AI换脸检测为 True
    }

    result_3 = alert_output.generate(test_data_3)
    print("📊 结果：")
    pprint(result_3, width=130)
    print("\n" + "-" * 60)

    # ============================================================
    # 测试场景4：无风险（无诈骗，无AI换脸）
    # ============================================================
    print("=" * 60)
    print("📌 测试场景4：无风险（无诈骗，无AI换脸）")
    print("=" * 60)

    test_data_4 = {
        "case_id": "TEST-004",
        "victim": {
            "name": "赵女士",
            "age": 32,
            "profession": "白领"
        },
        "relations": [],
        "transactions": [],
        "chat_history": "收到银行官方短信通知信用卡还款日提醒，登录银行APP确认后正常还款。",
        "deepfake_alert": False  # ❌ AI换脸检测为 False
    }

    result_4 = alert_output.generate(test_data_4)
    print("📊 结果：")
    pprint(result_4, width=130)
    print("\n" + "-" * 60)

    # ============================================================
    # 测试场景5：批量测试
    # ============================================================
    print("=" * 60)
    print("📌 测试场景5：批量测试（4个案例一起）")
    print("=" * 60)

    batch_data = [test_data_1, test_data_2, test_data_3, test_data_4]
    batch_result = alert_output.generator_batch(batch_data)

    print(f"📊 批量结果（共 {batch_result['total']} 个案例）：")
    for i, result in enumerate(batch_result["results"], 1):
        alert_types = [a["type"] for a in result.get("alerts", [])]
        print(f"  {i}. {result['case_id']}: {alert_types}")

    print("\n" + "=" * 60)
    print("✅ 测试完成")
    print("=" * 60)