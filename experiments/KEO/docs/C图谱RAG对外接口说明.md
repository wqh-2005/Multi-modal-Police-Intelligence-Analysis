# C 图谱 RAG 对外接口说明

本文供A数据处理、B公共回答模块和D测试评价负责人对接C图谱RAG使用。依据团队流程图及同目录《数据结构与交付说明.md》，只约定外部输入、输出和交接规则，不要求其他成员了解图谱内部节点、向量库或最大生成树实现。

本文是接口开发约定，不表示检索调用入口已经实现。示例均为虚构数据，不代表实验结果。

## 一 C负责什么

```text
A：案例数据、三元组数据、划分名单 → C：建立图谱和节点索引
D：questions.jsonl             → C：检索图谱
C：格式1.3检索结果             → B维护的公共回答模块
公共回答模块：answers.jsonl     → D：评分和比较
```

C负责建图、实体检索、邻居扩展、最大生成树筛选和上下文重建。C向公共回答模块交付知识上下文与原文证据，不单独生成最终答案，也不计算最终评价成绩。

与图中标签对应：本文件将“检索结果（中间格式1.3）”明确为C的直接输出；图中`answer.jsonl`统一沿用总接口的`answers.jsonl`命名，由公共回答模块产生。划分文件采用总接口约定的JSON对象，不将图中的“划分名单”标签另解释为逐行JSONL。

## 二 交接清单

| 交付方 | 接收方 | 文件或记录 | 格式 | 内容 |
| --- | --- | --- | --- | --- |
| A | C | `案例数据.json` | JSON数组 | 案例ID、完整原文、诈骗类别 |
| A | C | `三元组数据.jsonl` | 每行一个对象 | 清洗后的原有三元组及证据 |
| A | C | `划分名单.json` | JSON对象 | build_ids与eval_ids |
| D | C及B | `questions.jsonl` | 每行一个对象 | 完全相同的问题输入 |
| C | B公共回答模块 | 单条检索结果，批量时建议`graph_retrieval_results.jsonl` | 格式1.3 | 上下文、证据、状态及耗时 |
| 公共回答模块 | D | `answers.jsonl` | 格式1.4 | 最终答案及实际检索上下文 |

文件均使用UTF-8。JSONL示例为便于阅读展开显示，实际文件每行保存一个完整对象。输入和结果目录由配置指定，不在业务字段中写死；C本地小样本及图谱产物可保存在`experiments/KEO/output/test`。graph.json、节点向量和原实体映射属于C内部产物，不要求B、D读取。

## 三 A向C交付的数据 格式1.1

### 3.1 案例数据

