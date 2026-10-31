from typing import Dict, Optional, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator


class EntityInput(BaseModel):
    """A交付的head或tail实体。"""

    id: str = Field(description="原实体ID")
    name: str = Field(description="原实体名称")
    type: str = Field(description="实体类型")
    role: Optional[str] = Field(
        default=None,
        description="原人物角色"
    )

    @field_validator("id", "name", "type")
    @classmethod
    def check_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("实体id、name和type不能为空")
        return value


class NodeMapping(BaseModel):
    """一次原实体到图谱节点的映射结果。"""

    node_id: str = Field(description="图谱节点唯一ID")
    scope: Literal["case", "shared"] = Field(
        description="案件内节点或共享概念节点"
    )

    source_id: str = Field(description="当前实体所属案例")
    original_entity_id: str = Field(description="原实体ID")
    original_name: str = Field(description="原实体名称")

    name: str = Field(description="节点名称")
    type: str = Field(description="节点类型")
    role: Optional[str] = Field(default=None)

    graph_category: str = Field(description="图谱分类")
    display_name: str = Field(description="展示名称")


class NodeMapper:
    """根据type和role确定节点类别及唯一ID。"""

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

    def __init__(self):
        self.shared_types = set(self.DEFAULT_SHARED_TYPES)

    @staticmethod
    def _encode(value: str) -> str:
        """编码分隔符，避免不同字段拼接后产生相同ID。"""
        return quote(value, safe="")

    def _build_local_id(
        self,
        source_id: str,
        entity_id: str,
    ) -> str:
        return (
            f"local:{self._encode(source_id)}:"
            f"{self._encode(entity_id)}"
        )

    def _build_shared_id(
        self,
        entity_type: str,
        name: str,
    ) -> str:
        return (
            f"concept:{self._encode(entity_type)}:"
            f"{self._encode(name)}"
        )

    def _can_share(self, entity_type: str, name: str) -> bool:
        return (
            entity_type in self.shared_types
            and name.casefold() not in self.LOCAL_ONLY_NAMES
        )

    def map_entity(
        self,
        entity: Dict,
        source_id: str,
    ) -> Dict:
        """
        输入一个head或tail，返回节点映射。

        同一原实体反复调用，得到相同node_id。
        不在这里建立关系、读取文件或调用Embedding。
        """
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id不能为空")

        item = EntityInput(**entity)

        # A已完成名称规范化，这里只清除首尾空白。
        name = item.name.strip()
        entity_type = item.type.strip()
        role_value = (item.role or "").strip()

        if entity_type == "人员":
            # 人员始终使用案件内ID，不因角色相同而合并。
            node_id = self._build_local_id(source_id, item.id)
            scope = "case"

            category = self.PERSON_CATEGORIES.get(
                role_value,
                role_value or "人员",
            )

            if name.casefold() in self.LOCAL_ONLY_NAMES:
                display_name = f"{category}（姓名未知）"
            else:
                display_name = f"{category}（{name}）"

        elif self._can_share(entity_type, name):
            # 身份、明确机构、APP等共享概念节点。
            node_id = self._build_shared_id(entity_type, name)
            scope = "shared"
            category = entity_type
            display_name = name

        else:
            # 账户、金额及名称不明确的概念默认案件内保存。
            node_id = self._build_local_id(source_id, item.id)
            scope = "case"
            category = entity_type
            display_name = name

        return NodeMapping(
            node_id=node_id,
            scope=scope,
            source_id=source_id,
            original_entity_id=item.id,
            original_name=item.name,
            name=name,
            type=entity_type,
            role=item.role,
            graph_category=category,
            display_name=display_name,
        ).model_dump()


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