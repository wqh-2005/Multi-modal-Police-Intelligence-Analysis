"""知识存储服务 (模块三)。

将三元组（格式 1.3）写入 Neo4j 图数据库，产出格式 1.4。

由于格式 1.3 不包含显式实体列表，实体从三元组中推导：
    - 收集三元组中所有唯一的 subject / object 名称作为节点
    - 根据名称模式推断实体类型（手机号→PHONE，身份证→ID_CARD 等）

核心流程:
    1. _derive_entities : 从三元组推导实体（名称 + 类型）
    2. _create_entities : MERGE 实体节点（按 name 去重）
    3. _create_relations: MATCH 头尾节点 + MERGE 关系边
    4. _infer_persons   : 启发式推断受害者/嫌疑人
    5. 组装 GraphStorageOutput (格式 1.4)

对外接口:
    run_storage(data: ExtractionOutput) -> GraphStorageOutput (格式 1.4)

下游对接:
    智能研判模块（模块四）
"""
import json
import re
from logging import getLogger

from neo4j import AsyncGraphDatabase

from app.config.settings import neo4j_settings
from app.models.knowledge_schema import (
    ExtractionOutput,
    GraphStorageOutput,
    Victim,
    Suspect,
    Relation,
    Transaction,
)

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# Neo4j 连接池（单例，模块级别复用）
# ---------------------------------------------------------------------------
_driver_instance = None


def _get_driver():
    """获取 Neo4j 异步驱动单例。

    首次调用时创建连接池，后续调用返回同一实例。
    连接池大小由 URI 中的 max_connection_pool_size 参数控制，默认 10。

    如果检测到驱动已失效（事件循环变更导致 transport 为 None），
    自动重建驱动以兼容 TestClient 等场景。

    Returns:
        neo4j.AsyncDriver: Neo4j 异步驱动实例。
    """
    global _driver_instance
    if _driver_instance is None:
        _driver_instance = _create_driver()
    else:
        # 检测驱动是否已失效（TestClient 事件循环变更场景）
        try:
            # 尝试获取连接池状态，失败则重建
            if _driver_instance._pool is None:
                raise RuntimeError("pool is None")
        except Exception:
            logger.info("Neo4j 驱动已失效，重建连接池")
            _driver_instance = _create_driver()
    return _driver_instance


def _create_driver():
    """创建新的 Neo4j 异步驱动实例。"""
    driver = AsyncGraphDatabase.driver(
        neo4j_settings.NEO4J_URI,
        auth=(neo4j_settings.NEO4J_USER, neo4j_settings.NEO4J_PASSWORD),
        max_connection_pool_size=10,
        connection_acquisition_timeout=10,
    )
    logger.info("Neo4j 连接池已创建 (pool_size=10)")
    return driver


def _reset_driver():
    """重置 Neo4j 驱动（仅测试用）。"""
    global _driver_instance
    if _driver_instance is not None:
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(_driver_instance.close())
        except Exception:
            pass
        _driver_instance = None


def _infer_entity_type(name):
    """根据名称模式推断实体类型（正则兜底）。

    优先使用 LLM 在 Triplet 中给出的 subject_type / object_type，
    仅当 LLM 未提供类型时才走此规则匹配。

    仅保留无歧义的正则：手机号、身份证、URL。
    其他类型由 LLM 判断。

    Args:
        name: 实体名称。

    Returns:
        str: 实体类型标签，无法判断时返回 "OTHER"。
    """
    if re.fullmatch(r"1[3-9]\d{9}|1[3-9]\d{2}\*{4}\d{4}", name):
        return "PHONE"
    if re.fullmatch(r"\d{17}[\dXx]|\d{6}\*{8}\d{4}", name):
        return "ID_CARD"
    if re.match(r"^https?://", name):
        return "URL"
    return "OTHER"


