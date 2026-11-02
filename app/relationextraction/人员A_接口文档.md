# 人员 A 数据接口文档

> 版本：`0.2-draft`  
> 更新时间：2026-11-02  
> 适用范围：`app/relationextraction` 人员 A 的离线数据检查、划分与 KEO 格式 1.1 交付  
> 说明：人员 A 目前是**离线批处理**，没有 HTTP 服务。本文档定义的是**文件契约 + CLI/Python API**。正式交付以 `experiments/KEO/docs/数据结构与交付说明.md` 的**格式 1.1**为准。

---

## 1. 范围与术语

### 1.1 范围

本文档覆盖：

1. 人员 A 的输入数据格式；
2. 人员 A 的输出数据格式；
3. 当前脚本入口和建议的 CLI；
4. 建议的 Python 调用接口；
5. 给 B/C/D 的交接契约；
6. 质量标记和校验不变量；
7. KEO 格式 1.1 正式交付包：`案例数据.json`、`三元组数据.jsonl`、`划分名单.json`。

当前内部产物（`cases_master.jsonl`、`triples_clean.jsonl`、`split_*.csv/txt`）仍然保留，用于审计和复现；正式交付包由这些内部产物导出，不另做一套数据。

不覆盖：

- 文本索引和文本 RAG；
- 知识图谱构建；
- 图谱检索；
- 出题、回答生成和评测。

### 1.1.1 代码与数据位置

| 项目 | 位置 |
|---|---|
| 代码 | `experiments/KEO/core/data/`：`paths.py`、`build_dataset.py`、`audit_entity_names.py`、`apply_name_cleaning.py`、`export_keo.py`、`export_handoff.py`、`freeze_dataset.py` |
| 本地数据 | `experiments/KEO/data/person_a/`（gitignore；`KEO_DATA_ROOT` 可覆盖） |
| 正式交付包 | `experiments/KEO/data/person_a/handoff/keo/` |
| 角色分包 | `experiments/KEO/data/person_a/handoff/{b,c,d}/` |
| 上游抽取产物 | `app/relationextraction/output/case/`、`data/raw/`（保持原位置，只读） |

统一入口（仓库根目录执行）：

```powershell
python -m experiments.KEO.core.data.build_dataset --help
python -m experiments.KEO.core.data.audit_entity_names
python -m experiments.KEO.core.data.apply_name_cleaning
python -m experiments.KEO.core.data.export_keo
python -m experiments.KEO.core.data.export_handoff
python -m experiments.KEO.core.data.freeze_dataset
```

### 1.2 术语

| 术语 | 含义 |
|---|---|
| 案例 | 一条诈骗案例，`source_id` 形如 `case_000001` |
| 三元组 | `head - relation - tail`，带证据和来源 |
| 建库集 | `split=build`，60% 案例，供 B/C 建库 |
| 评估集 | `split=eval`，40% 案例，供 D 出题和评测 |
| 排除集 | `split=excluded_kg`，抽取失败或 0 三元组，不进入建库/评估 |
| 质量标记 | `quality.flags` 中的问题标签，不直接等于删除 |
| 冻结 | 名单和产物不再变化，后续修改升版本 |

---

## 2. 数据流

```text
data/raw/诈骗案例数据集_带编号.json
                +
app/relationextraction/output/case/
    ├── triples.jsonl
    ├── by_document/
    ├── failures/
    └── collaboration_merge_manifest.json
                |
                v
    人员 A：build_dataset
                |
                v
experiments/KEO/data/person_a/
    ├── cases_master.jsonl          # 内部审计用
    ├── triples_clean.jsonl         # 内部审计用
    ├── split_manifest.csv
    ├── split_build_ids.txt
    ├── split_eval_ids.txt
    ├── split_summary.csv
    ├── excluded_cases.csv
    ├── duplicate_text_groups.csv
    ├── issues_summary.csv
    ├── issues_triples.csv.gz
    ├── dataset_manifest.json
    ├── audit_report.md
    └── handoff/keo/                # 正式交付（KEO 格式 1.1）
        ├── 案例数据.json
        ├── 三元组数据.jsonl
        ├── 划分名单.json
        └── 交付说明.md
                |
                v
    B、C、D 共用同一个 handoff/keo 数据包
      B：按 build_ids 读取原文
      C：按 build_ids 读取三元组和原文
      D：按 eval_ids 读取评估原文
```

---

## 3. 输入契约

### 3.1 案例 JSON

默认路径：

```text
data/raw/诈骗案例数据集_带编号.json
```

格式：JSON 数组。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | `case_\d{6}`，唯一、连续、1-based |
| `案件类别` | string | 是 | 11 个诈骗类别之一 |
| `案情描述` | string | 是 | 案例原文，非空 |

不变量：

- 总数为 82,210；
- `id` 范围为 `case_000001`～`case_082210`；
- `id` 不重复、不缺失；
- `案件类别` 不为空；
- `案情描述` 不为空。

