"""知识抽取服务 (模块二)。

调用硅基流动 Qwen API，从非结构化文本中抽取三元组关系。

提示词采用"角色→实体类型→规则→输出格式→少样本示例→输入"六段式结构，
遵循 OpenAI / Anthropic 提示工程最佳实践。

对外接口:
    run_extraction(text, case_id) -> ExtractionOutput (格式 1.3)

下游对接:
    storage_service.run_storage()
"""
import json
import re
from logging import getLogger

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from app.config.settings import llm_settings
from app.models.knowledge_schema import ExtractionOutput, Triplet

logger = getLogger(__name__)

llm = ChatOpenAI(
    api_key=llm_settings.SILICONFLOW_API_KEY,
    base_url=llm_settings.SILICONFLOW_BASE_URL,
    model=llm_settings.EXTRACTION_MODEL,
    temperature=llm_settings.EXTRACTION_TEMPERATURE,
)

# ---------------------------------------------------------------------------
# 提示词组件（可配置，非硬编码）
# ---------------------------------------------------------------------------

# 实体类型定义，供提示词引用
_ENTITY_TYPE_DEF = (
    "PERSON(人名/角色) ORGANIZATION(机构/公司) PLATFORM(平台/APP/群聊) "
    "PHONE(手机号) ACCOUNT(银行账户) AMOUNT(金额) "
    "ID_CARD(身份证) URL(链接) TIME(时间) LOCATION(地点) OTHER(其他)"
)

# 少样本示例：从 sample/ 真实数据集中提取, 覆盖对话和叙述两种场景
_FEWSHOT_DIALOGUE = {
    "text": (
        "A：\"您好，这里是XX公安局，请配合调查一起资金案件。\"\n"
        "B：\"警察同志，是涉及什么案件啊？\"\n"
        "A：\"您账户已被冻结，需下载安全核查APP处理。\"\n"
        "B：\"没听说过这事，你们怎么证明身份？\"\n"
        "A：\"立即操作，否则将依法处置，并通知银行。\"\n"
        "B：\"[紧张]那我先下载试试...\"\n"
        "A：\"请在APP中填写收到的验证码。\""
    ),
    "output": [
        {"subject": "A", "relation": "冒充", "object": "XX公安局",
         "subject_type": "PERSON", "object_type": "ORGANIZATION"},
        {"subject": "A", "relation": "威胁", "object": "B",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "A", "relation": "要求下载", "object": "B",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "B", "relation": "质疑", "object": "A",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "B", "relation": "下载", "object": "安全核查APP",
         "subject_type": "PERSON", "object_type": "PLATFORM"},
        {"subject": "A", "relation": "要求填写", "object": "B",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "B", "relation": "填写", "object": "验证码",
         "subject_type": "PERSON", "object_type": "OTHER"},
    ],
}

_FEWSHOT_NARRATIVE = {
    "text": (
        "报案人经人介绍认识了自称股票专家的案犯甲，案犯甲将其拉入微信群，"
        "称可投资电影赚钱。报案人信以为真，通过网银向案犯甲提供的账户转账66000元。"
        "后联系不上案犯甲，意识到被骗。"
    ),
    "output": [
        {"subject": "报案人", "relation": "添加", "object": "案犯甲",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "案犯甲", "relation": "冒充", "object": "股票专家",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "案犯甲", "relation": "拉入群聊", "object": "报案人",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "案犯甲", "relation": "诱导", "object": "报案人",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "报案人", "relation": "转账", "object": "66000元",
         "subject_type": "PERSON", "object_type": "AMOUNT"},
    ],
}

_FEWSHOT_NARRATIVE2 = {
    "text": (
        "受害人收到短信称加支付宝好友可领礼品。添加后被拉入刷单群，"
        "按群内指示下载了乐橙APP，多次转账给对方账户共56946元。"
        "后发现无法提现，意识到被骗。"
    ),
    "output": [
        {"subject": "受害人", "relation": "添加", "object": "支付宝好友",
         "subject_type": "PERSON", "object_type": "ACCOUNT"},
        {"subject": "对方", "relation": "拉入群聊", "object": "受害人",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "受害人", "relation": "下载", "object": "乐橙APP",
         "subject_type": "PERSON", "object_type": "PLATFORM"},
        {"subject": "对方", "relation": "要求转账", "object": "受害人",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "受害人", "relation": "转账", "object": "56946元",
         "subject_type": "PERSON", "object_type": "AMOUNT"},
    ],
}

