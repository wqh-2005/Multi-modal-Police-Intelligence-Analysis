from app.models.alert import Alert

class AlertTemplate:
    
    @staticmethod
    def deepfake_alert() -> Alert:
        return Alert(
            type = "deepfake_warning",
            level="高",
            title="疑似使用AI换脸技术",
            message="检测到视频内容可能使用了AI换脸技术，请注意自身财产安全",
        )

    @staticmethod
    def fraud_alert(fraud_title: str, confidence: str, warning: str, reason: str) -> Alert:
        return Alert(
            type="fraud_warning",
            level=confidence,
            title=fraud_title,
            message=f"您正在遭遇{fraud_title}。",
            warning=warning,
            reason=reason,
        )

    @staticmethod
    def safe_alert() -> Alert:
        return Alert(
            type="safe_noteice",
            level="低",
            title="暂未发现诈骗风险",
            message="根据您提供的信息，我们暂未发现明确的诈骗特征，但建议您小心为妙",
        )