```json
[
  {
    "id": "case_000001",
    "案情描述": "对方冒充客服，要求受害人下载云视讯。",
    "案件类别": "虚假征信类"
  },
  {
    "id": "case_000002",
    "案情描述": "对方以贷款验资为由，要求报警人向指定账户付款。",
    "案件类别": "虚假贷款类"
  }
]
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | string | 是 | 原案例ID，不重新编号 |
| 案情描述 | string | 是 | 完整原文，不追加真实类别或评分提示 |
| 案件类别 | string | 是 | 统一类别集合中的原有类别 |

### 3.2 三元组数据

```json
{
  "triple_id": "case_000001_T001",
  "source_id": "case_000001",
  "source_type": "案例",
  "fraud_category": "虚假征信类",
  "head": {
    "id": "case_000001_E001",
    "name": "对方",
    "type": "人员",
    "role": "疑似诈骗者"
  },
  "relation": "要求下载",
  "tail": {
    "id": "case_000001_E002",
    "name": "云视讯",
    "type": "应用程序",
    "role": null
  },
  "factuality": "被要求执行",
  "evidence": [
    {
      "unit_id": "case_000001_clause_001",
      "text": "对方冒充客服，要求受害人下载云视讯。"
    }
  ],
  "confidence": 0.95,
  "status": "待人工审核"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| triple_id | string | 是 | 原三元组编号；C当前采用本项目全局唯一编号 |
| source_id | string | 是 | 对应案例数据的id |
| source_type | string | 是 | 本次使用“案例” |
| fraud_category | string | 是 | 与对应案例类别一致 |
| head、tail | Entity | 是 | 原有头尾实体，保留方向 |
| relation | string | 是 | 规范化关系名称 |
| factuality | string | 是 | 明确发生、被要求执行等原事实状态 |
| evidence | EvidenceUnit[] | 是 | 非空的原文证据列表 |
| confidence | number或null | 否 | 原模型分数，不等于人工正确率 |
| status | string或null | 否 | 原审核状态，建图不自动变更 |

| 子结构 | 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| Entity | id | string | 是 | 原实体编号；当前项目直接使用已有全局唯一实体ID |
| Entity | name | string | 是 | 清洗后的名称，允许UNKNOWN等已有缺失标记 |
| Entity | type | string | 是 | 人员、身份角色、应用程序等类型 |
| Entity | role | string或null | 否 | 保留已有角色，如疑似诈骗者、受害人 |
| EvidenceUnit | unit_id | string | 是 | 原文语句单元编号 |
| EvidenceUnit | text | string | 是 | 支持关系的原文证据，不是模型补写的解释 |

同一原实体参与多条三元组时复用相同实体ID，这是正常情况；同一ID出现矛盾内容须在交接时处理。三元组ID不能重复。总接口原本允许source_id与triple_id组合唯一；此处明确C当前直接使用本项目原有全局唯一triple_id，A若改变编号方式须先与C对齐。

### 3.3 划分名单

```json
{
  "build_ids": ["case_000001"],
  "eval_ids": ["case_000002"]
}
```

正式划分约60%用于建库、40%用于评估；示例仅说明字段，不表示正式比例。两个数组内ID不重复、相互不重叠，所有ID存在于案例数据；重复案例不得跨侧。

B、C使用同一份build_ids。C仅将该名单内的原文和三元组加入图谱与索引；eval_ids不进入知识库。无三元组的建库案例由A记录说明，C不擅自改变共同划分。

### 3.4 A与C交接时确认

- 原文、类别和三元组按source_id正确对应，原编号保持稳定。
- A按约定完成字段、重复项及概念和关系规范化；C不要求另交ARETO格式。
- 人员原角色与已有冒充关系直接复用；不同案件的人物不因称呼相同合并。相同身份、明确机构和APP概念可在C内部共享。
- 人员role为疑似诈骗者时，C内部归类为诈骗犯，保留原role，不要求A改为“诈骗犯”。
- C接收数据后检查结构和来源；抽样核验结果不等于每条记录已经人工审核，A无需批量改为审核完成。
- A应说明缺失结果、零三元组、隔离记录及使用的数据版本；这些说明不增加为每条三元组的强制字段。

## 四 D向C提供问题 格式1.2

```json
{
  "question_id": "q001",
  "question_type": "case_judgment",
  "question": "有人自称客服，要求我下载投屏软件并进行贷款验资。请判断诈骗类型、指出依据并提出应对措施。"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| question_id | string | 是 | 唯一问题编号，C原样回传 |
| question_type | string | 是 | global_analysis或case_judgment |
| question | string | 是 | 完整问题；案例题包含待分析正文 |

D向三种方法提供相同问题。案例题的待分析正文可以包含在问题中，但其真实类别、参考措施、出题来源ID及统计摘要不随问题交给C。

`question_metadata.jsonl`仅供D内部评价使用，C和公共回答模块不读取。C不需要D提供种子节点、实体清单或图路径。

## 五 C向公共回答模块提供结果 格式1.3

### 5.1 成功结果

```json
{
  "question_id": "q001",
  "method": "graph_rag",
  "status": "ok",
  "context": "[E001] 历史案例case_000001中，对方冒充客服，要求受害人下载云视讯。该记录表达下载要求，不能据此认定已经下载。",
  "evidence": [
    {
      "evidence_id": "E001",
      "source_id": "case_000001",
      "text": "对方冒充客服，要求受害人下载云视讯。",
      "triple_ids": ["case_000001_T001"]
    }
  ],
  "elapsed_ms": 120,
  "error": null
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| question_id | string | 是 | 回显D的问题ID |
| method | string | 是 | C固定输出graph_rag |
| status | string | 是 | ok、empty或error |
| context | string | 是 | 实际供回答模型使用的文字上下文 |
| evidence | RetrievedEvidence[] | 是 | 对应上下文的原文证据 |
| elapsed_ms | number | 是 | 非负检索耗时，单位毫秒；不含离线建图和回答生成时间 |
| error | Error或null | 是 | 成功、无结果为null，失败填写错误对象 |

| RetrievedEvidence字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| evidence_id | string | 是 | 本次结果内唯一，如E001，供上下文和回答引用 |
| source_id | string | 是 | 证据所属案例，必须属于build_ids |
| text | string | 是 | 实际使用的原文证据 |
| triple_ids | string[] | 否 | 与该证据关联的原三元组ID，C建议提供 |

同一个evidence_id只在当前问题的检索结果中唯一，不要求全库唯一。多条关系使用同一证据时可复用证据编号并累计triple_ids。context保留关系方向、事实状态和案件来源，不将跨案路径写成同一人或同一事件。证据text不改写，context可以组织为可读说明。

### 5.2 无结果

```json
{
  "question_id": "q002",
  "method": "graph_rag",
  "status": "empty",
  "context": "",
  "evidence": [],
  "elapsed_ms": 35,
  "error": null
}
```

empty表示检索正常执行但没有可用知识，不表示问题不存在诈骗或知识库已经证明某个否定结论。公共回答模块接收该状态并按共同约定生成回答，不私自切换为文本RAG。

### 5.3 检索失败

```json
{
  "question_id": "q003",
  "method": "graph_rag",
  "status": "error",
  "context": "",
  "evidence": [],
  "elapsed_ms": 50,
  "error": {
    "type": "RETRIEVAL_ERROR",
    "message": "节点向量索引不可用"
  }
}
```

Error的type和message均为字符串。可使用INVALID_INPUT、RETRIEVAL_ERROR等错误类型，message不包含密钥。失败不伪装为空结果，也不提供未完成的部分上下文作为成功结果。

## 六 B如何接收C的结果

1. 将相同的问题记录分别交给文本检索与图谱检索。
2. C返回格式1.3，B公共回答模块使用原question与C的context，并按共同约定提供证据引用。
3. 三种方法共用回答模型、任务要求、输出解析方式和预先约定的上下文预算。
4. 公共回答模块生成格式1.4，保留实际使用的检索结果，写入answers.jsonl交D。

C不向公共回答模块返回predicted_category、最终措施或评分。类别预测由回答模块产生；内部存储的历史案件类别是否进入上下文，需B、C共同约定，不能只有图谱RAG额外获得类别提示。

格式1.4中的retrieval取C结果的以下五个字段：status、context、evidence、elapsed_ms、error。外层question_id、method、run_id和model_id按总接口保存。

若检索失败阻止生成，公共回答模块仍保存失败回答记录：外层status=error、answer_text为空、predicted_category=null、cited_evidence_ids为空、parse_status=not_applicable、generation_elapsed_ms=0，并保留retrieval.error。检索失败不能当作没有调用图谱方法。

图中“直接回答”由B公共回答流程处理，不经过C检索器；其retrieval.status=not_applicable，按总接口保存。

## 七 调用方式与批量交付

本轮以文件交付或同进程函数调用为主，不要求HTTP服务。建议检索入口的逻辑签名为：

```python
retrieve(question_record: dict) -> dict
```

输入为格式1.2，返回格式1.3。实际模块路径、类名、同步或异步方式在检索实现后与B确认；此处不宣称已有可调用函数，也不要求B直接调用GraphBuilder。

批量模式读取questions.jsonl，按question_id关联结果，每个有效问题保留一条成功、无结果或失败记录。重复question_id或缺失问题ID在批量入口报错，不重新编号或任意补号。缺少ID的无效记录无法生成合规结果，应写入单独输入错误报告并通知D。

C建议批量结果文件名为graph_retrieval_results.jsonl，B读取后按question_id关联原问题。无需依赖文件行号或两文件排列顺序。重跑结果放不同运行目录，避免同一结果文件出现多个相同问题ID。

建图输入错误属于离线建库失败，先生成数据错误报告，不能等同于某道题empty。索引未准备好时应明确失败；不得自动改用全量案例或其他来源。

## 八 不要求其他模块接收的内容

以下内容由C内部维护，不进入强制公共字段：

- graph.json及其版本2的精简边结构。
- NodeMapping、entity_mappings、共享概念ID和原实体ID映射。
- 向量、种子节点、相似度、邻居扩展记录、最大生成树及路径日志。
- 检索参数、模型版本和建库数据版本的运行配置。

这些信息可作为内部日志或另行附加文件留存，B和D仅依赖既定公共格式。数据版本和检索配置应随每次运行记录保存，避免回答使用了哪个知识库无法追溯。

## 九 联调验收清单

| 验收项 | 通过标准 |
| --- | --- |
| A的数据关联 | 案例ID、来源ID、原实体及证据可对应，编号不重排 |
| 知识库范围 | B、C共用build_ids；C输出的每条证据均来自该名单 |
| D的问题输入 | C只需要格式1.2，不索取真实类别或内部metadata |
| 成功结果 | 格式1.3字段完整，引用编号有效，证据可回查原文 |
| 原事实保留 | 要求与执行不混淆，跨案人物不当作同一人 |
| 空与错误 | empty和error分别保存，不相互替代、不偷偷换检索方法 |
| 公共回答接入 | B无需读取图文件，能直接消费context与evidence |
| D评价接入 | D接收统一answers.jsonl，包含实际上下文、证据和失败记录 |

建议先用团队共同的小样本和少量调试题完成联调，再扩展到正式建库名单。A、D仍按统一字段交付，不为C额外制作不同的数据结构。