_FEWSHOT_NARRATIVE3 = {
    "text": (
        "报警人下载了安心借APP并注册。系统提示联系客服后，"
        "报警人添加了对方的QQ，对方向其发送二维码，报警人扫码转账74000元。"
    ),
    "output": [
        {"subject": "报警人", "relation": "下载", "object": "安心借APP",
         "subject_type": "PERSON", "object_type": "PLATFORM"},
        {"subject": "报警人", "relation": "添加", "object": "对方的QQ",
         "subject_type": "PERSON", "object_type": "ACCOUNT"},
        {"subject": "对方", "relation": "发送", "object": "二维码",
         "subject_type": "PERSON", "object_type": "OTHER"},
        {"subject": "报警人", "relation": "扫码", "object": "二维码",
         "subject_type": "PERSON", "object_type": "OTHER"},
        {"subject": "对方", "relation": "要求转账", "object": "报警人",
         "subject_type": "PERSON", "object_type": "PERSON"},
        {"subject": "报警人", "relation": "转账", "object": "74000元",
         "subject_type": "PERSON", "object_type": "AMOUNT"},
    ],
}


def _build_prompt(text):
    """构建抽取提示词, 使用三重引号分隔各段, 指令在前示例在后, 输入文本最后。

    Args:
        text: 待抽取文本。

    Returns:
        str: 完整的提示词。
    """
    return f"""## 角色
你是一名诈骗案件信息抽取专家，擅长从警情文本中识别人员、组织、平台、金额等实体及其之间的行为关系。

## 实体类型
{_ENTITY_TYPE_DEF}

## 核心要求
1. 必须为施骗方（对方/陌生人/案犯/客服/嫌疑人等）和受骗方（报警人/受害人/事主/被害人等）分别抽取行为关系，缺一不可
2. 施骗方即使没有具体姓名，也必须使用原词（如"对方""陌生人""客服"）作为 subject
3. 施骗方的常见行为包括：发送、要求（转账/下载/充值）、诱导、拉入群聊、提供、冒充、威胁等，这些都必须抽取
4. 每笔转账都要单独抽取：受骗方→转账→金额，不要合并

## 抽取规则
5. 关系的 object 按以下原则确定：
   - 人际行为（威胁、要求、冒充、诱导、联系、拉入群聊等）→ object 为被作用的人
   - 物操作（发送、下载、安装、填写、点击、扫码等）→ object 为内容对象（链接、APP、验证码、二维码等）
   - 资金行为（转账、充值、提现、投入等）→ object 为金额
6. 实体名称使用原文中的原词，不要改写或合并
7. subject_type 和 object_type 必须从上述实体类型中选择
8. 关系名使用简洁的动作动词，不超过 6 个字

## 输出格式
返回纯 JSON 对象（不要加 ```json 标记）：
{{"triplets": [{{"subject":"...","relation":"...","object":"...","subject_type":"...","object_type":"..."}}], "confidence": 0.85}}

## 置信度评估
- 0.85~1.0：文本中所有人物和行为都已被抽取
- 0.5~0.7：仅抽取了部分关键人物，遗漏了一些行为
- 0.0~0.4：文本中没有可抽取的人物交互行为

## 少样本示例

示例 1（双人对话-冒充公检法）：
文本：\"\"\"
{_FEWSHOT_DIALOGUE["text"]}
\"\"\"
输出：
{json.dumps(_FEWSHOT_DIALOGUE["output"], ensure_ascii=False, indent=2)}

示例 2（第三人称叙述-投资理财诈骗）：
文本：\"\"\"
{_FEWSHOT_NARRATIVE["text"]}
\"\"\"
输出：
{json.dumps(_FEWSHOT_NARRATIVE["output"], ensure_ascii=False, indent=2)}

示例 3（第三人称叙述-刷单返利诈骗）：
文本：\"\"\"
{_FEWSHOT_NARRATIVE2["text"]}
\"\"\"
输出：
{json.dumps(_FEWSHOT_NARRATIVE2["output"], ensure_ascii=False, indent=2)}

示例 4（第三人称叙述-虚假贷款诈骗）：
文本：\"\"\"
{_FEWSHOT_NARRATIVE3["text"]}
\"\"\"
输出：
{json.dumps(_FEWSHOT_NARRATIVE3["output"], ensure_ascii=False, indent=2)}

## 输入文本
\"\"\"
{text}
\"\"\""""


