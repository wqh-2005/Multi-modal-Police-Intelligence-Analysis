# KEO 实验启动与数据交付指南

本目录用于复用诈骗案例及已有三元组，对比直接回答、文本 RAG、图谱 RAG。本文说明当前代码的启动方式、A/D 数据放置、建库、运行与恢复，供成员从仓库根目录操作。

当前主要入口是 `KEO/core/run_experiment.py`，不是原业务系统的 Web 服务。运行本实验不要求启动原业务后端、OCR 或重新抽取三元组。

> 当前路径以本文和 `KEO/configs/config.py` 为准。旧 `KEO/README.md` 中的 `data/v1.1/`、旧暂存包导入说明属于历史流程；当前统一输入目录为 `KEO/output/v1.0/`。不要直接按旧路径再整理一套数据。

## 1. 先选择启动方式

| 场景 | 需要执行 |
| --- | --- |
| 首次拉取、没有数据与索引 | 准备环境 → 接收数据 → 新建实验 Neo4j → B 建库 → C 导入/向量同步/全文索引 → 运行 |
| 数据库与索引已经准备好 | 配置正确的连接、原向量目录与集合 → 直接运行 |
| 只测试直接回答 | 准备 Python、模型配置与题库 → 运行 `--methods direct` |

`output/` 已在 `.gitignore` 中，拉取代码不会自动获得案例、题库、Chroma 或历史结果。向 A/D 或实验负责人取得这些文件。

## 2. 目录与成员职责

```text
experiments/
├── README.md                     # 本文
└── KEO/
    ├── configs/config.py         # B/C 共用配置
    ├── core/
    │   ├── data/                 # A 的数据准备工具
    │   ├── text_rag/             # B 文本库、检索与公共回答
    │   ├── graph_rag/            # C Neo4j、节点向量、证据召回和上下文
    │   ├── evaluation/           # D 分类指标、问答评分与报告
    │   └── run_experiment.py     # 三种方法统一执行入口
    ├── models/                   # 数据模型、Embedding 和回答客户端
    ├── tests/                    # 离线回归测试，建议提交代码仓库
    ├── deploy/neo4j/             # 独立实验库的 Docker Compose
    ├── guodu/                    # 本机接收的 A 原始交付包，不是运行入口
    └── output/
        ├── v1.0/                 # 当前统一输入及按批次保存的实验结果
        ├── index/text_index/     # B 默认正式索引
        └── results/              # 统一入口默认结果根目录
```

Neo4j 保存完整图谱、来源映射与证据；Chroma 保存向量；NetworkX 只处理查询得到的小子图。C 返回格式 1.3，最终回答由 B 维护的公共模块生成格式 1.4，D 接收答卷评分。

## 3. A 的交付文件怎么放

`v1.0` 是本项目的目录版本，A 原始交付 manifest 记录的数据版本为 `1.1`。两者含义不同，不要改写原始版本声明。

```text
experiments/KEO/output/v1.0/
├── build/
│   ├── cases_build.jsonl         # B：建库原文
│   ├── triples_build.jsonl       # C：同批案例的全部三元组
│   ├── build_ids.txt             # BC 共用名单，一行一个案例 ID
│   └── dev_ids.txt               # 调参名单，属于建库侧
├── splits/
│   └── 划分名单.json             # 含 build_ids、eval_ids
├── eval/
│   ├── cases_eval.jsonl          # D 出题/评分资料，不进知识库
│   └── eval_ids.txt
├── full/
│   ├── 案例数据.json             # 完整案例主表，JSON 数组
│   ├── 三元组数据.jsonl          # 全量归档，非默认建库输入
│   └── 实体别名映射.json
├── questions/                    # D 另外交付
│   ├── Demo版/
│   │   ├── questions.jsonl
│   │   └── question_metadata.jsonl       # D 评分参考，需另行交付
│   ├── 全量版/
│   │   ├── questions_full.jsonl
│   │   └── question_metadata_full.jsonl  # D 评分参考，需另行交付
│   └── D 评测与打分接口.md
├── delivery_docs/
│   ├── b/                       # 原 b 包 manifest 与 README
│   ├── c/                       # 原 c 包 manifest 与 README
│   ├── d/                       # 原 d 包 manifest 与 README
│   ├── keo/                     # 原完整包 README
│   ├── 归一化规则.md
│   └── 交付说明.md
├── manifest.json                # 整理后的来源映射与校验值
└── README_目录说明.md
```

metadata 是预期路径，不代表文件已经交付。没有 metadata 也能检索和生成回答，但不能完成依赖金标的正式评分。

