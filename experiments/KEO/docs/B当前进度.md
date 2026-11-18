# B 当前进度

> 更新：2026-11-02　范围：KEO 实验 B 部分（文本 RAG + 直接回答 + 公共回答模块）
> 对照图：`关系梳理.drawio.svg`

## 一句话

B 的答题链路（`直接回答` → `公共回答模块` → `answer.jsonl`）**已全部写完并跑通**；
`检索结果（中间格式 1.3）` 与建索引还等 A 的 `splits.json`，正式答题还等 D 的 `questions.jsonl`。

---

## 一、对照关系图的进度

图中两个大区域：左 **各个模块输入**、右 **各个模块输出**。B 的输入落在左侧 B 泳道，
B 的产出 `answer.jsonl` 落在右侧输出区末端。

| 关系图上的节点 | 归属 | 状态 |
|---|---|---|
| `案例数据.json` | A 产出，B 读 | 已就位（`output/prepared_data/`，82,210 条） |
| `三元组数据.jsonl` | A 产出，C 读 | 已就位（B 的文本 RAG **不用**它） |
| `划分名单.jsonl` → `build_ids` / `eval_ids` | A 产出 | **缺失**，B 拿不到 `build_ids` |
| `questions.jsonl` | D 产出，B/C 读 | **缺失**，B 暂用手写题替代 |
| **直接回答** | **B 实现** | 已实现并跑通 |
| **检索结果（中间格式 1.3）** | **B/C 产出** | **代码未写**（第 10 项 `retrieve.py`） |
| **公共回答模块（B 负责）** | **B 实现，C 调用** | 已实现 |
| `answer.jsonl` | B 产出，D 读 | 已产出（当前仅 `direct` 两条样例） |
| `evaluations.jsonl` / `question_metadata.jsonl` | D 内部 | 与 B 无关 |

图中 `划分名单` → 60%/40% 拆分 → `build_ids`（B 建文本索引、C 建知识图谱）+ `eval_ids`（D 出题）
这条链路，**B 只差最上游的 `build_ids` 一个文件**。

---

## 二、已写的文件

全部位于 `experiments/KEO/`。

| 文件 | 大小 | 做什么 |
|---|---:|---|
| `configs/config.py` | 5.4 KB | 全项目唯一的路径/参数定义；11 类类别；`load_splits()`、`require_api_key()` |
| `core/text_rag/cases.py` | 4.6 KB | 读案例数据 + 格式校验（字段、id 唯一、空文本、未知类别），支持按 `ids` 过滤 |
| `core/text_rag/clause.py` | 2.5 KB | 分句；`chunk_id = {source_id}_clause_{n:03d}`，与三元组 `unit_id` 同构 |
| `core/text_rag/prompt.py` | 2.4 KB | 三种方法**唯一**的提示词模板；三个解析正则 |
| `core/text_rag/answerer.py` | 5.9 KB | **公共回答模块**：拼接 → 调模型 → 解析类别与引用 → 组装格式 1.4 |
| `core/text_rag/run_b.py` | 6.7 KB | 端到端 CLI：逐题 × 逐方法，落盘 `retrieval.jsonl`（1.3）与 `answers.jsonl`（1.4） |
| `models/embedder.py` | 3.0 KB | bge-m3 封装，批量向量化并按 `index` 排序 |
| `models/llm_client.py` | 3.9 KB | 统一回答模型入口；异常转 `AnswerError`，记录耗时 |

> `core/graph_rag/node_mapper.py`、`models/graph_schema.py`、`models/111.py` 属 C，不在本文范围。

---

## 三、对外提供的接口

