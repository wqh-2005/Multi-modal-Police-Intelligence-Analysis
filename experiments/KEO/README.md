# KEO 实验

复用已有诈骗案例和三元组，对比直接回答、文本 RAG 与图谱 RAG 的研判效果。

## 目录

```text
experiments/KEO/
├─ configs/config.py     唯一的路径与参数定义处（B 与 C 共用）
├─ core/
│  ├─ data/              A：对齐检查、划分、名称清洗、交付包导出（已实现）
│  ├─ text_rag/          B：建库、检索、直接回答、公共回答模块
│  ├─ graph_rag/         C：建图、图谱检索、邻居扩展与上下文重建
│  ├─ evaluation/        D：测试题组织、参考答案管理与评价
│  └─ ingest.py          B：把 A/D 的交接包导入 data/，登记 SHA256
├─ data/
│  ├─ person_a/          A 的中间数据与交付包（本地，不入库）
│  └─ v1.1/              B 导入后的输入数据（本地，不入库）
├─ deploy/neo4j/         C 的图数据库启动环境
├─ docs/                 接口文档、分工说明、进度
├─ models/               回答模型与 embedding 封装
└─ output/
   ├─ index/             向量索引，本地生成，不入库
   ├─ results/           检索与回答结果，本地生成，不入库
   └─ test/              C 图谱实验中间产物，本地生成，不入库
```

## A 模块入口（已实现，2026-11-02）

代码在 `experiments/KEO/core/data/`，本地数据在 `experiments/KEO/data/person_a/`（`KEO_DATA_ROOT` 可覆盖）。

```powershell
python -m experiments.KEO.core.data.build_dataset --help   # 重建对齐检查与 60/40 划分
python -m experiments.KEO.core.data.audit_entity_names     # 实体名称审计（只读）
python -m experiments.KEO.core.data.resolve_review_aliases  # review 组官方核验（应用宝 + 必应）
python -m experiments.KEO.core.data.apply_name_cleaning    # 执行名称清洗（auto + review 合并），产出别名映射
python -m experiments.KEO.core.data.export_keo             # 导出 KEO 格式 1.1 共用交付包
python -m experiments.KEO.core.data.export_handoff         # 导出 B / C / D 角色分包
python -m experiments.KEO.core.data.freeze_dataset         # 重算 SHA256 并冻结 manifest
```

交付包（当前版本 `1.1`，已冻结；`1.0-final` 保留在 `handoff/`）：

```text
experiments/KEO/data/person_a/handoff/
├── keo/                  # 共用包（B/C/D 同一份）
│   ├── 案例数据.json      # 82,210 条
│   ├── 三元组数据.jsonl   # 1,008,822 条
│   ├── 划分名单.json      # build 49,289 / eval 32,859
│   ├── 实体别名映射.json  # C 建图直接读取
│   └── 交付说明.md
├── b/  cases_build.jsonl / build_ids.txt / dev_ids.txt / manifest.json
├── c/  triples_build.jsonl / build_ids.txt / 实体别名映射.json / 归一化规则.md / manifest.json
└── d/  cases_eval.jsonl / eval_ids.txt / manifest.json
```

角色分包是共用包的切片，不重新抽取：B 拿建库原文 + 开发集，C 拿建库三元组 + 名称映射，D 只拿评估侧原文与真实类别。

清洗与别名：`name_cleaning/apply_v1.1/`（三元组、`实体别名映射.json`）；review 别名核验证据在 `name_cleaning/review_resolution/`。

版本、校验和与复现命令见 `experiments/KEO/data/person_a/冻结说明.md` 与 `dataset_manifest.json`（1.1），历史版本见 `dataset_manifest_1.0-final.json`。

## 数据怎么进来（B）

运行期不读 `temp/`。`temp/` 只是他人的暂存交接目录，随时可能被清空或覆盖，
直接依赖它等于把实验挂在不可控的位置。B 用 `core/ingest.py` 一次性拷入
`data/v1.1/`，登记 SHA256 与条数，之后所有代码只读 `data/`。

```text
python -m experiments.KEO.core.ingest             导入
python -m experiments.KEO.core.ingest --dry-run   只看计划，不写文件
python -m experiments.KEO.core.ingest --force     源更新后重新导入
```

导入内容约 88 MB：建库原文 49,289 篇、`build_ids.txt`、`dev_ids.txt`、划分名单、两套题库。
A 的完整包（案例主表 85 MB 加全量三元组 1.1 GB）默认不导入，需要时加 `--include-full`。

