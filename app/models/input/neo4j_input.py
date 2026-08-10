from typing import List, Optional
from pydantic import BaseModel, Field, field_validator 
from datetime import datetime

class VictimInfo(BaseModel):
    """受害人信息"""
    name: str = Field(default="未知", description="受害人姓名")
    age: int = Field(default=0, description="受害人年龄")
    profession: str = Field(default="未知", description="受害人职业")
    phone: Optional[str] = Field(default=None, description="受害人电话号码")
    id_card: Optional[str] = Field(default=None, description="受害人身份证号")

    @field_validator('name', 'profession',mode = 'before')
    @classmethod
    def normalize_fields(cls, v):
        if v is None or v == '':    #处理空值
            return "未知"
        if isinstance(v, (int,float)):
            return str(v)
        return str(v).strip()


class SuspectInfo(BaseModel):
    """嫌疑人信息"""
    name: str = Field(default="未知", description="嫌疑人姓名")
    age: int = Field(default=0, description="嫌疑人年龄")
    amount: str = Field(default=None, description="嫌疑人的银行账户")

    @field_validator('name', 'amount',mode = 'before')
    @classmethod
    def normalize_fields(cls, v):
        if v is None or v == '':    #处理空值
            return "未知"
        if isinstance(v, (int,float)):
            return str(v)
        return str(v).strip()


class RelationshipInfo(BaseModel):
    """关系信息"""
    from_: str = Field(alias="from",default="未知", description="关系起点")
    type_: str = Field(alias="type",default="未知", description="关系类型")
    to_: str = Field(alias="to",default="未知", description="关系终点")

    class Config:
        populate_by_name = True  # 允许使用别名进行赋值

    @field_validator('from_', 'type_', 'to_', mode='before')
    @classmethod
    def normalize_fields(cls, v):
        """统一处理：None 或空字符串转为 '未知'"""
        if v is None or v == "":
            return "未知"
        return str(v).strip()


class TransactionInfo(BaseModel):
    """交易信息"""
    from_: str = Field(alias="from", default="未知", description="转出方")
    to_: str = Field(alias="to", default="未知", description="转入方")
    amount: float = Field(ge=0, default=0.0, description="交易金额")
    time: Optional[str] = Field(default=None, description="交易时间，ISO格式")

    class Config:
        populate_by_name = True 

    @field_validator('from_', 'to_', mode='before')
    @classmethod
    def normalize_string_fields(cls, v):
        """统一处理字符串字段：None 或空字符串转为 '未知'"""
        if v is None or v == "":
            return "未知"
        return str(v).strip()

    @field_validator('time', mode='before')
    @classmethod
    def normalize_time(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()


    @field_validator('amount', mode='before')
    @classmethod
    def normalize_amount(cls, v):
        """统一处理金额：转换为 float"""
        if v is None or v == "":
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            # 移除可能的中文单位
            v = v.replace('元', '').replace('¥', '').replace(',', '').strip()
            try:
                return float(v)
            except ValueError:
                return 0.0
        return 0.0



class Neo4jData(BaseModel):
    """Neo4j 数据模型"""
    case_id: str = Field(default="未知", description="案例唯一标识")
    victim_info: VictimInfo = Field(default_factory=VictimInfo, description="受害人信息")
    suspect_info: SuspectInfo = Field(default_factory=SuspectInfo, description="嫌疑人信息")
    relationships: List[RelationshipInfo] = Field(default_factory=list, description="关系信息列表")
    transactions: List[TransactionInfo] = Field(default_factory=list, description="交易信息列表")
    chat_history: str = Field(default="", description="聊天记录")
    deepfake_alert: bool = Field(default=False, description="是否存在AI换脸嫌疑")

    def to_victim_text(self) -> str:
        lines = []

        '''受害人信息'''
        lines.append(f"受害人姓名：{self.victim_info.name}")
        lines.append(f"受害人年龄：{self.victim_info.age}")
        lines.append(f"受害人职业：{self.victim_info.profession}")
        if self.victim_info.phone:
            lines.append(f"受害人电话：{self.victim_info.phone}")
        if self.victim_info.id_card:
            lines.append(f"受害人身份证号：{self.victim_info.id_card}")

        '''嫌疑人信息'''
        lines.append(f"\n嫌疑人姓名：{self.suspect_info.name}")
        lines.append(f"嫌疑人年龄：{self.suspect_info.age}")
        lines.append(f"嫌疑人的银行账户：{self.suspect_info.amount}")

        '''关系信息'''

        for rel in self.relationships:
            if rel.from_ == "未知" and rel.to_ == "未知" and rel.type_ == "未知":
                continue
            else:
                lines.append(f"{rel.from_} -> {rel.type_} -> {rel.to_}")

        for tx in self.transactions:
            if tx.time:
                if tx.from_ == "未知" and tx.amount == "未知" and tx.to_ == "未知":
                    continue
                else:
                    lines.append(f"资金交易：{tx.from_} 向 {tx.to_} 转账 {tx.amount}元，时间：{tx.time}")
            else:
                if tx.from_ == "未知" and tx.amount == "未知" and tx.to_ == "未知":
                    continue
                else:
                    lines.append(f"资金交易：{tx.from_} 向 {tx.to_} 转账 {tx.amount}元")

        if self.chat_history:
            lines.append(f"\n聊天记录摘要：{self.chat_history}")

        return "\n".join(lines)


    def to_dict(self) -> dict:
        return self.model_dump(by_alias=True)