---

### 3.2 合并后的案例目录

默认路径：

```text
app/relationextraction/output/case/
```

目录结构：

```text
case/
├── triples.jsonl
├── summary.json
├── run_manifest.json
├── collaboration_merge_manifest.json
├── by_document/
│   └── <hash-prefix>/<source_id>.json
└── failures/
    └── <hash-prefix>/<source_id>.json
```

#### `triples.jsonl`

每行一个 JSON 对象，字段见 3.3。

#### `by_document/`

每个成功案例一个文件，文件名是 `source_id.json`，例如：

```text
by_document/fe/case_062486.json
```

文件内容至少包含：

```json
{
  "source_id": "case_062486",
  "source_type": "案例",
  "status": "success",
  "triples": []
}
```

#### `failures/`

每个失败案例一个文件，文件名同样是 `source_id.json`。

```json
{
  "source_id": "case_007923",
  "status": "failed",
  "error": "..."
}
```

#### `collaboration_merge_manifest.json`

记录三人合并的配置、分区范围、成功/失败数、资源哈希。

当前分区范围：

| 分区 | 案例范围 |
|---|---|
| A | `case_000001`～`case_027404` |
| B | `case_027405`～`case_054807` |
| C | `case_054808`～`case_082210` |

---

### 3.3 三元组对象

`triples.jsonl` 和 `triples_clean.jsonl` 的基础字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `triple_id` | string | 是 | 全局唯一，例如 `case_000001_T001` |
| `source_id` | string | 是 | 所属案例 ID |
| `source_type` | string | 是 | 当前为 `案例` |
| `fraud_category` | string | 是 | 诈骗类别，必须与案例类别一致 |
| `head` | object | 是 | 头实体 |
| `relation` | string | 是 | 28 类关系之一 |
| `tail` | object | 是 | 尾实体 |
| `factuality` | string | 是 | 6 类事实状态之一 |
| `evidence` | array | 是 | 证据列表 |
| `confidence` | number | 是 | 0～1 |
| `status` | string | 否 | 例如 `待人工审核` |

#### `head` / `tail`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 实体 ID，例如 `case_000001_E001` |
| `name` | string | 实体名称 |
| `type` | string | 实体类型，例如 `人员`、`银行卡` |
| `role` | string | 角色，例如 `受害人`、`疑似诈骗者` |

#### `evidence`

| 字段 | 类型 | 说明 |
|---|---|---|
| `unit_id` | string | 证据单元 ID，必须属于当前案例 |
| `text` | string | 证据原文，必须是案例原文的子串 |

#### 关系与事实状态

- 关系：28 类，定义见 `schemas/关系类型体系_v0.1_中文可读版.json`；
- 事实状态：6 类：

```text
明确发生
被要求执行
疑问表达
否定表达
假设表达
不确定
```

---

## 4. 输出契约

所有内部输出默认位于：

```text
experiments/KEO/data/person_a/
```

正式交付默认位于：

```text
experiments/KEO/data/person_a/handoff/keo/
```

### 4.0 KEO 格式 1.1 正式交付包（最高优先级）

远端 `experiments/KEO/docs/数据结构与交付说明.md` 已把 A 的交付固定为三个文件：

```text
案例数据.json
三元组数据.jsonl
划分名单.json
```

A 只提供同一个数据包，不分别制作 B 版和 C 版。B、C、D 都从这一份包中按 `划分名单.json` 取自己需要的部分。

| 正式文件 | 必填 | 主要使用方 | 来源 |
|---|---|---|---|
| `案例数据.json` | 是 | B、C、D | `cases_master.jsonl` |
| `三元组数据.jsonl` | 是 | C（D 可选参考） | `triples_clean.jsonl` |
| `划分名单.json` | 是 | B、C、D | `split_build_ids.txt` + `split_eval_ids.txt` |
| `交付说明.md` | 建议 | 全组 | 新生成 |

#### 4.0.1 `案例数据.json`

JSON 数组，每个案例一个对象，字段名必须保持中文原样：