# 非人格化词汇（不应被选为受害者/嫌疑人）
_NON_PERSON_WORDS = {
    "链接", "验证码", "填写验证码", "案件信息", "案件详情", "依法处置",
    "账户", "安全账户", "安全核查", "APP", "app", "信息", "资金", "案件",
    "通知", "警方", "风险", "身份", "银行",
    # 被冒充的公职身份：本案语境中不是自然人受害者（假身份）
    "民警", "警官", "警察", "公安", "公检法",
}
# 明确的角色标识（用于策略 1 结果校验）
_ROLE_PATTERNS = re.compile(
    r"^(A|B|C|D|[甲乙丙丁]|嫌疑人?|受害者?|骗子|您|你|我|他|她)$"  # 角色标识/代词
    r"|^.{1,4}(警官|先生|女士|同志|老师|经理|老板)$"              # 称谓
    r"|^.{1,3}公司$"                                           # 公司名
    r"|^(报警人|报案人|受害人|被害人|当事人|事主|举报人)$"           # 案件角色
    r"|^(对方|案犯|客服|客服人员|组织者|群主)$"                     # 通用嫌疑人指代
)

# 名称语义强指示：这些词明确标识受害/嫌疑身份
_VICTIM_NAME_HINT = re.compile(r"被害|受害|事主|报警|报案|被骗")
_SUSPECT_NAME_HINT = re.compile(r"案犯|嫌疑")


def _is_person_candidate(name):
    """判断实体是否可能是真实人物/机构。

    排除已知的非人格化词汇和过长字符串。

    Args:
        name: 实体名称。

    Returns:
        bool: 是否可能是人物/机构角色。
    """
    if name in _NON_PERSON_WORDS:
        return False
    if len(name) > 6:
        return False
    return True


def _is_likely_person(name):
    """判断实体是否极可能是人物（严格模式）。

    用于策略 1 结果的可信度校验。必须是角色标识或典型称谓。

    Args:
        name: 实体名称。

    Returns:
        bool: 是否极可能是真实人物。
    """
    if not _is_person_candidate(name):
        return False
    return bool(_ROLE_PATTERNS.search(name))


# 嫌疑人角色指代词：即使名称较长（超过人名长度上限）也视为嫌疑人候选
_SUSPECT_ROLE_WORDS = (
    "客服", "对方", "案犯", "骗子", "导师", "经理", "专家", "嫌疑",
    "组织者", "群主", "陌生", "诈骗", "工作人员", "业务员",
)


def _is_suspect_like(name):
    """判断实体名称是否带明确的施骗方角色指示。

    用于放宽嫌疑人候选：LLM 可能输出"APP内客服人员"这类
    超过人名长度上限但仍明确是施骗方的实体。

    Args:
        name: 实体名称。

    Returns:
        bool: 是否带施骗方角色指示。
    """
    if len(name) > 12:
        return False
    return any(w in name for w in _SUSPECT_ROLE_WORDS)


# Cypher 标识符（节点标签/关系类型）白名单：仅 中文/ASCII字母/数字/下划线
# 不用 \w（含全角变体，可经 NFKC 规范化出 `` ` ``/`{` 等语法字符），
# 显式收窄，作为纵深防御
_CYPHER_IDENTIFIER = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]{1,20}$")


def _safe_cypher_identifier(value, fallback):
    """校验并清洗将拼入 Cypher 标签位置的标识符。

    Args:
        value: LLM 输出的原始标识符（类型或关系名）。
        fallback: 校验失败时的降级值（如 "OTHER" 或 None 表示跳过）。

    Returns:
        str | None: 通过白名单的原值，否则 fallback。
    """
    if isinstance(value, str) and _CYPHER_IDENTIFIER.fullmatch(value):
        return value
    return fallback


def _derive_entities(triplets):
    """从三元组推导实体列表。

    收集所有 subject / object 去重。类型优先取 LLM 标注的
    subject_type / object_type，无标注时走正则兜底。

    Args:
        triplets: Triplet 列表。

    Returns:
        list[dict]: 实体列表，每个为 {"name": str, "type": str}。
    """
    seen = {}
    for t in triplets:
        for name, llm_type in ((t.subject, t.subject_type), (t.object, t.object_type)):
            if name not in seen:
                seen[name] = llm_type if llm_type else _infer_entity_type(name)
    return [{"name": name, "type": etype} for name, etype in seen.items()]


