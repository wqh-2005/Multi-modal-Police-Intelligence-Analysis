# app/core/alert/models.py
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from pprint import pprint
import asyncio
from app.core.judgment.judger import Judger
from app.models.output.alerttmplates import AlertTemplate
from app.models.output.alert import Alert

class AlertOutput:

    def __init__(self):
        self.judger = Judger()

    async def generate(self, neo4j_data_single: dict) -> dict:
        judger_result = await self.judger.judge(neo4j_data_single)

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
    async def generator_batch(self, neo4j_data: List[dict]) -> List[dict]:
        cout_to_frontend = {"total": 0,"skipped": 0, "results": []}
        for neo4j_data_single in neo4j_data:
            case_id = neo4j_data_single.get("case_id")
            if not case_id:
                print("该case_id无法识别")
                cout_to_frontend["skipped"] += 1
                continue
            single_result  = await self.generate(neo4j_data_single)
            cout_to_frontend["results"].append(single_result)
            cout_to_frontend["total"] += 1

        return cout_to_frontend
        

if __name__ == "__main__":
    pass