```json
[
  {
    "id": "case_000001",
    "案情描述": "对方称刷单可以赚钱，要求受害人下载某应用。",
    "案件类别": "刷单返利类"
  }
]
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 现有案例编号，不重新编号 |
| `案情描述` | string | 是 | 完整案例原文，不附加类别或评分说明 |
| `案件类别` | string | 是 | 11 类之一，与三元组 `fraud_category` 一致 |

建议默认包含全部 82,210 条案例，保留原始编号连续性；`build_ids` 和 `eval_ids` 只覆盖其中 82,148 条可用案例。如果组内要求只交付可用集，也可以只导出 82,148 条，但不得重新编号，并必须在 `交付说明.md` 中列出 62 条未交付案例。

#### 4.0.2 `三元组数据.jsonl`

JSONL，每行一条，沿用现有结构。当前 `triples_clean.jsonl` 的字段与格式 1.1 兼容，额外的 `quality` 字段允许保留。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `triple_id` | string | 是 | 与 `source_id` 组合唯一 |
| `source_id` | string | 是 | 必须存在于 `案例数据.json` 的 `id` |
| `source_type` | string | 是 | 固定 `案例` |
| `fraud_category` | string | 是 | 必须与案例 `案件类别` 一致 |
| `head` / `tail` | object | 是 | `id`、`name`、`type` 必填；`role` 可空 |
| `relation` | string | 是 | 28 类关系之一，保留方向 |
| `factuality` | string | 是 | 6 类事实状态之一 |
| `evidence` | array | 是 | `unit_id`、`text`，text 必须是原文子串 |
| `confidence` | number/null | 否 | 保留原抽取置信度 |
| `status` | string/null | 否 | 保留原审核状态 |
| `quality` | object | 否 | 内部质量标记，允许保留 |

建议交付全部 1,008,822 条三元组；C 按 `build_ids` 自行过滤。不要为了“看起来干净”而静默删除 `UNKNOWN`、自环或重复副本；这些必须通过 `quality.flags` 和 `交付说明.md` 显式记录。

#### 4.0.3 `划分名单.json`

结构固定为：

```json
{
  "build_ids": ["case_000001", "case_000002"],
  "eval_ids": ["case_000003", "case_000004"]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `build_ids` | string[] | 建库案例，约 60%，B/C 共用 |
| `eval_ids` | string[] | 评估案例，约 40%，D 使用 |

必须满足：

- 两份名单都按 `source_id` 升序排列；
- 各自无重复；
- 两份名单无交集；
- 并集 = 82,148；
- 当前数量为 `build_ids = 49,289`、`eval_ids = 32,859`；
- 62 条排除案例不进入任何一份名单，但必须在 `交付说明.md` 中列出。

#### 4.0.4 `交付说明.md`

正式包的必需说明文件，至少包含：

1. 包版本、生成时间、生成脚本及 SHA256；
2. 案例、三元组、建库、评估、排除的准确数量；
3. 62 条排除案例的 `source_id`、原因和影响；
4. 质量标记的数量与处理口径；
5. 名称清洗和别名映射的版本与使用方式；
6. 文件校验和；
7. 与上一版本的差异。

#### 4.0.5 名称清洗与别名映射（C 依赖）

C 的建图规则要求：

- 人员按 `(source_id, entity.id)` 建节点，不跨案合并；
- 身份、机构、APP、平台等按 `(concept, 规范类型, 规范名称)` 共享；
- 使用 **A 已清洗的名称**；
- 如果 A 提供确认的别名映射，C 直接使用，不另做语义相似度合并。

当前人员 A **只做了问题标记，尚未完成名称归一化**。正式交付前至少需要：

- 对 `head.name` / `tail.name` 做确定性清洗（去首尾空白、全半角统一、明显的同一名称合并）；
- 保留原名称，不覆盖原始字段；
- 输出可选的 `实体别名映射.json`，建议结构：

```json
{
  "version": "1.0",
  "entities": [
    {
      "source_id": "case_012615",
      "entity_id": "case_012615_E004",
      "original_name": "客服",
      "clean_name": "客服",
      "type": "身份角色",
      "role": null
    }
  ],
  "aliases": [
    {
      "type": "应用程序",
      "canonical": "微信",
      "aliases": ["WeChat", "微信APP"]
    }
  ]
}
```

约束：

- 不跨案合并人员；
- `UNKNOWN` / “未知” 保留原实体 ID，不生成全局未知节点；
- “微信”和“微信账号”不合并，“微信”和“微信微粒贷”不当作同义词；
- 不确定的名称保留并记录，不静默合并；
- 别名规则必须由 A 和 C 共同确认后才能进入正式包。

`实体别名映射.json` 不是格式 1.1 的必填文件；不提供时，C 直接使用 `三元组数据.jsonl` 中的清洗后名称。

#### 4.0.6 内部产物到正式包的映射

| 正式文件 | 内部来源 | 转换规则 |
|---|---|---|
| `案例数据.json` | `cases_master.jsonl` | `source_id`→`id`；`text`→`案情描述`；`category`→`案件类别`；丢弃审计字段 |
| `三元组数据.jsonl` | `triples_clean.jsonl` | 原样保留；`quality` 可保留 |
| `划分名单.json` | `split_build_ids.txt` / `split_eval_ids.txt` | 读取为数组，排序后写入 |
| `交付说明.md` | 新生成 | 汇总计数、排除项、质量口径、校验和 |

#### 4.0.7 正式包验收

- 三个 JSON/JSONL 文件都能被标准解析器读取；
- `案例数据.json` 的 `id` 唯一；
- `三元组数据.jsonl` 的 `source_id` 全部能在案例数据中找到；
- `划分名单.json` 无交集，并集为 82,148；
- `build_ids` / `eval_ids` 全部存在于案例数据；
- 建库侧三元组的 `source_id` 全部在 `build_ids` 中；
- 评估侧原文不出现在建库包中；
- 文件 SHA256 与 `交付说明.md` 一致。

---

### 4.1 `cases_master.jsonl`

每条案例一行，包含全部 82,210 条案例。

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_id` | string | 案例 ID |
| `category` | string | 诈骗类别 |
| `text` | string | 案例原文 |
| `text_sha256` | string | 原文 SHA256 |
| `text_length` | integer | 原文字符数 |
| `partition` | string | `A` / `B` / `C` |
| `triple_count` | integer | 该案例三元组数 |
| `kg_usable` | boolean | 是否有三元组且未失败 |
| `excluded_reason` | string | `extraction_failed` / `zero_triples` / 空 |
| `duplicate_text_group` | string/null | 重复文本组 ID |
| `duplicate_group_size` | integer | 该文本重复组大小 |
| `split` | string | `build` / `eval` / `excluded_kg` |

示例：

```json
{
  "source_id": "case_000001",
  "category": "刷单返利类",
  "text": "...",
  "text_sha256": "ce4db4e0...",
  "text_length": 316,
  "partition": "A",
  "triple_count": 18,
  "kg_usable": true,
  "excluded_reason": "",
  "duplicate_text_group": null,
  "duplicate_group_size": 1,
  "split": "build"
}
```

---

### 4.2 `triples_clean.jsonl`

在原始三元组字段基础上增加 `quality`：

```json
{
  "triple_id": "case_000001_T001",
  "source_id": "case_000001",
  "source_type": "案例",
  "fraud_category": "刷单返利类",
  "head": {
    "id": "case_000001_E001",
    "name": "受害人",
    "type": "人员",
    "role": "受害人"
  },
  "relation": "添加好友",
  "tail": {
    "id": "case_000001_E002",
    "name": "未知",
    "type": "人员",
    "role": "疑似诈骗者"
  },
  "factuality": "明确发生",
  "evidence": [
    {
      "unit_id": "case_000001_clause_005",
      "text": "..."
    }
  ],
  "confidence": 0.95,
  "status": "待人工审核",
  "quality": {
    "partition": "A",
    "resource_version": "current",
    "source_category": "刷单返利类",
    "flags": ["unknown_tail"],
    "duplicate_content_group": "74925df9c7eea6cd",
    "is_duplicate_content_copy": false
  }
}
```

`quality` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `partition` | string | 抽取分区 `A` / `B` / `C` |
| `resource_version` | string | `current` 或 `legacy`；当前只有历史标记意义 |
| `source_category` | string/null | 案例类别 |
| `flags` | array | 质量问题标签 |
| `duplicate_content_group` | string | 内容重复组 ID |
| `is_duplicate_content_copy` | boolean | 是否为重复副本 |

注意：**当前 `triples_clean.jsonl` 不含 `split` 字段**。需要按建库/评估筛选时，必须与 `split_manifest.csv` 或 `split_build_ids.txt` / `split_eval_ids.txt` 关联。

---

### 4.3 `split_manifest.csv`

每条案例一行，共 82,210 行。

| 列 | 说明 |
|---|---|
| `source_id` | 案例 ID |
| `category` | 诈骗类别 |
| `partition` | 抽取分区 |
| `split` | `build` / `eval` / `excluded_kg` |
| `kg_usable` | 是否可用 |
| `triple_count` | 三元组数 |
| `excluded_reason` | 排除原因 |
| `duplicate_text_group` | 重复文本组 ID |
| `duplicate_group_size` | 重复组大小 |

---

### 4.4 `split_build_ids.txt` / `split_eval_ids.txt`

- UTF-8 文本；
- 每行一个 `source_id`；
- 已排序；
- 末尾有换行；
- 两份名单无交集；
- 并集为 82,148。

当前数量：

```text
split_build_ids.txt  49,289
split_eval_ids.txt   32,859
```

---

### 4.5 `split_summary.csv`

| 列 | 说明 |
|---|---|
| `category` | 类别 |
| `total` | 案例总数 |
| `usable` | 可用案例数 |
| `excluded` | 排除数 |
| `build` | 建库数 |
| `eval` | 评估数 |
| `build_pct_of_usable` | 建库占比 |
| `eval_pct_of_usable` | 评估占比 |

最后一行 `category=ALL` 是总计。

---

### 4.6 `excluded_cases.csv`

| 列 | 说明 |
|---|---|
| `source_id` | 案例 ID |
| `category` | 类别 |
| `partition` | 抽取分区 |
| `reason` | `extraction_failed` 或 `zero_triples` |
| `text_length` | 原文长度 |
| `triple_count` | 三元组数（当前为 0） |

当前共 62 行：

- 9 篇 `extraction_failed`；
- 53 篇 `zero_triples`。

---

### 4.7 `duplicate_text_groups.csv`

| 列 | 说明 |
|---|---|
| `group_id` | 重复组 ID |
| `source_ids` | 分号分隔的案例 ID |
| `categories` | 分号分隔的类别 |
| `partitions` | 分号分隔的抽取分区 |
| `cross_category` | 是否跨类别，`0/1` |

当前共 29 组，全部为 2 条一组；23 组跨抽取分区；1 组跨类别。

---

### 4.8 `issues_summary.csv`

| 列 | 说明 |
|---|---|
| `issue_type` | 问题类型 |
| `triple_count` | 涉及三元组数 |
| `case_count` | 涉及案例数 |

常见问题类型：

```text
unknown_head
unknown_tail
same_name
duplicate_content_copy
self_loop
missing_head_tail_name
duplicate_content_group
duplicate_content_extra
zero_triples
extraction_failed
duplicate_text_group
```

注意：当前文件仍有一行 `partition_resource_mismatch`，属于过期误报，应在 P0-1 中删除或标记为 `resolved`。

---

### 4.9 `issues_triples.csv.gz`

gzip 压缩的 CSV，每行一个有问题三元组。

| 列 | 说明 |
|---|---|
| `triple_id` | 三元组 ID |
| `source_id` | 案例 ID |
| `partition` | 抽取分区 |
| `relation` | 关系 |
| `head_name` | 头实体名 |
| `tail_name` | 尾实体名 |
| `flags` | 分号分隔的质量标记 |

---

### 4.10 `dataset_manifest.json`

建议结构：

```json
{
  "version": "v1.0",
  "created_at": "2026-10-27T19:13:58+08:00",
  "frozen_at": null,
  "status": "draft_pending_config_confirmation",
  "inputs": {
    "cases": {
      "path": "data/raw/诈骗案例数据集_带编号.json",
      "sha256": "..."
    },
    "triples": {
      "path": "app/relationextraction/output/case/triples.jsonl",
      "size_bytes": 995778517,
      "sha256": "..."
    },
    "merge_manifest": {
      "path": "app/relationextraction/output/case/collaboration_merge_manifest.json",
      "sha256": "..."
    }
  },
  "seed": 42,
  "train_ratio": 0.6,
  "counts": {
    "documents": 82210,
    "success_documents": 82201,
    "failed_documents": 9,
    "zero_triple_documents": 53,
    "usable_documents": 82148,
    "excluded_documents": 62,
    "triples": 1008822,
    "unique_triple_ids": 1008822,
    "build": 49289,
    "eval": 32859
  },
  "outputs": {
    "cases_master.jsonl": {
      "path": "experiments/KEO/data/person_a/cases_master.jsonl",
      "size_bytes": 107777400,
      "sha256": "..."
    }
  }
}
```

状态建议：

```text
draft -> ready_for_handoff -> frozen -> deprecated
```

---

### 4.11 `audit_report.md`

人类可读的报告，至少包含：

1. 输入与总量；
2. 已确认决策；
3. 数据质量概况；
4. 划分结果；
5. 输出文件清单；
6. 剩余问题和下一步。

---

## 5. 当前脚本接口

### 5.1 `build_dataset.py`

当前路径（2026-11-02 迁移后，已纳入 git）：

```text
experiments/KEO/core/data/build_dataset.py
```

无硬编码路径，仓库根目录由 `experiments/KEO/core/data/paths.py` 自动定位；数据目录可用 `KEO_DATA_ROOT` 覆盖。已提供 CLI：

```powershell
python -m experiments.KEO.core.data.build_dataset --help
python -m experiments.KEO.core.data.build_dataset --dry-run
```

默认输入输出：

| 角色 | 默认路径 |
|---|---|
| 案例原文 | `data/raw/诈骗案例数据集_带编号.json` |
| 抽取产物 | `app/relationextraction/output/case/` |
| 输出 | `experiments/KEO/data/person_a/` |

---

### 5.2 `merge_collaboration_results.py`

当前已有 CLI：

```powershell
python -m app.relationextraction.code.merge_collaboration_results `
  --target "app\relationextraction\output\student_a\case" `
  --student-b "app\relationextraction\output\student_b\case" `
  --student-c "app\relationextraction\output\student_c\case"
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--target` | 是 | A 的 case 目录，也是合并目标 |
| `--student-b` | 是 | B 的 case 目录 |
| `--student-c` | 是 | C 的 case 目录 |

该脚本会校验区间、模型、Prompt、Schema，并生成 `collaboration_merge_manifest.json`。

---

### 5.3 `llmuse.py`

上游抽取入口，已有子命令：

```text
check
run
status
export
```

常用命令：

```powershell
python -m app.relationextraction.code.llmuse check
python -m app.relationextraction.code.llmuse run --source-type case --start-index 0 --limit 1
python -m app.relationextraction.code.llmuse status --source-type case --start-index 0 --limit 27404
python -m app.relationextraction.code.llmuse export --source-type case --start-index 0 --limit 27404
```

详细参数见 `README_三人协作运行.md`。

### 5.4 当前运行环境注意事项

当前仓库中的虚拟环境不可直接使用：

- `venv/pyvenv.cfg` 指向不存在的 `C:\Program Files\Python310`；
- `.venv` 是 WSL 环境，Windows 下不可用；
- 系统 Python 3.10 缺少 `pydantic_settings`，`llmuse.py` 和 `merge_collaboration_results.py` 会启动失败。

**2026-11-02 更新：Windows `venv` 已修复**（`pyvenv.cfg` 的 `home` 指向本机 Python 3.10.11 安装目录），为轻量环境，已通过 `llmuse check` 与 `merge_collaboration_results --help`。`build_dataset.py` 只依赖标准库，也可用系统 Python 运行。FastAPI / paddle / chromadb 等主服务依赖尚未安装，需要时按 `requirements.txt` 补齐。

---

## 6. CLI（已实现）

入口已固定在 `experiments/KEO/core/data/build_dataset.py`，接口为：

```powershell
python -m experiments.KEO.core.data.build_dataset `
  --cases "data/raw/诈骗案例数据集_带编号.json" `
  --case-dir "app/relationextraction/output/case" `
  --output "experiments/KEO/data/person_a" `
  --seed 42 `
  --train-ratio 0.6 `
  --status frozen `
  --hash-large-files
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--cases` | `data/raw/诈骗案例数据集_带编号.json` | 案例 JSON |
| `--case-dir` | `app/relationextraction/output/case` | 合并后的案例目录 |
| `--output` | `experiments/KEO/data/person_a` | 输出目录 |
| `--seed` | `42` | 划分随机种子 |
| `--train-ratio` | `0.6` | 建库比例 |
| `--status` | `draft` | `draft` / `ready_for_handoff` / `frozen` |
| `--accept-excluded` | `false` | 是否接受 62 篇排除案例，不再报错 |
| `--hash-large-files` | `false` | 是否为 >200MB 文件计算 SHA256 |
| `--dry-run` | `false` | 只校验，不写输出 |
| `--overwrite` | `false` | 是否允许覆盖已有输出 |

建议退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 输入校验失败 |
| 3 | 输出目录已存在且未允许覆盖 |
| 4 | 资源版本检查失败 |
| 5 | 未处理异常 |

---

## 7. 目标 Python API（待实现，当前只提供 CLI）

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BuildResult:
    output_dir: Path
    status: str
    manifest_path: Path
    counts: dict
    warnings: list[str]


def build_dataset(
    cases_path: Path,
    case_dir: Path,
    output_dir: Path,
    *,
    seed: int = 42,
    train_ratio: float = 0.6,
    status: str = "draft",
    accept_excluded: bool = False,
    hash_large_files: bool = False,
    dry_run: bool = False,
) -> BuildResult:
    ...
```

异常建议：

```python
class PersonAError(Exception): ...
class InputValidationError(PersonAError): ...
class ResourceMismatchError(PersonAError): ...
class OutputExistsError(PersonAError): ...
```

---

## 8. 下游交接接口

> 正式交付以 4.0 的 `handoff/keo/` 为准。`handoff/b/`、`handoff/c/`、`handoff/d/` 是同一份数据的角色切片，由 `python -m experiments.KEO.core.data.export_handoff` 生成，不重新抽取；两者冲突时以 KEO 格式 1.1 为准。
>
> 已交叉校验：`b/build_ids.txt` ≡ `c/build_ids.txt` ≡ `划分名单.json`；`c/triples_build.jsonl` 与 `keo/三元组数据.jsonl` 中 `source_id ∈ build_ids` 的部分 SHA256 完全一致（604,035 条）；`d` 中不含建库三元组。

### 8.1 给 B：建库文本

```text
handoff/b/
├── cases_build.jsonl
├── build_ids.txt
├── dev_ids.txt
└── manifest.json
```

`cases_build.jsonl` 字段：

```json
{
  "source_id": "case_000001",
  "category": "刷单返利类",
  "text": "...",
  "split": "build"
}
```

要求：

- 只含建库侧；
- `source_id` 与 `split_build_ids.txt` 一致；
- `dev_ids.txt` 固定 22 条（seed=42，按类别轮转），只用于 B 调试，不计入评估；
- 当前包：案例 49,289 条。

---

### 8.2 给 C：建库三元组

```text
handoff/c/
├── triples_build.jsonl
├── build_ids.txt
├── 实体别名映射.json     # 正式别名表，C 直接读取
├── 归一化规则.md         # 清洗口径、未合并项、建图注意
└── manifest.json
```

`triples_build.jsonl` 字段：

- 基础三元组字段 + `evidence` + `quality.flags`；
- `head.name` / `tail.name` 已按确认口径清洗；被改名时带 `head.original_name` / `tail.original_name`；
- 当前包：三元组 604,035 条，覆盖 49,289 个建库案例。

要求：

- 只含建库侧；
- 每条 `source_id` 都在 `build_ids.txt` 中；
- 不包含评估侧三元组。

---

### 8.3 给 D：评估文本和真实类别

```text
handoff/d/
├── cases_eval.jsonl
├── eval_ids.txt
└── manifest.json
```

`cases_eval.jsonl` 字段：

```json
{
  "source_id": "case_000001",
  "category": "刷单返利类",
  "text": "...",
  "split": "eval"
}
```

要求：

- 只含评估侧；
- 不包含建库侧原文、三元组或图谱内容；
- 真实类别可用于评分，但题干不能泄露类别；
- 当前包：案例 32,859 条。

---

### 8.4 给全组：冻结清单

冻结信息不再单独出 `handoff/all/`，统一在 `experiments/KEO/data/person_a/` 下：

```text
experiments/KEO/data/person_a/
├── dataset_manifest.json        # status=frozen / version / frozen_at / 全部 SHA256 / handoff.packages
├── dataset_manifest.draft.json  # 冻结前版本，留档
├── 冻结说明.md                   # 冻结范围、交付包、清洗校验、复现命令
├── decisions.md                 # 已确认口径（含名称清洗、冻结、代码位置、角色分包）
├── audit_report.md              # 数据检查与划分报告
├── split_summary.csv / split_manifest.csv / excluded_cases.csv
└── name_cleaning/apply_v1.0/    # 清洗产物：triples_clean_v2.jsonl、实体别名映射.json、变更明细、报告
```

任何校验都以 `dataset_manifest.json` 记录的文件级 SHA256 为准；路径均为相对仓库根目录，可跨机器验证。

---

## 9. 质量标记与处理规则

| 标记 | 含义 | 建议处理 |
|---|---|---|
| `source_not_found` | `source_id` 不在案例表 | 拒绝，必须修 |
| `duplicate_triple_id` | `triple_id` 重复 | 拒绝，必须修 |
| `category_mismatch` | 三元组类别与案例类别不一致 | 拒绝，必须修 |
| `bad_relation` | 关系不在 28 类内 | 拒绝或映射 |
| `bad_factuality` | 事实状态不在 6 类内 | 拒绝或映射 |
| `bad_confidence` | 置信度不在 0～1 | 拒绝或修正 |
| `missing_field` | 必填字段缺失 | 拒绝或人工补 |
| `missing_head_tail_name` | 头/尾实体名为空 | 人工复核 |
| `unknown_head` | 头实体为未知/空占位 | 按 `source_id` 隔离，禁止全局合并 |
| `unknown_tail` | 尾实体为未知/空占位 | 按 `source_id` 隔离，禁止全局合并 |
| `self_loop` | 头尾实体 ID 相同 | 建图时删除或白名单保留 |
| `same_name` | 头尾实体名称相同 | 同 ID 同类型可合并；否则人工复核 |
| `entity_type_conflict` | 同一实体 ID 出现不同类型 | 人工复核 |
| `duplicate_content_copy` | 内容重复副本 | 保留首条，合并证据 |
| `evidence_missing` | 证据缺失 | 拒绝或人工补 |
| `evidence_not_substring` | 证据不是原文子串 | 拒绝，必须修 |
| `unit_prefix_mismatch` | `unit_id` 不属于该案例 | 拒绝，必须修 |
| `zero_triples` | 成功但 0 三元组 | 标记排除，或补跑 |
| `extraction_failed` | 抽取失败 | 标记排除，或补跑 |
| `duplicate_text_group` | 案例文本完全重复 | 划分时保持同侧 |

---

## 10. 校验不变量

任何一次正式生成都必须满足：

### 10.1 案例侧

- 案例总数 = 82,210；
- ID 连续、唯一；
- 原文非空；
- 类别非空且在 11 类内。

### 10.2 三元组侧

- 三元组总数 = 1,008,822；
- `triple_id` 唯一；
- `source_id` 全部存在；
- 类别不一致 = 0；
- 证据不是原文子串 = 0；
- 关系/事实状态/置信度越界 = 0；
- `unit_id` 前缀属于对应案例。

### 10.3 划分侧

- 建库 + 评估 = 82,148；
- 建库 ∩ 评估 = 0；
- 建库 = 49,289；
- 评估 = 32,859；
- 排除 = 62；
- 11 个类别在建库和评估两侧都有；
- 同一案例的全部三元组在同一侧；
- 重复文本组不跨侧；
- seed = 42。

### 10.4 质量侧

当前已知指标（用于回归）：

| 指标 | 当前值 |
|---|---:|
| 至少一端未知/空 | 416,288 |
| 自环 | 5,820 |
| 同名 | 124,133 |
| 内容重复组 | 28,443 |
| 内容重复额外条数 | 43,836 |
| 重复文本组 | 29 |
| 重复文本条数 | 58 |
| 跨类别重复文本组 | 1 |
| 跨分区重复文本组 | 23 |

---

## 11. 版本与状态

### 11.1 状态流转

```text
draft
  -> ready_for_handoff
  -> frozen
  -> deprecated
```

| 状态 | 含义 |
|---|---|
| `draft` | 可修改，不可用于正式实验 |
| `ready_for_handoff` | 核心检查完成，等待交接 |
| `frozen` | 冻结，B/C/D 可正式使用 |
| `deprecated` | 已被新版本替代 |

### 11.2 版本规则

- 初次冻结：`v1.0`；
- 任何影响 ID、划分、三元组内容的修改：主版本 +1；
- 只修文档或 manifest 字段：次版本 +1；
- 不覆盖旧版本目录；
- 每次冻结记录输入哈希、脚本哈希、生成时间。

---

### 11.3 当前状态（2026-11-02）

| 项目 | 状态 |
|---|---|
| 数据冻结 | `status=frozen`、`version=1.0-final` |
| 正式包 | `handoff/keo/`：案例 82,210 / 三元组 1,008,822 / 划分 49,289+32,859 / 实体别名映射 / 交付说明 |
| 角色分包 | `handoff/{b,c,d}/`（49,289 / 604,035 三元组 / 32,859） |
| 名称清洗 | 已完成：A 1 次、B2 467 次、C1 61,657 次、D 17,133 次；auto 1,125 组；冲突 105 个；校验残留全 0 |
| 未合并别名 | `review` 1,434 组、`hold` 898 组（按口径保留） |
| 代码 | `experiments/KEO/core/data/`，无硬编码路径，CLI 齐全 |
| Windows 环境 | `venv` 已修复为 Python 3.10.11 轻量环境（46 包），`llmuse check` 通过；主服务依赖（FastAPI/paddle/chromadb）按需补装 |
| 遗留风险 | 9 篇抽取失败 + 53 篇零三元组（47 篇疑似遗漏）已记录为已接受风险，不补跑 |

## 12. 示例

### 12.1 `cases_master.jsonl`

```json
{"source_id":"case_000001","category":"刷单返利类","text":"...","text_sha256":"ce4db4e0...","text_length":316,"partition":"A","triple_count":18,"kg_usable":true,"excluded_reason":"","duplicate_text_group":null,"duplicate_group_size":1,"split":"build"}
```

### 12.2 `triples_clean.jsonl`

```json
{"triple_id":"case_000001_T001","source_id":"case_000001","source_type":"案例","fraud_category":"刷单返利类","head":{"id":"case_000001_E001","name":"受害人","type":"人员","role":"受害人"},"relation":"添加好友","tail":{"id":"case_000001_E002","name":"未知","type":"人员","role":"疑似诈骗者"},"factuality":"明确发生","evidence":[{"unit_id":"case_000001_clause_005","text":"..."}],"confidence":0.95,"status":"待人工审核","quality":{"partition":"A","resource_version":"current","source_category":"刷单返利类","flags":["unknown_tail"],"duplicate_content_group":"74925df9c7eea6cd","is_duplicate_content_copy":false}}
```

### 12.3 `split_manifest.csv`

```csv
source_id,category,partition,split,kg_usable,triple_count,excluded_reason,duplicate_text_group,duplicate_group_size
case_000001,刷单返利类,A,build,True,18,,,1
case_000002,虚假网络投资理财类,A,build,True,9,,,1
```

### 12.4 `excluded_cases.csv`

```csv
source_id,category,partition,reason,text_length,triple_count
case_007923,冒充公检法及政府机关类,A,extraction_failed,489,0
case_007535,刷单返利类,A,zero_triples,325,0
```

### 12.5 `issues_triples.csv.gz`

```csv
triple_id,source_id,partition,relation,head_name,tail_name,flags
case_000001_T001,case_000001,A,添加好友,受害人,未知,unknown_tail
```

---

## 13. 待确认问题

1. 正式包目录是否固定为 `experiments/KEO/data/person_a/handoff/keo/`？
2. `案例数据.json` 是包含全部 82,210 条案例，还是只包含 82,148 条可用案例？两种都不重新编号，但后者需要额外列出 62 条排除项。
3. ~~人员 A 的代码迁移位置~~（已定：`experiments/KEO/core/data/`，空的 `core/cleardata/` 已删除）；README 写的 `common/configs/`、`common/docs/` 实际也在 `experiments/KEO/configs/`、`experiments/KEO/docs/`，需要与组内确认以哪个为准。
4. 名称清洗和别名映射由 A 主导还是 C 主导？`实体别名映射.json` 是否作为正式附件？
5. 1000 条人工抽检的 gold 数据从哪里来？
6. 冻结产物是否允许进入 git，还是只保留校验和与外部存储地址？