async def _create_entities(driver, entities):
    """遍历实体列表，写入 Neo4j 节点。

    策略: MERGE 按名称去重，新建时写时间戳。

    Args:
        driver: Neo4j 异步驱动。
        entities: 实体 dict 列表。

    Returns:
        int: 处理的实体数量。
    """
    count = 0
    async with driver.session() as session:
        for e in entities:
            safe_type = _safe_cypher_identifier(e["type"], "OTHER")
            await session.run(
                f"MERGE (n:`{safe_type}` {{name: $name}}) "
                "ON CREATE SET n.created_at = timestamp()",
                name=e["name"],
            )
            count += 1
    return count


async def _create_relations(driver, triplets):
    """将三元组写入 Neo4j 关系边。

    策略: MATCH 头尾节点 → MERGE 关系。relation 字段直接作为关系类型名。

    Args:
        driver: Neo4j 异步驱动。
        triplets: Triplet 列表。

    Returns:
        int: 成功写入的关系边数量。
    """
    count = 0
    async with driver.session() as session:
        for t in triplets:
            # 关系类型会拼入 Cypher 标签位置，白名单校验失败则跳过该关系
            safe_rel = _safe_cypher_identifier(t.relation.replace(" ", "_"), None)
            if safe_rel is None:
                logger.warning("跳过非法关系类型: len=%d", len(t.relation))
                continue
            await session.run(
                f"""
                MATCH (a {{name: $subject}})
                MATCH (b {{name: $object}})
                MERGE (a)-[r:`{safe_rel}`]->(b)
                """,
                subject=t.subject,
                object=t.object,
            )
            count += 1
    return count


def _build_type_map(triplets):
    """从三元组的 LLM 标注构建 name→type 映射。

    同一实体可能在多个三元组中出现，优先取 LLM 明确标注的类型。

    Args:
        triplets: Triplet 列表。

    Returns:
        dict[str, str]: name → type 映射。
    """
    type_map = {}
    for t in triplets:
        if t.subject_type:
            type_map[t.subject] = t.subject_type
        if t.object_type:
            type_map[t.object] = t.object_type
    return type_map