### 3.1 从原始 A给出的数据包 复制到哪里

原始 `b/c/d` 是完整包的角色切片，不是彼此独立的三份数据集。保留原件，按下面映射复制：

| A 原交付路径，相对于 `guodu/` | 目标，相对于 `output/v1.0/` |
| --- | --- |
| `b/cases_build.jsonl` | `build/cases_build.jsonl` |
| `b/build_ids.txt`、`c/build_ids.txt` | 核对一致后只存一份 `build/build_ids.txt` |
| `b/dev_ids.txt` | `build/dev_ids.txt` |
| `c/triples_build.jsonl` | `build/triples_build.jsonl` |
| `d/cases_eval.jsonl` | `eval/cases_eval.jsonl` |
| `d/eval_ids.txt` | `eval/eval_ids.txt` |
| `keo/划分名单.json` | `splits/划分名单.json` |
| `keo/案例数据.json` | `full/案例数据.json` |
| `keo/三元组数据.jsonl` | `full/三元组数据.jsonl` |
| `keo/实体别名映射.json`、`c/实体别名映射.json` | 核对一致后只存一份 `full/实体别名映射.json` |
| 各角色原始 manifest、README | `delivery_docs/对应角色/` |
| `c/归一化规则.md`、`keo/交付说明.md` | `delivery_docs/` |

D 的 `Demo版/`、`全量版/` 子目录直接放入 `questions/`，不要擅自展平或改题号。

当前冻结包应有：49,289 个建库案例、604,035 条建库三元组、32,859 个评估案例。60%/40%按案例划分，不要求实体节点数量等于三元组数量。

### 3.2 不同文件不能互相改名替代

B 的 `cases_build.jsonl` 每行是：

```json
{"source_id":"case_000001","category":"刷单返利类","text":"案情原文……","split":"build"}
```

C 的 `triples_build.jsonl` 每行包含 `triple_id/source_id/head/relation/tail/factuality/evidence` 等原有字段。完整案例主表是另一种 JSON 数组格式，不能直接改名为 B 的建库 JSONL。

`划分名单.json` 的核心结构：

```json
{"build_ids":["case_000001"],"eval_ids":["case_000002"]}
```

检查 build_ids 与 eval_ids 不重叠；B 原文与 C 三元组覆盖同一建库名单。评估原文、metadata 和参考措施不能作为回答资料。

已收到整理好的 `v1.0` 包时，直接放到上述路径。旧 `core/ingest.py` 针对历史暂存包结构，不是当前 `guodu/b,c,d,keo` 的通用整理入口。

## 4. 准备 Python 环境

以下命令全部在仓库根目录运行，不是在 `experiments/` 或单个代码目录运行。示例使用 Windows PowerShell，成员按实际克隆位置修改第一行。

```powershell
cd D:\Multi-modal-Police-Intelligence-Analysis
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

本机验证环境是 Python 3.10。仅运行 KEO 可先安装下面这些依赖，无需先装原业务系统的 GPU/OCR 全套依赖：

```powershell
python -m pip install "neo4j==6.2.0" "chromadb==1.5.9" "networkx==3.4.2" "openai==2.49.0" "httpx==0.28.1" "pydantic==2.13.4" "pydantic-settings==2.14.2" "python-dotenv==1.2.2" "langchain-core==1.5.1" "langchain-chroma==1.1.0" "langchain-openai==1.4.1"
```

C 当前复用了 `app/models/SiliconFlowEmbedding.py`，该文件还导入 LangChain Chroma/OpenAI 包，所以即使只跑 C，也需要相关依赖。上述安装命令依据当前依赖与已安装环境整理，尚未在另一台全新机器上重装验证。

PowerShell 若将管道中的中文变成问号，可在当前终端执行：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = "utf-8"
```

## 5. 根目录 .env 配置

配置文件位于仓库根目录 `.env`。没有该文件时可参考根目录 `.env.example` 创建；已有文件时添加或修改对应项目，不要覆盖原业务配置。不要提交真实密钥与密码。

以下示例对应“首次部署 Compose 实验库”，向量目录也使用新的统一路径：