| 接口 | 签名 | 谁用 |
|---|---|---|
| **公共回答模块** | `answer_one(*, run_id, question_id, question, question_type, method, retrieval, client) -> dict` | **C 必用**：C 产出 1.3 后直接调用，不必关心提示词与解析 |
| 分句 | `split_clauses(source_id, text) -> list[dict]` | C 可选：保证 B/C 的 chunk 与 unit 编号同源 |
| 数据读取 | `load_cases(path=None, ids=None) -> list[dict]` | C / D 可选 |
| 全局配置 | `config.DATA_DIR` `config.load_splits()` `config.CATEGORIES` 等 | 全组 |
| **交付文件** | `answers.jsonl`（格式 1.4） | **D** |
| 自查文件 | `retrieval.jsonl`（格式 1.3） | C / 自查；D 不单独读，只通过 1.4 的 `retrieval` 字段看到 |

**B 是 `公共回答模块` 的唯一维护方**：C 的 `graph_rag` 检索结果最终也要走这里才能变成 D 能读的回答。

---

## 四、需要别人提供的接口与文件

| 需要什么 | 谁提供 | B 用在哪 | 没有时的替代 |
|---|---|---|---|
| `configs/splits.json`（`build_ids` / `eval_ids`） | **A** | 建文本索引、检索时过滤 | `--debug-all-cases` 建全量调试索引 + `--debug-index` 检索 |
| `questions.jsonl`（格式 1.2） | **D** | 正式答题 | 手写 2 题（见第六节） |
| C 的检索结果（格式 1.3，`graph_rag`） | **C** | 第三组回答要进公共回答模块 | 暂无，B 只负责 `direct` 与 `text_rag` |

---

## 五、自拟文本（团队需评审后锁定）

| 文本 | 位置 | 性质 |
|---|---|---|
| **提示词正文** | `prompt.py::_TEMPLATE` | **纯自拟，且会直接影响三组回答的分数** |
| 三个输出标签 `类型判定：` `证据说明：` `应对措施：` | `prompt.py` | 自拟（对应 D 的五维评分维度） |
| 空上下文文案 | `prompt.py` | 自拟 |
| 目录 / 集合命名 `prepared_data` `text_index` `keo_text` `results` | `config.py` | 自拟，无实质影响 |
| `run_id` 格式 `run_YYYYmmdd_HHMMSS` | `run_b.py` | 自拟 |
| 11 个类别名 | `config.py::CATEGORIES` | 从案例数据抄录，非自拟 |
| 分句正则、`chunk_id` 命名 | `clause.py` | 抄自 `app/relationextraction/code/llmuse.py`，非自拟 |
| 上下文格式 `[E001] 案例case_xxx中，…` | 检索上下文 | 取自格式 1.3 示例 |
| 错误类型 `MODEL_TIMEOUT` / `GENERATION_ERROR` / `RETRIEVAL_ERROR` | `llm_client.py` | 取自规范 §8 |

**提示词是唯一会改变实验结论的自拟文字，建议组内评审后再冻结。**

---

## 六、暂时用替身替代的部分

| 替身 | 替代什么 | 何时替换 | 风险 |
|---|---|---|---|
| 手写 2 题 `questions.jsonl`（存 `%TEMP%`） | D 的正式题库 | D 交付后直接替换 | 无，代码不动 |
| `--debug-all-cases` 建的全量索引（`keo_text_debug`） | 按 `build_ids` 建的正式索引（`keo_text`） | A 交付后重建正式索引 | **调试索引必须删除，两份索引不得混用** |
| `DIRECT_RETRIEVAL` 空壳（`not_applicable` / 空上下文 / 空证据 / 0 耗时） | 直接回答的检索结果 | 永久如此，规范 §5 明确要求 | 无 |
| `run_demo` 产出的 `answers.jsonl`（2 条） | 正式 `run_id` 的全量回答 | 正式跑时覆盖 | 无 |

---

## 七、已完成的验证

