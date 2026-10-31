from typing import Optional, List, Literal

from pydantic import BaseModel, Field

class EntityInput(BaseModel):
    '''数据清洗后的数据中head或tail实体'''

    id: str = Field(description = "原实体ID")
    name: str = Field(description = "原实体名称")
    type: str = Field(description = "实体类型")
    role: Optional[str] = Field(
        default = None,
        description = "原人物角色"
    )

class NodeMapping(BaseModel):
    '''一次原实体到图谱实体的映射结果
        scope: case内节点或共享概念节点,区分节点是否bei
        {
            "id": node.node_id,

            # 这段文字交给Embedding模型，生成向量
            "document": "名称：客服；类型：人员；角色：疑似诈骗者；图谱分类：诈骗犯。",

            # 普通属性，用于定位和筛选
            "metadata": {
                "node_id": node.node_id,
                "source_id": "case_012615",
                "name": "客服",
                "type": "人员",
                "role": "疑似诈骗者",
                "scope": "case",
                "graph_category": "诈骗犯",
                "display_name": "诈骗犯（客服）"
            }
        }
    '''

    node_id: str = Field(description="图谱节点唯一ID")
    scope: Optional[Literal["case", "shared"]] = Field(
        default = None,
        description = "节点作用域"
    )
    
    name: str = Field(description="节点名称")
    type: str = Field(description="节点类型")
    role: Optional[str] = Field(default=None)

    graph_category: str = Field(description="图谱分类")
    display_name: str = Field(description="展示名称")



    