def _infer_persons(triplets):
    """从三元组推断受害者与嫌疑人。

    启发式规则（按优先级）:
        策略0: 攻击/施害关系（冒充、诱导、威胁、要求转账、拉入群聊、骗取等）
               的 subject 是嫌疑人，object 是被攻击者（受害者候选）；
               "冒充"的 object 是被冒用身份（假身份），不作为受害者。
        策略1: 纯统计兜底——纯主动方为嫌疑人；被作用最多的为受害者。
        策略2: 双向交互（威胁/要求/冒充对方）。
        策略3: 关系多的一方是嫌疑人。
        策略4: 名称语义纠正（被害/受害/报警/被骗 强指示受害身份）。
        策略5: 单侧结果按名称语义纠正。

    全程确定性选择：多候选时按 (出现次数, 语义提示) 排序取最优，
    不使用 set.pop()，避免受 hash 随机化影响。

    Args:
        triplets: Triplet 列表。

    Returns:
        (Victim, Suspect): 受害者与嫌疑人信息元组。
    """
    from collections import Counter

    # 直接从 LLM 类型标注收集人物（不依赖 type_map 闭包）
    person_set = {
        t.subject for t in triplets if t.subject_type == "PERSON"
    } | {
        t.object for t in triplets if t.object_type == "PERSON"
    }
    persons = {n for n in person_set if _is_person_candidate(n)}

    subj_count = Counter(t.subject for t in triplets if t.subject_type == "PERSON")
    obj_count = Counter(t.object for t in triplets if t.object_type == "PERSON")

    suspects = set()
    victims = set()

    # 攻击/施害类关系：subject 是施害者，object 是被攻击者
    # 注意：冒充 的 object 是被冒用的身份（假身份），不算受害者
    _AGGRESSIVE = {
        "冒充", "诱导", "威胁", "恐吓", "哄骗", "欺骗", "诈骗", "骗取",
        "设局", "拉入群聊", "拉进群聊", "指挥", "指导", "唆使", "教唆",
        "拨打电话", "自称",
    }

    def _is_aggressive(rel):
        return rel in _AGGRESSIVE or rel == "要求" or rel.startswith("要求")

    attacker_count = Counter(
        t.subject for t in triplets
        if _is_aggressive(t.relation)
        and (t.subject in persons or _is_suspect_like(t.subject))
    )
    victim_count = Counter(
        t.object for t in triplets
        if _is_aggressive(t.relation)
        and t.relation not in ("冒充", "自称")  # 被冒用/自称的身份是假身份，非受害者
        and t.object in persons
    )

    # ---- 策略0：攻击关系优先 ----
    if attacker_count:
        # 含受害语义提示（报警/被害/被骗等）的人不作嫌疑人
        pool = Counter({n: c for n, c in attacker_count.items()
                        if not _VICTIM_NAME_HINT.search(n)})
        if pool:
            suspects = {pool.most_common(1)[0][0]}
    if victim_count:
        ranked_victims = sorted(
            victim_count,
            key=lambda n: (
                victim_count[n],
                1 if _VICTIM_NAME_HINT.search(n) else 0,
                1 if _is_likely_person(n) else 0,
            ),
            reverse=True,
        )
        for cand in ranked_victims:
            if cand not in suspects:
                victims.add(cand)
                break
        # 若被攻击者全部是嫌疑人（罕见）：不强行设受害者，
        # 避免 victim==suspect 同名，交给后续策略处理

    # ---- 策略1：纯统计兜底（攻击关系不足时） ----
    if not suspects:
        # 含受害语义提示（报警/被害/被骗等）的人优先归为受害者，不作嫌疑人
        suspect_pool = {n for n in persons
                        if not _VICTIM_NAME_HINT.search(n)} | {
            n for n in person_set if _is_suspect_like(n)
        }
        pure_actors = {
            n for n in suspect_pool
            if subj_count.get(n, 0) > 0 and obj_count.get(n, 0) == 0
        }
        if pure_actors:
            suspects = {max(pure_actors, key=lambda n: (subj_count.get(n, 0), n))}
        elif subj_count:
            pool = suspect_pool & set(subj_count)
            if pool:
                best = max(pool, key=lambda n: (subj_count[n] - obj_count.get(n, 0), n))
                if subj_count[best] > obj_count.get(best, 0):
                    suspects = {best}
    if not victims and obj_count:
        ranked_victims = sorted(
            obj_count,
            key=lambda n: (
                obj_count.get(n, 0),
                1 if _VICTIM_NAME_HINT.search(n) else 0,
                1 if _is_likely_person(n) else 0,
            ),
            reverse=True,
        )
        for cand in ranked_victims:
            if cand not in suspects:
                victims.add(cand)
                break

    # 被动人物不足时：取含受害语义提示（报警/被害/被骗等）的人
    # 覆盖"纯受害者操作"场景：如 报警人-转账->5000元（object 是金额非人）
    if not victims:
        hinted = sorted(
            (n for n in persons if _VICTIM_NAME_HINT.search(n)),
            key=lambda n: (subj_count.get(n, 0) + obj_count.get(n, 0), n),
            reverse=True,
        )
        if hinted:
            victims.add(hinted[0])

    # ---- 策略2：双向交互（无攻击关系但有人物交互） ----
    if not suspects and not victims:
        aggressive = {"威胁", "要求", "要求下载", "要求转账", "要求填写", "冒充", "自称", "诱导"}
        for t in triplets:
            if t.relation in aggressive and t.subject in persons and t.object in persons:
                suspects.add(t.subject)
                victims.add(t.object)

    # ---- 策略3：均无 → 关系多的一方是嫌疑人 ----
    if not suspects and not victims:
        suspect_pool = {n for n in persons
                        if not _VICTIM_NAME_HINT.search(n)} | {
            n for n in person_set if _is_suspect_like(n)
        }
        subj_pool = Counter(t.subject for t in triplets if t.subject in suspect_pool)
        if subj_pool:
            suspects = {subj_pool.most_common(1)[0][0]}
            victims = persons - suspects

    # ---- 策略4（角色纠正）：名称语义纠正，在过滤前执行 ----
    # 若 suspects 含受害语义（报警/被害/被骗）而 victims 不含 → 交换角色，
    # 避免 LLM 把受害者标为施害方时被过滤剔除导致双方落"未知"
    if suspects and victims:
        suspect_is_victim = any(_VICTIM_NAME_HINT.search(n) for n in suspects)
        victim_is_victim = any(_VICTIM_NAME_HINT.search(n) for n in victims)
        if suspect_is_victim and not victim_is_victim:
            suspects, victims = victims, suspects  # 交换角色

    # ---- 策略5：单侧 → 按名称语义纠正 ----
    if suspects and not victims:
        only = max(suspects, key=lambda n: (subj_count.get(n, 0),
                                            1 if _SUSPECT_NAME_HINT.search(n) else 0, n))
        if _VICTIM_NAME_HINT.search(only):
            victims, suspects = {only}, set()
    elif victims and not suspects:
        only = max(victims, key=lambda n: (obj_count.get(n, 0),
                                           1 if _VICTIM_NAME_HINT.search(n) else 0, n))
        if _SUSPECT_NAME_HINT.search(only):
            suspects, victims = {only}, set()

    # ---- 校验：过滤明显非人 / 语义冲突（普通人名保留） ----
    victims = {
        v for v in victims
        if _is_person_candidate(v) and not _SUSPECT_NAME_HINT.search(v)
    }
    suspects = {
        s for s in suspects
        if (_is_person_candidate(s) or _is_suspect_like(s))
        and not _VICTIM_NAME_HINT.search(s)
    }

    # ---- 最终兜底：已识别嫌疑人但无受害者时，用通用角色词补位 ----
    # 覆盖原文未点名受害者的场景（如"要求转账到安全账户5万元"），
    # 避免 victim 落入"未知"而丢失案件主体信息
    if suspects and not victims:
        victims = {"受害人"}

    # 确定性选择：多候选时取 (次数, 语义提示) 最优者，避免 set.pop 随机
    def _pick(items, counter, hint):
        if not items:
            return "未知"
        return max(items, key=lambda n: (counter.get(n, 0), 1 if hint.search(n) else 0, n))

    victim_name = _pick(victims, obj_count, _VICTIM_NAME_HINT)
    suspect_name = _pick(suspects, subj_count, _SUSPECT_NAME_HINT)

    return Victim(name=victim_name), Suspect(name=suspect_name)


