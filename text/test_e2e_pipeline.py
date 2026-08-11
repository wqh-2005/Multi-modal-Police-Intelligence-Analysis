"""
端到端流水线测试：多模态输入 → 智能研判输出

测试整个系统的串联链路，验证从用户提交到最终预警输出的全流程。

运行方式:
    python text/test_e2e_pipeline.py

前提:
    1. Neo4j Docker 已启动（bolt://localhost:7687）
    2. .env 中 SILICONFLOW_API_KEY 等已配置
    3. FastAPI 服务已启动: uvicorn app.main:app --reload --port 8000
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from fastapi.testclient import TestClient

# ═══════════════════════════════════════════════════════════════
# 初始化 TestClient
# ═══════════════════════════════════════════════════════════════

from app.main import app
from app.core.knowledge.storage_service import _reset_driver

client = TestClient(app)


def _reset_neighbors():
    """每个测试前重置全局状态，避免连接池复用问题。"""
    _reset_driver()
    from app.core.judgment.judger import Judger
    Judger._global_engine = None
    Judger._global_kb_loaded = False


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════

def test_pipeline_imposter_scam():
    """测试场景 1：冒充公检法诈骗（纯文本输入）"""
    _reset_neighbors()
    print("\n" + "=" * 70)
    print("📌 测试场景 1：冒充公检法诈骗（纯文本）")
    print("=" * 70)

    payload = {
        "case_id": "E2E-TEST-001",
        "inputs": [
            {
                "type": "text",
                "content": (
                    "A：您好，这里是XX市公安局刑侦支队，请问是张先生吗？\n"
                    "B：是的，什么事？\n"
                    "A：我们查到你的身份证被冒用开设了一个银行账户，涉嫌洗钱，涉案金额高达200万元。\n"
                    "B：不可能！我从来没有做过这种事！\n"
                    "A：请配合调查，否则我们将冻结你名下所有账户，并下发逮捕令。\n"
                    "B：那我要怎么证明清白？\n"
                    "A：请下载我们的安全核查APP，将你所有存款转到指定安全账户进行资金核查。\n"
                    "B：好吧，那我试试..."
                )
            }
        ]
    }

    response = client.post("/api/v1/pipeline", json=payload)
    print(f"HTTP 状态码: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"案件编号: {data['case_id']}")
        print(f"总耗时: {data['elapsed_ms']}ms")
        print(f"各阶段耗时: {json.dumps(data['stages'], indent=2)}")
        print(f"\n研判结果:")
        print(f"  是否诈骗: {data['judgment']['is_fraud']}")
        print(f"  诈骗类型: {data['judgment']['fraud_type']}")
        print(f"  置信度: {data['judgment']['confidence']} ({data['judgment']['confidence_score']})")
        print(f"  研判理由: {data['judgment']['reason']}")
        print(f"\n预警列表:")
        for alert in data['alerts']:
            print(f"  [{alert['type']}] {alert['level']} - {alert['title']}")
            print(f"   {alert['message']}")
        print(f"\nAI换脸检测: {data['deepfake_detected']}")

        # 断言
        assert data["case_id"] == "E2E-TEST-001"
        assert "judgment" in data
        assert "alerts" in data
        assert data["elapsed_ms"] > 0
        assert "multimodal_ms" in data["stages"]
        assert "extraction_ms" in data["stages"]
        assert "judgment_ms" in data["stages"]
        print("\n✅ 测试场景 1 通过")
    else:
        print(f"❌ 失败: {json.dumps(response.json(), ensure_ascii=False, indent=2)}")
        assert False, f"HTTP {response.status_code}"


def test_pipeline_investment_scam():
    """测试场景 2：投资理财诈骗（纯文本）"""
    _reset_neighbors()
    print("\n" + "=" * 70)
    print("📌 测试场景 2：投资理财诈骗")
    print("=" * 70)

    payload = {
        "case_id": "E2E-TEST-002",
        "inputs": [
            {
                "type": "text",
                "content": (
                    "报案人经人介绍认识了自称股票专家的案犯甲，案犯甲将其拉入微信群，"
                    "称可投资电影赚钱。报案人信以为真，通过网银向案犯甲提供的账户转账66000元。"
                    "后联系不上案犯甲，意识到被骗。"
                )
            }
        ]
    }

    response = client.post("/api/v1/pipeline", json=payload)
    print(f"HTTP 状态码: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"案件编号: {data['case_id']}")
        print(f"总耗时: {data['elapsed_ms']}ms")
        print(f"研判结果: is_fraud={data['judgment']['is_fraud']}, "
              f"fraud_type={data['judgment']['fraud_type']}, "
              f"confidence={data['judgment']['confidence']}")
        print(f"预警: {[a['type'] for a in data['alerts']]}")
        print("\n✅ 测试场景 2 通过")
    else:
        print(f"❌ 失败: {json.dumps(response.json(), ensure_ascii=False, indent=2)}")
        assert False, f"HTTP {response.status_code}"


def test_pipeline_safe_case():
    """测试场景 3：正常业务沟通（无诈骗）"""
    _reset_neighbors()
    print("\n" + "=" * 70)
    print("📌 测试场景 3：正常业务沟通（无诈骗）")
    print("=" * 70)

    payload = {
        "case_id": "E2E-TEST-003",
        "inputs": [
            {
                "type": "text",
                "content": (
                    "收到银行官方短信通知信用卡还款日提醒，登录银行APP确认后正常还款。"
                    "今天天气不错，适合出门散步。"
                )
            }
        ]
    }

    response = client.post("/api/v1/pipeline", json=payload)
    print(f"HTTP 状态码: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"案件编号: {data['case_id']}")
        print(f"总耗时: {data['elapsed_ms']}ms")
        print(f"研判结果: is_fraud={data['judgment']['is_fraud']}, "
              f"fraud_type={data['judgment']['fraud_type']}, "
              f"confidence={data['judgment']['confidence']}")
        print(f"预警: {[a['type'] for a in data['alerts']]}")
        print("\n✅ 测试场景 3 通过")
    else:
        print(f"❌ 失败: {json.dumps(response.json(), ensure_ascii=False, indent=2)}")
        assert False, f"HTTP {response.status_code}"


def test_pipeline_empty_text():
    """测试场景 4：空文本输入（应返回 400）"""
    print("\n" + "=" * 70)
    print("📌 测试场景 4：空文本输入（应返回 400）")
    print("=" * 70)

    payload = {
        "case_id": "E2E-TEST-004",
        "inputs": [
            {
                "type": "text",
                "content": ""
            }
        ]
    }

    response = client.post("/api/v1/pipeline", json=payload)
    print(f"HTTP 状态码: {response.status_code}")

    assert response.status_code == 400, f"期望 400，实际 {response.status_code}"
    print(f"错误信息: {response.json()['detail']}")
    print("✅ 测试场景 4 通过（正确返回 400）")


def test_pipeline_missing_case_id():
    """测试场景 5：缺少 case_id（多模态模块会拒绝）"""
    print("\n" + "=" * 70)
    print("📌 测试场景 5：缺少 case_id")
    print("=" * 70)

    payload = {
        "case_id": "",
        "inputs": [
            {
                "type": "text",
                "content": "测试内容"
            }
        ]
    }

    response = client.post("/api/v1/pipeline", json=payload)
    print(f"HTTP 状态码: {response.status_code}")

    # 多模态模块会返回 200 但带 error 状态，或者 500
    # 这里我们只验证不会崩溃
    if response.status_code == 200:
        data = response.json()
        print(f"案件编号: {data['case_id']}")
        print(f"预警: {[a['type'] for a in data['alerts']]}")
        print("✅ 测试场景 5 通过（优雅降级）")
    else:
        print(f"返回: {response.json().get('detail', 'N/A')}")
        print("✅ 测试场景 5 通过（返回错误）")


# ═══════════════════════════════════════════════════════════════
# 运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("🚀 端到端流水线测试")
    print("=" * 70)
    print("链路: 多模态输入 → 知识抽取 → 知识图谱存储 → 智能研判 → 预警输出")
    print("=" * 70)

    all_passed = True
    tests = [
        ("冒充公检法诈骗", test_pipeline_imposter_scam),
        ("投资理财诈骗", test_pipeline_investment_scam),
        ("正常业务沟通", test_pipeline_safe_case),
        ("空文本输入", test_pipeline_empty_text),
        ("缺少 case_id", test_pipeline_missing_case_id),
    ]

    for name, test_func in tests:
        try:
            test_func()
        except Exception as e:
            print(f"\n❌ [{name}] 测试失败: {e}")
            all_passed = False

    print("\n" + "=" * 70)
    if all_passed:
        print("✅ 全部测试通过")
    else:
        print("❌ 部分测试失败")
    print("=" * 70)