| 验证项 | 方法 | 结果 |
|---|---|---|
| 案例数据 | `python -m experiments.KEO.core.text_rag.cases` | 82,210 条；重复/缺字段/空文本/未知类别 **全为 0** |
| 坏数据能否被抓 | 造 4 类错误的 JSON 跑校验 | 四类问题全部列出，退出码 1 |
| 分句与 `unit_id` 对齐 | 200 篇案例，比对三元组 `evidence.unit_id` | **200/200 覆盖，671 个 unit_id 无一缺失** |
| embedding 连通性 | `python -m experiments.KEO.models.embedder --live` | 维度 **1024**；`embed_one` 与批量结果逐位相同 |
| 提示词渲染 | `python -m experiments.KEO.core.text_rag.prompt --with-context` | 425 字，11 类齐全，`[E00x]` 正确插入 |
| 回答模型 | `python -m experiments.KEO.models.llm_client --live` | 单次 3.9 s；三行输出正则均命中 |
| 公共回答模块 | `python -m experiments.KEO.core.text_rag.answerer --selftest` | 5 条断言全过（含 `E099` 越界引用被过滤） |
| 端到端（direct） | `python -m experiments.KEO.core.text_rag.run_b --methods direct` | 2 题，失败 0，解析失败 0，11.6 s |

---

## 八、下一步

1. **第 10 项 `retrieve.py`** —— 现在就能写，逻辑不依赖数据
2. **第 9 项 `build_index.py`** —— 需 A 的 `splits.json`；可先用 `--debug-all-cases` 打通
3. A 交付 `splits.json` 后：删调试索引 → 建正式索引 → 跑 `text_rag`
4. D 交付 `questions.jsonl` 后：正式跑 `direct` + `text_rag`，产出交给 D

## 九、常用命令

```powershell
# 数据体检
.\.venv\Scripts\python.exe -m experiments.KEO.core.text_rag.cases

# 看分句
.\.venv\Scripts\python.exe -m experiments.KEO.core.text_rag.clause --limit 1

# 接口连通性
.\.venv\Scripts\python.exe -m experiments.KEO.models.embedder --live
.\.venv\Scripts\python.exe -m experiments.KEO.models.llm_client --live

# 公共回答模块离线自测
.\.venv\Scripts\python.exe -m experiments.KEO.core.text_rag.answerer --selftest

# 只跑直接回答（不需要索引与划分名单）
.\.venv\Scripts\python.exe -m experiments.KEO.core.text_rag.run_b --questions q.jsonl --methods direct
```

> 数据放 `output/prepared_data/`（只读，勿删）；派生产物在 `output/index/` 与 `output/results/`。
> 终端中文乱码先执行 `chcp 65001`。

## 十、v1.1 数据到货后的进展（2026-11-08 追加）

### 10.1 到货清单

| 来源 | 内容 |
|---|---|
| A | `temp/person_a_v1.1_keo_完整交付包/keo/`：案例数据.json 82,210、三元组数据.jsonl 1,008,822、划分名单.json（build 49,289 / eval 32,859）、实体别名映射.json |
| A | `temp/person_a_v1.1_b_建库原文/b/`：cases_build.jsonl 49,289 篇、build_ids.txt、dev_ids.txt 22 条 |
| D | `temp/D正式题库/`：Demo 203 题、全量 32,862 题 |
| C | `temp/C图谱RAG调用接口.md`，`api.py` 尚未实现 |

两个原阻塞点已解除：划分名单字段与 `load_splits()` 完全兼容；D 题库字段与格式 1.2 一致。

原文第九节写的「数据放 output/prepared_data/」已作废，数据改由 `temp/` 读取。

### 10.2 本次改动的文件

| 文件 | 动作 | 说明 |
|---|---|---|
| `configs/config.py` | 修改 | 路径层改到 `temp/`；新增 `BUILD_CASES_PATH`、`BUILD_IDS_PATH`、`DEV_IDS_PATH`、`D_QUESTION_DEMO`、`D_QUESTION_FULL`、`ALIAS_PATH`、`resolve_splits_path()`。`load_splits()` 签名与返回值未变 |
| `core/text_rag/build_data.py` | 新增 | A 专用包适配：`source_id/category/text` 归一化为 `id/案情描述/案件类别`，复用 `cases.validate_cases` |
| `core/text_rag/retrieve.py` | 新增 | 格式 1.3 检索实现：`open_collection` / `retrieve` / `retrieval_record` |
| `core/text_rag/build_index.py` | 新增 | 建索引：跨案例攒批、断点续跑、`window` 与 `min_chars` |