def _decode_json_block(block):
    """对候选 JSON 块做清洗后解析：去除行注释与尾部逗号。

    LLM 可能在 JSON 内附加 // 注释或末尾多余的逗号，
    直接 json 解析会失败，先做轻量清洗。

    Args:
        block: 候选 JSON 文本块。

    Returns:
        解析后的 JSON 对象（dict 或 list）。
    """
    decoder = json.JSONDecoder()
    block = block.strip()
    # 先按原样解析：合法 JSON 不应被清洗逻辑改动
    try:
        return decoder.raw_decode(block)[0]
    except json.JSONDecodeError:
        pass

    # 原样解析失败才做轻量清洗：去除行注释、尾部逗号、单引号包裹
    cleaned = re.sub(r"(?m)^\s*//[^\n]*$", "", block)   # 独立的 // 行注释
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)      # 结尾前多余的逗号
    try:
        return decoder.raw_decode(cleaned.strip())[0]
    except json.JSONDecodeError:
        alt = cleaned.replace("'", '"')  # 单引号包裹的键/字符串（非标准 JSON）
        if alt != cleaned:
            return decoder.raw_decode(alt.strip())[0]
        raise


def _parse_json(text):
    """从 LLM 返回的文本中提取 JSON。

    LLM 可能在 json 代码块前后附加说明文字, 也可能在 JSON 后追加解释,
    因此先截取完整 JSON 块, 再做清洗（去注释/尾逗号）后解析。

    Args:
        text: LLM 返回的原始文本。

    Returns:
        dict | list: 解析后的 JSON 对象或数组。

    Raises:
        json.JSONDecodeError: 文本无法解析为合法 JSON 时由调用方捕获。
    """
    text = text.strip()

    # 策略 1: 提取 ```json ... ``` 或 ``` ... ``` 代码块中的内容
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        return _decode_json_block(m.group(1))

    # 策略 2: 截取第一个 { 或 [ 到最后一个 } 或 ] 之间的块
    m = re.search(r"[{\[]", text)
    if m:
        start = m.start()
        last = max(text.rfind("}"), text.rfind("]"))
        if last > start:
            return _decode_json_block(text[start:last + 1])

    # 策略 3: 直接解析原文
    return _decode_json_block(text)


