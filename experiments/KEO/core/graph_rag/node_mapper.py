from typing import Dict

from experiments.KEO.models.graph_schema import (
    EntityInput,
    NodeMapping,
)


class NodeMapper:
    """根据type和role确定节点类别及唯一ID，不负责存储或向量生成。"""

    DEFAULT_SHARED_TYPES = {
        "身份角色",
        "机构",
        "应用程序",
        "平台",
    }

    PERSON_CATEGORIES = {
        "疑似诈骗者": "诈骗犯",
        "受害人": "受害人",
    }

    # 缺失或泛指名称不建立全局共享节点。
    LOCAL_ONLY_NAMES = {
        "",
        "unknown",
        "未知",
        "不详",
        "未知人员",
        "未知机构",
        "某机构",
        "某应用",
        "某app",
        "某平台",
        "app会议视频",
    }

    def __init__(self) -> None:
        self.shared_types = set(self.DEFAULT_SHARED_TYPES)

    def _build_shared_id(self, entity_type: str, name: str) -> str:
        """沿用类型和规范名称生成共享ID，保留中文，不做URL编码。"""
        return f"concept:{entity_type}:{name}"

    def _can_share(self, entity_type: str, name: str) -> bool:
        """判断实体是否可以建立全局共享节点。

        Args:
            entity_type: 实体类型。
            name: 实体名称。
        """
        return (
            entity_type in self.shared_types
            and name.casefold() not in self.LOCAL_ONLY_NAMES
        )

    def map_entity(self, entity: Dict, source_id: str) -> NodeMapping:
        """将原实体映射为图谱节点。

        Args:
            entity: 原实体输入。
            source_id: 来源ID，仅在此校验，不拼接到已有实体ID。

        """

        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id不能为空")

        item = EntityInput(**entity)

        # 数据已经清洗，此处仅清除名称、类型和角色的首尾空白。
        name = item.name.strip()
        entity_type = item.type.strip()
        role_value = item.role.strip() if item.role else None

        if entity_type == "人员":
            # 人员直接沿用全局唯一原实体ID，不按名称或角色合并。

            node_id = item.id
            scope = "case"

            category = self.PERSON_CATEGORIES.get(
                role_value,
                role_value or "人员",
            )

            if name.casefold() in self.LOCAL_ONLY_NAMES:
                # 缺失名称只调整展示文字，不改变节点身份或原role。
                display_name = f"{category}（姓名未知）"
            else:
                display_name = f"{category}（{name}）"

        elif self._can_share(entity_type, name):
            # 身份、明确机构、APP等使用共享概念节点。
            node_id = self._build_shared_id(entity_type, name)
            scope = "shared"
            category = entity_type
            display_name = name

        else:
            # 账户、金额及名称不明确的概念默认沿用原实体ID。
            node_id = item.id
            scope = "case"
            category = entity_type
            display_name = name

        return NodeMapping(
            node_id=node_id,
            scope=scope,
            name=name,
            type=entity_type,
            role=role_value,
            graph_category=category,
            display_name=display_name,
        )


if __name__ == "__main__":
    from pprint import pprint

    mapper = NodeMapper()

    suspect = {
        "id": "case_012615_E003",
        "name": "客服",
        "type": "人员",
        "role": "疑似诈骗者",
    }

    identity = {
        "id": "case_012615_E004",
        "name": "客服",
        "type": "身份角色",
        "role": None,
    }

    pprint(mapper.map_entity(suspect, "case_012615"))
    pprint(mapper.map_entity(identity, "case_012615"))