### 10.3 对外接口的影响

- 无破坏性变更。`answer_one()` 签名不变，`answers.jsonl` 字段不变，D 无需改动。
- `retrieve()` 是新增模块，调用方需传入 `Embedder` 实例。
- 待接入 C 时：`GraphRAG.retrieve` 是 async，而 `run_b` 目前同步，接入时需改为 asyncio 驱动。

### 10.4 实测数据

| 项 | 结果 |
|---|---|
| 建库数据校验 | 49,289 篇，重复/缺字段/空文本/未知类别 全 0；id 与 `build_ids.txt` 完全一致；dev 22 条全部属于建库集 |
| 建索引 window=1 min_chars=0 | 300 篇 → 1,493 chunk，0.7 分钟 |
| 建索引 window=3 min_chars=8 | 300 篇 → 569 chunk，0.2 分钟；全量预估 0.7 小时 |
| 检索 | status=ok，单次约 1.6 至 1.8 秒（含 embedding 往返） |

两次关键修正：

1. 逐篇发送 embedding 改为跨案例攒批，全量预估由 13.4 小时降到 2.3 小时。
2. 加 `window=3` 与 `min_chars=8` 后，检索命中的证据从「手段：刷单类诈骗。」这类标签句变为「受害人被以刷单的方式诈骗，共计被骗 8887 元」这类实质内容，chunk 数下降且建索引更快。

### 10.5 需要 D 澄清

D 的接口文档写「全局分析题无需执行单案检索」。若全局题完全不检索，则 `text_rag` 与 `direct` 在该题上等价，三方法对比在 3 道全局题上失效。建议确认：全局题仍检索建库的跨案知识，只是题干不含案件正文。

### 10.6 下一步

1. 用 Demo 203 题跑 `direct` 与 `text_rag`，产出 `answers.jsonl` 交 D（约 26 分钟）。
2. 正式建索引（约 0.7 小时）后跑全量题库。
3. 接 C 的 `GraphRAG`，需处理 async。

## 十一、端到端接通后的补充（2026-11-08 追加）

### 11.1 修复的问题

`retrieve()` 新增了 `embedder` 参数，但 `run_b.py` 仍按旧的三个位置参数调用，导致 `text_rag` 的每一条都返回 `status=error`、`elapsed_ms=0`。已在 `run_b.py` 中创建 `Embedder` 实例并作为第三个参数传入。

这个错误没有抛异常，而是被 `retrieve` 的异常兜底转成了 `error` 字段 —— 符合规范（失败不得伪装成空结果），但也说明只看「失败 0」不足以判断链路正常，必须核对 `retrieval.status`。

### 11.2 端到端验证结果

命令：`run_b --questions temp/D正式题库/Demo版/questions.jsonl --methods direct,text_rag --limit 2 --debug-index`

| 记录 | 结果 |
|---|---|
| q001 direct | status=ok，parse=ok，`retrieval=not_applicable`，cites=[] |
| q001 text_rag | status=ok，parse=ok，`retrieval=ok`，cites=['E003','E005'] |
| q002 direct | status=ok，parse=ok，`retrieval=not_applicable`，cites=[] |
| q002 text_rag | status=ok，parse=ok，`retrieval=ok`，cites=['E002'] |

检索每条返回 5 条证据，耗时 1.2 至 1.7 秒；4 条回答失败 0、解析失败 0、带引用 2。

说明：`direct` 与 `text_rag` 对同一题给出的类别判定一致，差别在 `text_rag` 附带可核对的 `cited_evidence_ids`，这正是三方法对比要观察的差异。