```dotenv
KEO_DATASET_VERSION=v1.0

SILICONFLOW_API_KEY=YOUR_API_KEY
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
KEO_ANSWER_MODEL=Qwen/Qwen2.5-32B-Instruct
RAGENGING_MODEL=BAAI/bge-m3
KEO_ANSWER_MAX_TOKENS=2048

KEO_NEO4J_URI=bolt://localhost:7688
KEO_NEO4J_USER=neo4j
KEO_NEO4J_PASSWORD=ReplaceWithYourOwnLongPassword
KEO_NEO4J_DATABASE=neo4j

KEO_EMBEDDING_API_KEY=YOUR_API_KEY
KEO_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
KEO_EMBEDDING_MODEL=BAAI/bge-m3
KEO_EMBEDDING_DIMENSION=1024

KEO_CHROMA_PERSIST_DIR=experiments/KEO/output/v1.0/node_vectors
KEO_CHROMA_COLLECTION=keo_graph_nodes
KEO_EVIDENCE_RECALL_ENABLED=true
KEO_EVIDENCE_FULLTEXT_ANALYZER=cjk
```

注意 `RAGENGING_MODEL` 是现有 B 代码使用的拼写；C 的 Embedding 参数有独立 `KEO_` 前缀，两边都要配。

默认输入路径已指向 `output/v1.0`，一般无需再写 KEO_TRIPLES_PATH、KEO_SPLIT_PATH、KEO_CASES_PATH。若之前设过这些环境变量，检查是否仍指向旧小样本。当前终端 `$env:...` 通常优先于 `.env`，新开终端后这些临时值会消失。

当前实验实际使用一个回答模型 Qwen2.5-32B，三种方法不是三个不同模型；Embedding 使用 bge-m3。全文索引的 cjk 是分析器，不是大语言模型。

## 6. 启动独立 Neo4j

### 6.1 成员首次部署

先启动 Docker Desktop。将 `experiments/KEO/deploy/neo4j/.env.example` 复制为同目录 `.env`，填写密码，并与根目录 `.env` 中 KEO_NEO4J_PASSWORD 一致。

```powershell
docker compose --env-file experiments/KEO/deploy/neo4j/.env -f experiments/KEO/deploy/neo4j/compose.yaml up -d
docker compose --env-file experiments/KEO/deploy/neo4j/.env -f experiments/KEO/deploy/neo4j/compose.yaml logs --tail 40
```

看到启动成功后，Browser 地址为 `http://localhost:7475`，Bolt 地址为 `bolt://localhost:7688`，数据库名为 `neo4j`，用户名为 `neo4j`。Compose 中的资源设置是初始值，大规模导入时应根据机器资源检查内存和耗时。

### 6.2 复用负责人此前运行的实例

历史实验使用另一套端口和索引位置，不要和上面的首次部署配置混用：

| 项目 | 既有实验值 |
| --- | --- |
| Docker 原容器名 | `keo-smoke1000`；若手动改名，使用改后的名称 |
| Browser | `http://localhost:7476` |
| Bolt | `bolt://localhost:7689` |
| 数据库名 | `neo4j` |
| C 向量目录 | `experiments/KEO/output/smoke1000/node_vectors` |
| C 集合 | `keo_graph_smoke1000` |
| B 完整索引 | `experiments/KEO/output/v1.0/index/text_index` |
| B 集合 | `keo_text` |

复用时将根 `.env` 的 URI、密码、C 向量目录和集合改成实际原值。`smoke1000` 是历史名称，这个实例后续已经用于完整建库，不代表数据库仅有1000个案例。

Docker 卷、Neo4j 数据与 Chroma 不是 Git 代码；只拉代码无法复用这些存储。对另一台机器，需要自行建库，或由负责人安排数据库与向量的配套备份恢复。不能只复制一个 Chroma 目录就假定能匹配另一份 Neo4j 的同步状态。

## 7. B 首次建立文本向量库

默认读取 `output/v1.0/build/cases_build.jsonl`，默认写入 `output/index/text_index/`：

```powershell
python -m experiments.KEO.core.text_rag.build_index --batch 8 --max-batch-chars 3000
```

日志应显示 49,289 篇。中断后可使用相同参数重跑，已有完成记录会复用；原文、Embedding、切块规则变化时不要盲目沿用旧索引，应使用新目录。

如需与负责人历史实验一样，写到 `output/v1.0/index/text_index/`，使用：

```powershell
python -c "from pathlib import Path; import sys,runpy; from experiments.KEO.configs import config; config.INDEX_ROOT=Path('experiments/KEO/output/v1.0/index'); sys.argv=['build_index','--batch','8','--max-batch-chars','3000']; runpy.run_module('experiments.KEO.core.text_rag.build_index',run_name='__main__')"
```

两条命令选择一种，不要重复建两份；后面的 `--text-index-dir` 必须对应实际位置。

## 8. C 首次导入与同步

按顺序执行，前一步成功后再继续。只复用已就绪库的成员可跳过已经完成的步骤。