def _build_relations(triplets):
    """三元组 → Relation 列表（格式 1.4 字段名：from / type / to）。

    Args:
        triplets: Triplet 列表。

    Returns:
        list[Relation]: Relation 模型列表。
    """
    return [
        Relation(from_entity=t.subject, type=t.relation, to_entity=t.object)
        for t in triplets
    ]


# 金额模式：匹配 "99元", "50000", "12万元", "5万", "100万美元", "100万韩元" 等
# 第二组捕获"万"用于换算；第三组为货币后缀（美元/韩元/日元/欧元/元 等）
_AMOUNT_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*(万)?\s*(?:[^\s]{0,3}元)?$")

# 指示/要求类关系：subject 是收款方的强信号
_DEMAND_RELATIONS = {"要求转账", "指示", "诱导", "要求", "要求下载", "要求填写", "让充值"}


def _infer_payee(triplets, payer):
    """从三元组上下文推断付款方的收款人。

    启发式：
        1. 谁在"要求转账"/"指示"付款方 → 收款方
        2. 纯主动方（只作 subject 不作 object 的人）→ 最可能的收款方

    Args:
        triplets: 全部三元组。
        payer: 付款方名称。

    Returns:
        str: 推断的收款方名称，无法推断时返回 "未知收款方"。
    """
    # 策略 1：找出对 payer 有指示/要求行为的人
    candidates = set()
    for t in triplets:
        if t.relation in _DEMAND_RELATIONS and t.object == payer:
            candidates.add(t.subject)
    if len(candidates) == 1:
        return candidates.pop()
    if candidates:
        from collections import Counter
        return Counter(
            t.subject for t in triplets
            if t.relation in _DEMAND_RELATIONS and t.object == payer
        ).most_common(1)[0][0]

    # 策略 2：纯主动方（只作 subject 不作 object）中最活跃的
    subjects = {t.subject for t in triplets}
    objects = {t.object for t in triplets}
    pure_actors = subjects - objects
    # 排除 payer 自己和非人格化词
    pure_actors = {
        n for n in pure_actors
        if n != payer and _is_person_candidate(n)
    }
    if pure_actors:
        from collections import Counter
        subj_count = Counter(t.subject for t in triplets)
        return max(pure_actors, key=lambda n: (subj_count.get(n, 0), n))

    return "未知收款方"