### 11.3 尚未完成

- 本次用的是 300 篇的调试索引（569 条 chunk），`--debug-index` 不按 `build_ids` 过滤；**正式结果必须用正式索引重跑**。
- Demo 203 题全量、以及正式索引（49,289 篇，约 0.7 小时）都还没跑。

## 十二、数据目录改造（2026-11-08 追加，取代第十节做法）

### 12.1 变更原因

第十节让运行期直接读 `temp/`，这个做法是错的。`temp/` 是他人暂存交接目录，随时可能被清空或覆盖，运行期依赖它等于把实验挂在不可控的位置上。改成工程做法：一次性把 B 需要的文件导入项目内数据目录并登记校验信息，之后运行期只读项目内数据。

`temp/` 只在 `core/ingest.py` 里作为导入源出现，其他模块不得引用。

### 12.2 新结构

```text
experiments/KEO/data/v1.1/
├─ manifest.json          导入登记：版本、时间、每个文件的 SHA256 与条数
├─ build/                 cases_build.jsonl、build_ids.txt、dev_ids.txt
├─ splits/                划分名单.json
├─ questions/             questions_demo.jsonl、questions_full.jsonl
└─ full/                  A 的完整包，仅 --include-full 时导入，B 不使用
```

`data/` 已被 `.gitignore` 第 36 行的 `data/` 规则覆盖，不入库。

### 12.3 新增与修改

| 文件 | 动作 | 说明 |
|---|---|---|
| `core/ingest.py` | 新增 | 导入交接包，三层校验，写 `manifest.json`；支持 `--dry-run`、`--force`、`--include-full` |
| `configs/config.py` | 修改 | 新增 `DATASET_VERSION`、`DATA_DIR`、`DATA_MANIFEST`、`STAGING_ROOT`、`FULL_DIR`；`BUILD_CASES_PATH`、`BUILD_IDS_PATH`、`DEV_IDS_PATH`、`SPLITS_PATH`、`D_QUESTION_*` 全部改指 `data/v1.1/`；`CASES_PATH`、`TRIPLES_PATH`、`ALIAS_PATH` 改指 `data/v1.1/full/`，B 不使用 |

### 12.4 导入与校验结果

- 导入 6 个文件，共约 88 MB。
- A 的 manifest 提供 SHA256 的 3 个文件（`cases_build.jsonl`、`build_ids.txt`、`dev_ids.txt`）全部匹配。
- 条数一致性：`build_ids.txt` 49,289 = 划分名单 `build_ids` 49,289 = `cases_build.jsonl` 49,289，为真。
- 其余条数：dev 22、eval 32,859、Demo 题库 203、全量题库 32,862。
- 下游复测：`build_data` 退出码 0，49,289 篇 0 异常；`run_b` 端到端 4 条回答 14.8 秒、失败 0、带引用 2。

### 12.5 未受影响的既有接口

本次改造只动路径，以下均未变，C/D 无需适配：

- `answer_one()` 签名与返回结构
- `answers.jsonl` 的 12 个字段
- `retrieval_record()` 与格式 1.3 的五个字段
- `split_clauses()` 与 `chunk_id` 命名规则
- `load_splits()` 签名与返回值

## 十三、正式索引建成与三次故障（2026-11-08 追加）

### 13.1 结果

| 项 | 值 |
|---|---|
| chunk 数 | 100,751 |
| 覆盖案例 | 49,289 篇，`done_cases.json` 无遗漏 |
| 磁盘占用 | 725.2 MB（`chroma.sqlite3` 312.8 MB、`data_level0.bin` 404.4 MB、`index_metadata.pickle` 6.1 MB、`done_cases.json` 0.7 MB） |
| 耗时 | 约 28 分钟，速率 25.3 篇/秒 |
| 正式检索验证 | 2 题 `text_rag`，各返回 5 条证据，耗时 707 / 145 毫秒，回答分别引用 4 条与 3 条 |