async def _extract_triplets(text):
    """LLM 调用：从文本中抽取三元组关系。

    加入重试机制：如果首次抽取只有 ≤1 个人物，重试一次以抵消 LLM 非确定性。

    Args:
        text: 待抽取的原始文本。

    Returns:
        (list[Triplet], float): 三元组列表和 LLM 自评估置信度。
    """
    prompt = _build_prompt(text)
    last_triplets = []
    last_confidence = 0.0

    for attempt in range(2):
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content if hasattr(response, "content") else str(response)
        # 仅记录长度，不记录原始响应（可能含用户案件/聊天文本等敏感数据）
        logger.debug("三元组抽取返回(第%d次): len=%d", attempt + 1, len(raw))

        try:
            data = _parse_json(raw)
            if isinstance(data, dict):
                # 逐条构造 Triplet：单条字段异常时跳过该条，不整批丢弃
                triplets = []
                for t in data.get("triplets", []) or []:
                    try:
                        triplets.append(Triplet(**t))
                    except Exception:
                        logger.warning("跳过非法三元组: type=%s",
                                       type(t).__name__ if isinstance(t, dict) else "?")
                        continue
                # confidence 单独解析：格式异常时仅该字段降级，不连带丢弃已解析的三元组
                try:
                    confidence = float(data.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
            elif isinstance(data, list):
                triplets = []
                for t in data:
                    try:
                        triplets.append(Triplet(**t))
                    except Exception:
                        logger.warning("跳过非法三元组: type=%s",
                                       type(t).__name__ if isinstance(t, dict) else "?")
                        continue
                confidence = 0.85 if triplets else 0.0
            else:
                triplets, confidence = [], 0.0
        except (json.JSONDecodeError, Exception) as e:
            # 仅记异常类型，不记录 str(e)：Pydantic 校验错误的字符串表示
            # 会包含 LLM 输出中的案件/金额等敏感数据
            logger.warning("三元组解析失败(第%d次): %s", attempt + 1, type(e).__name__)
            triplets, confidence = [], 0.0

        # 检查人物数量
        person_count = len({
            t.subject for t in triplets if t.subject_type == "PERSON"
        } | {
            t.object for t in triplets if t.object_type == "PERSON"
        })

        last_triplets, last_confidence = triplets, confidence

        if person_count >= 2 or attempt == 1:
            break
        logger.info("第%d次抽取仅%d个人物，重试...", attempt + 1, person_count)

    return last_triplets, last_confidence


# 嫌疑人指代词（在原文中扫描这些词作为隐式嫌疑人回退）
_IMPLICIT_SUSPECT_PATTERNS = (
    (re.compile(r"对方"), "对方"),
    (re.compile(r"陌生人"), "陌生人"),
    (re.compile(r"客服"), "客服"),
    (re.compile(r"案犯"), "案犯"),
    (re.compile(r"嫌疑人"), "嫌疑人"),
    (re.compile(r"骗子"), "骗子"),
)


def _backfill_implicit_persons(triplets, raw_text):
    """后处理：从原文中扫描未被 LLM 提取的隐式嫌疑人。

    如果 LLM 只抽出 1 个 PERSON 类实体，且原文中存在嫌疑人指代词，
    则为该指代词补一条"联系"关系的三元组。

    Args:
        triplets: LLM 抽取的三元组列表。
        raw_text: 原始文本。

    Returns:
        list[Triplet]: 补充后的三元组列表（可能已修改原列表）。
    """
    persons = {
        t.subject for t in triplets if t.subject_type == "PERSON"
    } | {
        t.object for t in triplets if t.object_type == "PERSON"
    }

    if len(persons) >= 2:
        return triplets  # 已有足够人物，不需要补充

    for pattern, name in _IMPLICIT_SUSPECT_PATTERNS:
        if pattern.search(raw_text) and name not in persons:
            # 添加一条合成三元组：嫌疑人→联系→受害人
            victim = next(iter(persons), "事主")
            triplets.append(Triplet(
                subject=name,
                relation="联系",
                object=victim,
                subject_type="PERSON",
                object_type="PERSON",
            ))
            logger.info("后处理补充隐式嫌疑人: %s", name)
            break

    return triplets


async def run_extraction(text, case_id="", deepfake_alert=False):
    """模块二主函数：单次 LLM 调用完成知识抽取。

    Args:
        text: 多模态识别后的原始文本。
        case_id: 案件编号，贯穿全流程的唯一标识。
        deepfake_alert: 多模态识别模块是否检测到 AI 换脸/伪造。

    Returns:
        ExtractionOutput: 格式 1.3，包含三元组列表和整体置信度。
    """
    logger.info("知识抽取开始, case_id=%s, text_len=%d, deepfake=%s",
                case_id, len(text), deepfake_alert)

    triplets, confidence = await _extract_triplets(text)
    triplets = _backfill_implicit_persons(triplets, text)
    logger.info("三元组: %d 个, LLM 自评估置信度: %s", len(triplets), confidence)

    return ExtractionOutput(
        triplets=triplets,
        raw_text=text,
        extraction_confidence=round(confidence, 4),
        case_id=case_id,
        deepfake_alert=deepfake_alert,
    )