### 8.1 导入三元组

```powershell
python -m experiments.KEO.core.graph_rag.grapy_builder
```

默认读取 `output/v1.0/build/triples_build.jsonl`，并按正式 build_ids 过滤。完整包预期 `status=imported`、`build_case_count=49289`、`error=null`。导入完成暂不代表向量可检索。

导入中断：确认旧进程停止，恢复原配置，重新执行同一命令。会重新扫描文件并按已有唯一 ID 增量写入，不是从中断行直接续读。不需要先清库。

### 8.2 同步节点向量

```powershell
python -m experiments.KEO.core.graph_rag.node_index
```

成功后状态应为 `ready`。中断且旧进程停止后恢复：

```powershell
python -m experiments.KEO.core.graph_rag.node_index --resume
```

恢复必须使用原 Neo4j、Embedding 配置、向量目录和集合，不能随意更换路径来绕过状态检查。日志批次从1开始是本次启动计数，不代表从头重新计算所有已确认向量。

### 8.3 初始化证据全文索引

当前 C 使用“节点向量＋原文证据全文”两路召回，首次运行还需：

```powershell
python -m experiments.KEO.core.graph_rag.evidence_index
```

确认输出索引状态为 `ONLINE`。该命令索引已有 `KEOEvidence.text`，不重新抽取数据、不调用 Embedding。分析器不可用时会列出可用项，需要核对中文支持再配置，不能随意改为默认英文分析器。查询阶段不会自动创建缺失索引。

关闭证据召回用于消融：`KEO_EVIDENCE_RECALL_ENABLED=false`；保留两路召回但关闭同案扩展：`KEO_CASE_EXPANSION_ENABLED=false`。改变实验配置后使用新 run_id。

## 9. 先跑3题，确认整条链路

下面命令对应首次部署：B 默认 `output/index/text_index`，C 使用第5节新向量路径。复制整行命令，不要复制终端的 `PS ...>`、`>>` 提示符。

```powershell
python -m experiments.KEO.core.run_experiment --questions "experiments/KEO/output/v1.0/questions/Demo版/questions.jsonl" --split "experiments/KEO/output/v1.0/splits/划分名单.json" --text-index-dir "experiments/KEO/output/index/text_index" --text-collection "keo_text" --graph-vector-dir "experiments/KEO/output/v1.0/node_vectors" --graph-collection "keo_graph_nodes" --methods direct,text_rag,graph_rag --limit 3 --run-id member_smoke_001 --output-dir "experiments/KEO/output/v1.0/results" --workers 1
```

预期输出6条检索结果、9条回答。BC 的检索结果应含 context/evidence，直接回答没有检索证据属于正常情况。

只跑直接回答，不需要 Neo4j 或向量库：

```powershell
python -m experiments.KEO.core.run_experiment --questions "experiments/KEO/output/v1.0/questions/Demo版/questions.jsonl" --methods direct --limit 3 --run-id direct_smoke_001 --output-dir "experiments/KEO/output/v1.0/results"
```

## 10. 正式扩大测试与续跑

首次部署完成后，运行全量题库前1000题，三种方法共3000条回答：

```powershell
python -m experiments.KEO.core.run_experiment --questions "experiments/KEO/output/v1.0/questions/全量版/questions_full.jsonl" --split "experiments/KEO/output/v1.0/splits/划分名单.json" --text-index-dir "experiments/KEO/output/index/text_index" --text-collection "keo_text" --graph-vector-dir "experiments/KEO/output/v1.0/node_vectors" --graph-collection "keo_graph_nodes" --methods direct,text_rag,graph_rag --limit 1000 --run-id member_compare1000_001 --output-dir "experiments/KEO/output/v1.0/results" --workers 1
```

负责人复用现有库时，改用下面的路径和集合：

```powershell
python -m experiments.KEO.core.run_experiment --questions "experiments/KEO/output/v1.0/questions/全量版/questions_full.jsonl" --split "experiments/KEO/output/v1.0/splits/划分名单.json" --text-index-dir "experiments/KEO/output/v1.0/index/text_index" --text-collection "keo_text" --graph-vector-dir "experiments/KEO/output/smoke1000/node_vectors" --graph-collection "keo_graph_smoke1000" --methods direct,text_rag,graph_rag --limit 1000 --run-id member_existing1000_001 --output-dir "experiments/KEO/output/v1.0/results" --workers 1
```

说明：