### 13.2 三次故障与修复

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | `ERROR 20015 parameter is invalid`，停在 5,000 篇 | 单次 embedding 请求的文本总量超接口上限 | 加 `--max-chars`（合并后单块 800）与 `--max-batch-chars`（单请求 6,000）；写入由 `add` 改 `upsert`，续跑不冲突 |
| 2 | `httpx.ConnectError: EOF occurred in violation of protocol`，停在 7,600 篇 | Windows 系统代理（`ProxyEnable=1`，`127.0.0.1:7890`）被 httpx 经 `urllib.request.getproxies()` 读取，国内接口 `api.siliconflow.cn` 的 TLS 被中途断开 | 新增 `models/http_client.py`，统一 `httpx.Client(trust_env=False)`；`Embedder` 与 `AnswerClient` 均改用；并加连接失败退避重试 3 次 |
| 3 | `'Embedder' object has no attribute 'embed_one'`，检索全部 error | 编辑时把 `embed_one` 误并入 `_MergedEmbedding` 类 | 移回 `Embedder`，并逐个补测公开方法 |

### 13.3 一处测试口径修正

`embed_one` 与批量结果的分量差约 1.4e-3（批量推理的浮点累加顺序不同），原先断言「逐位差小于 1e-6」不现实。改为余弦相似度判定，实测 0.999952，判定为一致。

### 13.4 教训

第 3 个 bug 说明：重构后必须把每个公开方法都跑一遍。建索引只用到 `embed()`，所以索引本身有效（100,751 条），但 `retrieve()` 全部失败；而 `run_b` 的汇总行仍显示「失败 0」，因为异常被转成了 `error` 字段。**判断链路是否正常必须核对 `retrieval.status`，不能只看失败计数。**

### 13.5 副作用提醒

系统代理同样会影响主项目 `app` 的模型调用（同一套 OpenAI 客户端默认 `trust_env=True`），是否有必要单独处理待定。

## 十四、全链路打通与产出（2026-11-17 追加）

### 14.1 金标还原（格式 1.5）

D 未交付 `question_metadata.jsonl`，交接包里也没有。新增 `core/ingest_question_metadata.py`，从 A 的 `cases_eval.jsonl` 按题干前缀反推真实类别：

| 题库 | 案例题 | 匹配成功 | 匹配率 |
|---|---:|---:|---:|
| Demo | 200 | 200 | 100% |
| 全量 | 32,859 | 32,859 | 100% |

产出 `data/v1.1/questions/question_metadata.jsonl`（203 行）与 `question_metadata_full.jsonl`（32,862 行）。**需 D 复核后才算正式底牌。**

匹配踩过一个坑：最初按案例前 80 字建桶，短案例整篇不足 80 字，题干前 80 字混入指令文字导致桶键对不上；改为 30 字桶键 + 短案例线性匹配后达到 100%。

### 14.2 D 的脚本改造

`run_eval.py`、`metrics.py`、`report.py` 原来是写死 `C:/Users/ZZY/...` 与 `sk-...` 的本地脚本，改为配置驱动：读 `config` 与根 `.env`、走 `make_http_client()`（忽略系统代理）、支持 `--resume` / `--workers` / `--limit`。**评分细则与输出格式保持原样**，未改动 D 的评分口径。

`metrics.py` 在原 Accuracy 之外补了 Macro-F1 与逐类明细（清单要求，原脚本缺）。`report.py` 增加按维度拆分与运行情况。

### 14.3 B 的补跑

`--resume` 补齐 78 条网络失败后，Demo 全量 **406 条回答、失败 0、解析失败 0、带引用 171 条**。

### 14.4 环境：Neo4j 改为原生运行

Docker 不可用：`VirtualMachinePlatform` 与 `Microsoft-Hyper-V` 均为 Disabled，开启需重启，不能在会话内完成。