# 转账类关系关键词，用于识别交易记录
_TRANSFER_KEYWORDS = ("转账", "充值", "投入")


def _is_transfer_relation(relation):
    """判断关系是否为实际的转账/交易操作（排除要求类行为）。

    Args:
        relation: 关系名称。

    Returns:
        bool: 是否为实际交易操作。
    """
    if relation.startswith("要求"):
        return False
    return any(kw in relation for kw in _TRANSFER_KEYWORDS)


def _build_transactions(triplets):
    """从三元组中筛选转账类关系，组装 Transaction 列表。

    LLM 抽取的转账三元组中，object 通常是金额数字而非收款方。
    因此从 object 中解析金额，并从上下文推断收款方。

    Args:
        triplets: Triplet 列表。

    Returns:
        list[Transaction]: 资金交易记录。
    """
    transactions = []
    for t in triplets:
        if not _is_transfer_relation(t.relation):
            continue

        m = _AMOUNT_PATTERN.match(t.object.strip())
        if m:
            # object 是金额，解析数值（"万"单位换算为元），从上下文推断收款方
            amount = float(m.group(1))
            if m.group(2):  # 含"万"单位 → 换算为元（round 消除浮点误差）
                amount = round(amount * 10000, 2)
            to_entity = _infer_payee(triplets, t.subject)
        else:
            # object 是人物/账户
            amount = 0.0
            to_entity = t.object

        transactions.append(Transaction(
            from_entity=t.subject,
            to_entity=to_entity,
            amount=amount,
        ))
    return transactions


