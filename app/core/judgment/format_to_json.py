class FormatToJson:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.detection_rules = [
            (
                lambda item: "dialogue" in item,
                {
                    "content": "dialogue",
                    "fraud_type": "fraud_category_standard",
                    "id": "id",
                    "source": "source",
                    "data_type": "dialogue"
                }
            ),
            (
                lambda item: "案件描述" in item,
                {
                    "content": "案件描述",
                    "fraud_type": "案件类型",
                    "id": "id",
                    "source": "来源",
                    "data_type": "case_summary"
                }
            )
            (
                lambda item: True,
                {
                    "content": self._find_content_field,
                    "fraud_type": self._find_type_field,
                    "id": "id",
                    "source": "source",
                    "data_type": "unknown"
                }
            )
        ]

    def _detect_data_type(self, data:List[Dict]) -> dict:
        if not data:
            return self.detection_rules[-1][1]

        