改法：取 Neo4j Desktop 缓存中的 `neo4j-enterprise-5.24.0`，复制到 `data/neo4j-local/neo4j-5.24`，用本机 `D:\jdk-21.0.11` 运行，并接受评估许可（30 天）。

注意：环境变量覆盖只作用于 routing 端口，主 Bolt 仍是 **7687**，故 `KEO_NEO4J_URI` 设为 `bolt://127.0.0.1:7687`（C 文档写的 7688 是 docker compose 的端口映射）。

### 14.5 C 的建图

| 项 | 值 |
|---|---|
| 入图三元组 | 604,035 |
| 跳过评估三元组 | 404,787 |
| 耗时 | 251 秒 |
| 节点 / 关系 | 2,233,889 / 2,377,895 |
| 待向量化实体 | 461,292 |

`node_index` 速率约 20 节点/秒，全量预计 **6 小时**。未完成前知识库状态为 `updating`，C 的 `api.py` 会拒绝检索——这是 C 自己的保护逻辑，不是故障。

### 14.6 Demo 实测结果

分类指标：

| 方法 | 题数 | 正确 | Accuracy | Macro-F1 |
|---|---:|---:|---:|---:|
| direct | 200 | 160 | 80.00% | 0.7485 |
| text_rag | 200 | 160 | 80.00% | 0.7166 |

五维评分（满分 25）：

| 方法 | 平均总分 | 类型判定 | 证据指向 | 措施正确性 | 可执行性 | 完整性 |
|---|---:|---:|---:|---:|---:|---:|
| direct | 17.24 | 4.17 | 0.99 | 3.83 | 4.05 | 4.03 |
| text_rag | 19.19 | 4.15 | 2.64 | 3.94 | 4.08 | 4.21 |

检索：text_rag 203 次全部 ok，带引用 171/203，平均检索 2066 毫秒，平均生成 3699 毫秒。

结论：加上检索后总分提升 1.95 分，主要来自「证据指向」（0.99 到 2.64）；但 Macro-F1 反而下降 0.03，说明检索让错分更集中在稀有类别，这一点值得在报告里单独说明。

### 14.7 如何调用

```powershell
# B：公共回答模块（C 用）
python -m experiments.KEO.core.text_rag.run_b --questions <题库> --methods direct,text_rag --resume

# C：图谱检索（async）
python -m experiments.KEO.core.graph_rag.api --question-id q001 --question-type case_judgment --query "..."

# D：评分与指标
python -m experiments.KEO.core.evaluation.run_eval --workers 3 [--resume]
python -m experiments.KEO.core.evaluation.metrics
python -m experiments.KEO.core.evaluation.report
```

### 14.8 node_index 首次失败与修复（2026-11-17 追加）

第一次 `node_index` 在约 2,500 个节点后崩溃：知识库状态被置为 `error`，错误信息 `节点向量同步失败：BadRequestError`。

根因：C 用的是 `app/models/SiliconFlowEmbedding.py`，该组件**没有重试、没有按批拆分、且默认走系统代理**（`trust_env=True`）。接口返回一次 400 即整段中断，461,292 个节点的长任务无法承受这种脆弱性。

修复（用与 B 相同的三层防护，公开接口保持不变）：

| 防护 | 作用 |
|---|---|
| `httpx.Client(trust_env=False)` | 不被 Windows 系统代理绕行，避免 TLS 中断 |
| 400 对半拆分重试 | 单次请求过大时自动缩小批次，不再整段失败 |
| 连接失败退避重试 3 次 | 网络抖动不打断长任务 |
| 单条失败的三级兜底 | 空文本替换为「未知」→ 超长截断到 1,200 字 → 仍失败才抛错（含内容前缀，便于定位） |

压测覆盖四种边界：普通、含空串、3000 字超长、16 条批量，全部返回 1024 维。

另将 `KEO_EMBEDDING_BATCH_SIZE` 设为 8，并把知识库状态从 `error` 复位为 `updating` 后 `--resume` 续跑。