def _truncate_text(text, max_len=800):
    """智能截断文本，在句子边界截断并保留关键信息。

    优先保留含金额、账户、平台等关键信息的末尾段落。

    Args:
        text: 原始文本。
        max_len: 最大字符数。

    Returns:
        str: 截断后的文本。
    """
    if len(text) <= max_len:
        return text

    # 关键信息模式
    key_pattern = re.compile(r"(\d+\.?\d*[万元]|转账|充值|账户|APP|平台|二维码|链接|验证码)")

    # 在句子边界截断
    sentences = re.split(r"(?<=[。！？；\n])", text)
    result = ""
    for i, s in enumerate(sentences):
        if len(result) + len(s) > max_len:
            remaining = "".join(sentences[i:])
            if key_pattern.search(remaining):
                # 末尾有关键信息，保留尾部
                tail = remaining
                if len(tail) > max_len // 2:
                    tail = "…" + tail[-(max_len // 2):]
                result = result[:max_len - len(tail)] + tail
            break
        result += s
    return result


async def run_storage(data):
    """模块三主函数：将三元组写入 Neo4j 图数据库。

    流程:
        1. 连接 Neo4j，验证连通性
        2. 从三元组推导实体 + 写入节点
        3. 写入关系边
        4. 推断受害者/嫌疑人
        5. 组装格式 1.4 返回

    Args:
        data: ExtractionOutput（格式 1.3），来自 extraction_service。

    Returns:
        GraphStorageOutput: 格式 1.4，供模块四（智能研判）消费。

    Raises:
        neo4j.exceptions.ServiceUnavailable: Neo4j 连接不可用时。
    """
    logger.info("知识存储开始, case_id=%s", data.case_id)

    driver = _get_driver()
    await driver.verify_connectivity()
    logger.info("Neo4j 连接成功")

    entities = _derive_entities(data.triplets)
    node_count = await _create_entities(driver, entities)
    rel_count = await _create_relations(driver, data.triplets)

    victim, suspect = _infer_persons(data.triplets)
    relations = _build_relations(data.triplets)
    transactions = _build_transactions(data.triplets)

    logger.info("存储完成: nodes=%d, relations=%d", node_count, rel_count)

    # 保存案件记录节点（供历史记录查询）
    try:
        await _upsert_case_node(
            driver,
            case_id=data.case_id,
            chat_history=_truncate_text(data.raw_text),
            victim=victim.name if victim else "",
            suspect=suspect.name if suspect else "",
            deepfake_alert=data.deepfake_alert,
        )
    except Exception as e:
        logger.warning("案件记录节点写入失败: %s", e)

    return GraphStorageOutput(
        victim=victim,
        suspect=suspect,
        relations=relations,
        transactions=transactions,
        chat_history=_truncate_text(data.raw_text),
        deepfake_alert=data.deepfake_alert,
        case_id=data.case_id,
    )


async def _upsert_case_node(
    driver,
    case_id: str,
    chat_history: str,
    victim: str,
    suspect: str,
    deepfake_alert: bool,
) -> None:
    """创建/更新案件记录节点（供历史记录查询）。

    以 case_id 为主键 MERGE，创建时写 created_at，之后仅更新内容字段。
    """
    await driver.execute_query(
        """
        MERGE (c:Case {case_id: $case_id})
        ON CREATE SET c.created_at = timestamp()
        SET c.chat_history = $chat_history,
            c.victim = $victim,
            c.suspect = $suspect,
            c.deepfake_alert = $deepfake_alert,
            c.updated_at = timestamp()
        """,
        case_id=case_id,
        chat_history=chat_history,
        victim=victim,
        suspect=suspect,
        deepfake_alert=deepfake_alert,
    )


def _parse_judgment(raw):
    """将 Neo4j 中 JSON 字符串形式的研判结果解析为 dict；无数据时返回 None。"""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.debug("研判结果 JSON 解析失败: %.200s", raw)
        return None


async def list_case_history(driver, limit: int = 50) -> list:
    """查询历史案件记录（按最近更新时间倒序，更新时间相同按 case_id）。

    返回: [{case_id, created_at, chat_history, victim, suspect, deepfake_alert, judgment}]
    """
    result = await driver.execute_query(
        """
        MATCH (c:Case)
        RETURN c.case_id AS case_id,
               c.created_at AS created_at,
               c.chat_history AS chat_history,
               c.victim AS victim,
               c.suspect AS suspect,
               c.deepfake_alert AS deepfake_alert,
               c.judgment AS judgment
        ORDER BY coalesce(c.updated_at, c.created_at) DESC, c.case_id DESC
        LIMIT $limit
        """,
        limit=limit,
    )
    cases = []
    for record in result.records:
        cases.append(
            {
                "case_id": record["case_id"],
                "created_at": record["created_at"],
                "chat_history": record["chat_history"],
                "victim": record["victim"],
                "suspect": record["suspect"],
                "deepfake_alert": record["deepfake_alert"],
                "judgment": _parse_judgment(record["judgment"]),
            }
        )
    return cases


async def get_case_history(driver, case_id: str):
    """查询单个案件完整记录（含研判结果）。

    返回: dict 或 None（案件不存在时）。
    """
    result = await driver.execute_query(
        """
        MATCH (c:Case {case_id: $case_id})
        RETURN c.case_id AS case_id,
               c.created_at AS created_at,
               c.chat_history AS chat_history,
               c.victim AS victim,
               c.suspect AS suspect,
               c.deepfake_alert AS deepfake_alert,
               c.judgment AS judgment
        """,
        case_id=case_id,
    )
    if not result.records:
        return None
    record = result.records[0]
    return {
        "case_id": record["case_id"],
        "created_at": record["created_at"],
        "chat_history": record["chat_history"],
        "victim": record["victim"],
        "suspect": record["suspect"],
        "deepfake_alert": record["deepfake_alert"],
        "judgment": _parse_judgment(record["judgment"]),
    }


async def update_case_judgment(driver, case_id: str, judgment: dict) -> None:
    """流水线研判完成后，将研判结果写入案件记录节点。

    仅更新已存在的 :Case 节点（MATCH），避免存储阶段失败时创建
    只有 judgment 的孤儿节点。Neo4j 属性不支持嵌套 Map，
    故将 judgment 序列化为 JSON 字符串存储。
    """
    await driver.execute_query(
        """
        MATCH (c:Case {case_id: $case_id})
        SET c.judgment = $judgment,
            c.updated_at = timestamp()
        """,
        case_id=case_id,
        judgment=json.dumps(judgment, ensure_ascii=False, default=str),
    )