- `--limit` 是题目数量，不是建库案例数或三元组数。取前N题，不是随机采样。
- Demo有200道案例题＋3道全局题；全量有32,859道案例题＋3道全局题。先用案例题联调，全局题的统计与评分仍待完善。
- `--workers` 默认1，可改为3加速回答生成；BC检索目前各自串行。TPM限流与 token 使用量有关，并发3不保证不会限流。
- 同一批次不可启动两个进程；新实验换 run_id，保留旧结果。
- 按一次 Ctrl+C 后等待在途请求结束、保存和退出，不要立即关闭窗口。
- 下次运行完全相同命令，末尾增加 `--resume`；跳过成功记录，重试失败及未完成项。并发数可以调整。
- 不要在同一批次中途改变题库、模型、提示词、检索实现、知识库或索引。配置不一致时先定位差异，不手改 manifest 绕过检查。

## 11. 结果文件在哪里

```text
output/v1.0/results/<run_id>/
├── manifest.json          # 题库摘要、模型、检索配置
├── execution.json         # 本次并发数、是否续跑
├── checkpoint.jsonl       # 逐条持久化，可能包含失败重试前的旧记录
├── answers.jsonl          # 去重后格式1.4答卷，交给D
└── retrieval.jsonl        # 格式1.3检索结果
```

统计以 `answers.jsonl` 为准，不直接数 checkpoint 行数。存在1000条记录不等于1000题都成功，还应看 status、parse_status 和 retrieval.status。引用编号存在也不代表引用语义正确。

## 12. D 如何接收与评估

将同批次 `answers.jsonl`、对应题库及运行信息交给 D；metadata 只在评分环节读取，不作为回答输入。

配套 metadata 交付并复核后，可显式指定批次计算分类指标：

```powershell
python -m experiments.KEO.core.evaluation.metrics --answers "experiments/KEO/output/v1.0/results/member_compare1000_001/answers.jsonl" --metadata "experiments/KEO/output/v1.0/questions/全量版/question_metadata_full.jsonl" --out "experiments/KEO/output/v1.0/results/member_compare1000_001/metrics.json"
```

不要省略 --answers 后误读旧的默认结果路径。正式评测前还需完善答卷完整性检查、双评判、评分失败续跑、案例/全局题分开汇总等问题；目前评分默认模型和老师任务清单要求也并不完全一致。

## 13. 常见问题

| 现象 | 排查或处理 |
| --- | --- |
| 文件不存在 | 检查 output 没随 Git 下载、交付包层级、版本号与旧环境变量 |
| Neo4j连不上 | Docker是否启动、7475/7688与7476/7689是否混用、密码是否一致 |
| 新空库提示HEAD/TAIL等不存在 | 可能是首批写入前的查询警告；以最终导入报告为准 |
| 向量恢复配置不一致 | 恢复原目录、集合、模型、维度与服务地址，不删状态绕过 |
| 证据全文索引缺失或未ONLINE | 执行 evidence_index 初始化并等待完成 |
| HTTP429，TPM limit reached | 降低并发、等待配额窗口恢复，再 --resume；必要时增加请求节流 |
| HTTP451服务拒绝 | 保留失败记录与原因，单独统计，不反复无界重试或绕过限制 |
| 连续status=error | 及时Ctrl+C，排查首个错误，不让整个题库持续失败 |
| PowerShell提示StreamAlreadyRedirected | 不要把续行提示符 >> 复制进命令，优先复制本文单行命令 |
| 续跑提示参数或模型变化 | 对比原 manifest，检查新终端环境、题库与配置；不要直接改旧清单 |
| 想改Docker容器名 | 容器名与Neo4j数据库名不同；连接依据端口，数据库名仍使用neo4j |

## 14. 离线测试与相关文档

离线检查不访问数据库或模型API，但部分模块导入仍会加载配置，建议先完成根 `.env` 配置：

```powershell
python -m unittest experiments.KEO.tests.test_graph_context_ranking experiments.KEO.tests.test_evidence_recall experiments.KEO.tests.test_experiment_workers -v
```

相关资料：

- [团队数据接口](KEO/docs/数据结构与交付说明.md)
- [系统问题清单](../docs/question/KEO当前问题清单_2026-11-19.md)
- [三种方法200题分析](../docs/优化/v1.0/bug_三种方法200题对比分析.md)
- [BCD与公共模块优化清单](../docs/优化/v1.0/bug_BD与公共回答模块优化清单.md)

提交仓库时保留代码、tests 和说明；不要提交真实 `.env`、A/D案例数据、数据库文件、向量索引或批量答卷。复制数据后可核对交付 manifest 的 SHA256；不要仅凭目录存在就判断环境已经准备完成。