校验分三层：有源 manifest 的文件比对 SHA256；其余记录本项目自己的 SHA256；
最后检查 `build_ids.txt`、`划分名单.json`、`cases_build.jsonl` 三者条数一致。
结果写入 `data/v1.1/manifest.json`。

数据集版本可用环境变量 `KEO_DATASET_VERSION` 覆盖，交接包位置可用 `KEO_STAGING_ROOT` 覆盖。

## B 的运行步骤

在仓库根执行，命令前缀 `.\.venv\Scripts\python.exe -m`。

**1. 导入数据**

```text
python -m experiments.KEO.core.ingest
```

**2. 校验建库数据**

```text
python -m experiments.KEO.core.text_rag.build_data
```

输出 49,289 篇的校验结果，以及与 `build_ids.txt` 的一致性。

**3. 建调试索引（先小样本）**

```text
python -m experiments.KEO.core.text_rag.build_index --limit 300 --debug
```

调试索引写到 `output/index/text_index_debug`，集合名 `keo_text_debug`，与正式索引隔离。

**4. 试检索**

```text
python -m experiments.KEO.core.text_rag.retrieve --debug --question "对方称刷单可以赚钱，要求下载应用充值后无法提现"
```

**5. 正式建索引**

```text
python -m experiments.KEO.core.text_rag.build_index
```

49,289 篇，实测约 28 分钟，产出 100,751 个 chunk、约 725 MB。中断后重跑会跳过已完成案例。

**6. 跑题并产出 answers.jsonl**

```text
python -m experiments.KEO.core.text_rag.run_b --questions experiments/KEO/data/v1.1/questions/questions_demo.jsonl --methods direct,text_rag
```

结果写到 `output/results/`，`answers.jsonl` 交 D。

网络抖动会让部分题失败。补跑失败的题用 `--resume`，它按（题号, 方法）跳过已成功的记录，只重跑失败项：

```text
python -m experiments.KEO.core.text_rag.run_b --questions experiments/KEO/data/v1.1/questions/questions_demo.jsonl --methods direct,text_rag --run-id <同上一次的 run_id> --resume
```

## C 图谱 RAG 入口

代码在 `core/graph_rag/`，配置为 `configs/config.py` 里的 `GraphRAGSettings`（`KEO_` 前缀环境变量），
数据库启动环境在 `deploy/neo4j/`。对外接口与启动步骤见
`docs/C图谱RAG对外接口与启动环境说明.md`。

`GraphRAGSettings` 的 Neo4j 与 Embedding 字段是必填的，缺少 `KEO_` 前缀变量时会报错；
该实例改为首次访问 `graph_rag_cfg` 时才创建，因此不影响 B 的模块导入。

## 参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| `--window` | 3 | 相邻分句合并数。1 会把案情切得过碎 |
| `--min-chars` | 8 | 丢弃过短碎片。约 8.9% 的分句是「卡号：」这类字段标签 |
| `--max-chars` | 800 | 合并后单块的字符目标上限；单句本身超长时不切分 |
| `--max-batch-chars` | 6000 | 单次 embedding 请求的字符预算，超了接口返回 20015 |
| `--batch` | 64 | 单次 embedding 条数上限，与字符预算同时生效，任一触顶就发 |
| `--top-k` | 5 | 检索返回证据条数 |

`window` 与 `min_chars` 是在 300 篇样本上实测选的，可在 22 条 `dev_ids` 上继续调。

## 网络与代理

`models/http_client.py` 统一构造 `httpx.Client(trust_env=False)`，`Embedder` 与 `AnswerClient` 都用它。

原因：Windows 的「系统代理」会被 `urllib.request.getproxies()` 读到，httpx 默认采用，于是国内接口 `api.siliconflow.cn` 的请求被绕到代理上，长任务会中途报 `httpx.ConnectError: EOF occurred in violation of protocol`。直连国内接口更快也更稳，所以 KEO 的模型调用一律不看系统代理，也不修改系统设置。

`Embedder` 另有两层兜底：接口返回 400 时对半拆分重试（应对单次请求超限）；连接失败退避重试 3 次。宁可多几次请求，也不让一次异常打断几十分钟的建索引。

## 约定

- 不改案例编号 `source_id`。
- 不拿评估集（`eval_ids`）建库或调参；调参用 `dev_ids` 的 22 条。
- 三种回答方法共用同一提示词和同一模型，否则评测差异无法归因到检索方式。
- 数据、索引、结果都是本地文件，不提交 GitHub。`data/`、`temp`、`output/` 均已被 `.gitignore` 覆盖。
- `docs/B当前进度.md` 只追加不修改，C/D 已按其中接口对接。