续跑实测速率约 60 节点/秒，461,292 个节点预计 **2.1 小时**（原先按 20 节点/秒估算的 6 小时偏保守）。完成后知识库状态会变为 `ready`，C 的 `api.py` 才能开始检索。

### 14.9 第三组方法的接线与两处修正（2026-11-17 追加）

**能否只跑部分索引？不能。** C 的检索门禁是「向量指纹」而非仅状态：`graph_retriever` 比对 `embedding_fingerprint`，该字段只在**全量同步完成**时写入。把状态改成 `ready` 也没用，实测仍返回 `知识库状态不允许检索`。所以部分索引无法用于正式或试验性检索。

**做了什么验证**：改为验证真正的接口契约——用一条 C 风格的格式 1.3（带 `triple_ids`）喂给 B 的 `answer_one`，确认：

- `method=graph_rag`、`predicted_category` 正确解析、`cited_evidence_ids` 正确过滤
- `retrieval` 原样透传，`triple_ids` 未被丢弃
- 12 个字段与格式 1.4 一致

**run_b 已支持第三组**：`--methods` 增加 `graph_rag`，整轮共用一个 asyncio 事件循环（GraphRAG 要求实例在同一循环复用），结束时 close。

接线过程修了两个 bug：

| bug | 现象 | 原因 |
|---|---|---|
| `retrieval_record` 未定义 | 单跑 graph_rag 直接崩 | 它只在 text_rag 分支内局部导入，函数内局部名遮蔽了模块级导入 |
| 检索失败仍出答案 | `status=ok`、答案非空 | `answer_one` 没区分「direct 的空上下文」与「检索失败」 |

**规范修正**：格式 1.4 §8 要求检索失败阻止生成，现已实现——`retrieval.status=error` 时直接返回空白 error 记录，生成耗时为 0，不再调用模型。

**新增工具**：`tools/watch_progress.py`（每 5 秒刷新的进度窗口）、`tools/看进度.cmd`（双击运行）、`tools/node_index_supervisor.py`（断网自动续跑，最多 20 次，退避到 5 分钟）。

**进度修正**：实测约 18 节点/秒，461,292 个实体约需 6–7 小时；此前写的 2.1 小时是拿 90 秒瞬时速率推算，偏乐观。

### 14.10 更正：那句话不存在，闸门实际有三重（2026-11-17 追加）

14.9 里写的「返回 `知识库状态不允许检索`」是**笔误**。C 的代码里没有这句话，实际是 `api.py` 的统一包装：`图谱检索失败（RuntimeError），请检查知识库状态及服务配置。`，真实原因被包在里面。

按要求做过四轮「临时置 ready 跑 2 题再改回」的验证，全部失败，且逐轮排除了原因：

| 轮次 | 做法 | 结果 |
|---|---|---|
| 1 | 只改 `status=ready` | 失败——索引进程同时在跑，状态被立刻改回 |
| 2 | 停掉子进程后改 `status` + `target_fingerprint` | 失败 |
| 3 | 改用 `NodeIndex` 现场算出的实时指纹与 index_key | 失败 |
| 4 | 确认无进程竞争后再试 | 失败；且连 `NodeIndex.search` 自身都报「图谱索引身份与配置不一致」 |

**实际闸门是三重身份校验**，任一项不一致即拒：

1. `state.status == 'ready'`
2. `state.embedding_fingerprint == node_index.fingerprint`（实时计算，非库内 target）
3. Chroma 集合元数据里的 `keo_fingerprint` 与当前配置一致（`search` 自身的检查）

部分索引下第 3 项必然不一致——集合元数据是首次创建时写入的，只有全量同步结束时才会被 C 自己改写并对齐。

**结论**：部分验证在 C 的架构下不可行，这不是开关问题。四轮尝试后知识库状态已还原为 `updating`、临时指纹已移除，索引已恢复续跑。等全量完成，C 会把三重身份写成一致，届时一次跑通。
