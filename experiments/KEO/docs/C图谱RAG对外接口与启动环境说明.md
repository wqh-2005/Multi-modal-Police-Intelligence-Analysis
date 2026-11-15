# C图谱RAG对外接口与启动环境说明

更新日期：2026-11-12。依据同目录[数据结构与交付说明.md](./数据结构与交付说明.md)及当前代码编写。

本文面向B公共回答模块、A数据处理和D出题评价负责人，说明如何准备环境、启动C模块并调用接口。它不改变团队格式1.1—1.6；实现与文档不一致时应报告并共同修正，不由调用方私自增加字段。

## 1. C模块的职责和交接边界

```text
A：案例、清洗三元组、共同划分名单（格式1.1）
    ↓
C离线：按build_ids导入Neo4j → 节点描述向量同步到Chroma

D：问题（格式1.2）
    ↓
C在线：问题Embedding → 种子ID → 有限子图 → 最大生成森林 → 原文证据上下文
    ↓ 格式1.3
B公共回答模块：原问题 + context/evidence → 最终回答（格式1.4）
    ↓
D：评价及比较（格式1.6）
```

C提供检索结果，不生成最终answer_text、predicted_category或处置措施，不计算评价分数。B牵头维护公共回答生成流程，文本RAG、图谱RAG及直接回答使用同一题目、回答模型和任务要求。

当前对接形式是同进程异步Python调用，也提供命令行调试入口；没有单独的HTTP服务，不需要启动FastAPI或uvicorn才能使用C。

完整图谱、来源映射和证据在Neo4j；Chroma保存节点向量；NetworkX只处理有限检索子图。graph.json不是正式运行依赖。

## 2. 文件入口

下列路径相对项目根目录 `D:\Multi-modal-Police-Intelligence-Analysis`。

| 文件 | 用途 |
| --- | --- |
| experiments/KEO/core/graph_rag/api.py | 对外GraphRAG类，格式1.2输入、格式1.3输出 |
| experiments/KEO/configs/config.py | GraphRAGSettings配置，读取项目根目录.env |
| experiments/KEO/models/graph_schema.py | 输入、检索及上下文数据模型 |
| experiments/KEO/core/graph_rag/grapy_builder.py | JSONL流式导入、名单筛选、报告；保留当前文件名拼写 |
| experiments/KEO/core/graph_rag/node_mapper.py | 人员及共享概念映射 |
| experiments/KEO/core/graph_rag/neo4j_store.py | 图谱持久化、增删改、同步任务、有限查询与一致性检查 |
| experiments/KEO/core/graph_rag/node_index.py | 节点向量同步、恢复、重建和种子检索 |
| experiments/KEO/core/graph_rag/graph_retriever.py | 组织种子检索与受限子图查询 |
| experiments/KEO/core/graph_rag/context_builder.py | 关系筛选、证据去重和上下文重建 |
| app/models/SiliconFlowEmbedding.py | 复用的Embedding封装，单条与批量请求显式使用float返回格式 |
| experiments/KEO/deploy/neo4j/compose.yaml | 独立实验Neo4j容器配置 |

调用方只需要依赖GraphRAG，不需要直接调用构图类、读取内部图文件或理解KEOFact物理存储结构。

## 3. 启动所需环境

### 3.1 必需组件

| 组件 | 当前部署/要求 |
| --- | --- |
| Python | 优先使用项目已运行成功的venv；现有运行记录使用Python 3.10，新环境须确认依赖兼容性 |
| Docker Desktop | Linux容器引擎已运行；仅用于本地Neo4j部署 |
| Neo4j Community | 独立实例，Compose镜像neo4j:5.26，业务数据库名neo4j |
| Chroma | Python本地PersistentClient，独立持久化目录和集合；不需另起Chroma容器 |
| Embedding服务 | 能访问配置指定的OpenAI兼容/v1服务，密钥有效，模型和维度一致 |
| 数据文件 | 三元组JSONL、共同划分JSON；在线查询必须能读取相同划分 |
| 网络 | Python进程能访问Neo4j和Embedding服务，代理及TLS配置正常 |

C自身不依赖本地GPU，也不要求配置回答大模型；回答模型由B配置。Embedding调用会产生服务请求，首次节点同步可能产生较多请求。

### 3.2 Python依赖

项目requirements.txt当前声明的相关版本：

| 包 | 声明版本 |
| --- | --- |
| neo4j | 6.2.0 |
| chromadb | 1.5.9 |
| openai | 2.49.0 |
| pydantic | 2.13.4 |
| pydantic-settings | 2.14.2 |
| python-dotenv | 1.2.2 |
| langchain-core | 1.5.1 |
| langchain-chroma | 1.1.0 |
| langchain-openai | 1.4.1 |

还需要NetworkX；当前requirements.txt未显式列出，应在联调环境安装兼容版本，验收后锁定实际版本。复用的SiliconFlowEmbedding文件还导入LangChain相关包，不能因为C使用原生Chroma就省略这些导入依赖。

已有项目环境优先复用，不要为了启动C全量升级依赖。首次新环境可按项目要求安装requirements.txt，但它还包含OCR、GPU等其他业务模块依赖，不是C的最小安装清单。

```powershell
cd D:\Multi-modal-Police-Intelligence-Analysis
.\venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable)"
# 仅在缺少NetworkX时安装，正式实验记录实际版本。
python -m pip install networkx
```

命令应使用该虚拟环境的Python，不要误用只有Conda base的另一解释器。以下所有python -m命令均从项目根目录执行。

## 4. 环境变量和数据路径

### 4.1 Python配置：项目根目录.env

当前配置统一使用KEO_前缀。示例为可修改的配置模板，不包含真实密钥：

```dotenv
KEO_NEO4J_URI=bolt://localhost:7688
KEO_NEO4J_USER=neo4j
KEO_NEO4J_PASSWORD=填写实验库密码
KEO_NEO4J_DATABASE=neo4j

KEO_EMBEDDING_API_KEY=填写Embedding服务密钥
KEO_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
KEO_EMBEDDING_MODEL=BAAI/bge-m3
KEO_EMBEDDING_DIMENSION=1024

KEO_CHROMA_COLLECTION=keo_graph_nodes
```

以上模型和维度对应前期成功的单文本测试；不能据此宣称所有批次、所有环境已验收。若使用其他服务或模型，按实际服务填写，且必须与建索引时的配置相同。BASE_URL填写/v1级地址，不追加/embeddings；请求路径由SDK拼接。

GraphRAGSettings在模块导入时即实例化，因此即使只使用dry-run，也必须提供必要的Neo4j和Embedding配置字段。dotenv写成KEY=VALUE，不放PowerShell的$env:赋值语句，不把真实凭据提交到仓库。

### 4.2 默认文件和规模配置

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| TRIPLES_PATH | experiments/KEO/output/test/三元组数据.jsonl | 导入读取的三元组文件 |
| SPLIT_PATH | experiments/KEO/output/test/划分名单.json | 只按其中build_ids建库 |
| CASES_PATH | experiments/KEO/output/test/案例数据.json | 当前导入器不直接读取原文数组，不创建KEOCase |
| CHROMA_PERSIST_DIR | experiments/KEO/output/test/node_vectors | 节点向量持久化目录 |
| CHROMA_COLLECTION | keo_graph_nodes | 集合名 |
| CHROMA_DISTANCE | cosine | 当前固定余弦距离 |
| IMPORT_BATCH_SIZE / DELETE_BATCH_SIZE | 300 / 300 | 单批写入/删除规模 |
| VECTOR_SYNC_BATCH_SIZE | 100 | 同步任务读取批量 |
| EMBEDDING_BATCH_SIZE | 16 | 每次Embedding请求文本数 |
| NODE_DESCRIPTION_VERSION | v1 | 描述模板版本，变化应显式重建 |
| NEO4J_POOL_SIZE | 5 | 连接池规模 |
| NEO4J_CONNECTION_TIMEOUT / NEO4J_QUERY_TIMEOUT | 10 / 15秒 | 连接等待、数据库事务/查询超时；不是公共调用总超时 |
| SEED_TOP_K / MAX_HOPS | 5 / 2 | 种子数、逻辑关系跳数 |
| MAX_NEIGHBORS_PER_NODE | 20 | 单节点展开上限 |
| MAX_NODES / MAX_EDGES | 100 / 150 | 返回子图节点/事实上限 |
| MAX_EVIDENCE_UNITS / MAX_CONTEXT_CHARS | 20 / 8000 | 原文单元数、上下文字符预算，后者不是token数 |

覆盖配置字段时，环境变量名加KEO_，例如KEO_TRIPLES_PATH。Windows路径推荐使用绝对路径；使用相对路径时保持当前目录为项目根目录。修改模型、维度、描述版本、向量目录或集合身份，不能直接混用原索引。

### 4.3 Compose配置：部署目录.env

`experiments/KEO/deploy/neo4j/.env`与根目录.env不同，只供容器部署使用：

```dotenv
KEO_NEO4J_PASSWORD=填写同一个实验库密码
```

两份.env不会自动同步。已有数据库后只修改Compose的密码变量不会自动修改数据库密码。

## 5. Neo4j部署和启动

当前实验实例HTTP端口7475、Bolt端口7688，与旧业务实例隔离，端口只绑定本机127.0.0.1。Community使用独立实例中的neo4j数据库，不在原业务实例中另建实验数据库。

首次部署时，在不存在部署.env的情况下复制模板并编辑密码：

```powershell
cd D:\Multi-modal-Police-Intelligence-Analysis\experiments\KEO\deploy\neo4j
Copy-Item .env.example .env
notepad .env
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml pull
docker compose --env-file .env -f compose.yaml up -d
docker compose --env-file .env -f compose.yaml ps
```

已有.env不要重复复制覆盖。config --quiet用于检查格式而不展开打印密码。

已部署后的日常启动，在项目根目录执行：

```powershell
docker compose --env-file experiments/KEO/deploy/neo4j/.env -f experiments/KEO/deploy/neo4j/compose.yaml up -d
```

Browser：`http://localhost:7475`；登录连接：`bolt://localhost:7688`；用户名：neo4j。keo-graph-rag是Compose项目名，不是登录用户。

Compose默认容器上限4GB内存、2 CPU，heap上限1GB、page cache 1GB，是小样本起始配置，不保证百万级运行性能。Docker引擎和宿主机须另留余量。数据保存在命名卷，停止Docker、重启容器会保留已提交数据；不要执行down -v或删除数据卷。卷持久化不代替备份。

其他组员电脑上的localhost指向其自己的机器。当前没有远程HTTP部署方案；跨机器接入要由C明确提供远程连接与网络安排，或在目标机器重新部署和构建索引。向量路径和集合UUID参与身份检查，不能假定拷贝目录后无需重新绑定就能查询。

## 6. 首次建库与日常查询的启动顺序

### 6.1 首次建库，或明确需要重新导入时

准备A的数据并确认根目录配置后：

```powershell
cd D:\Multi-modal-Police-Intelligence-Analysis
.\venv\Scripts\Activate.ps1

# 只检查输入，不写Neo4j。
python -m experiments.KEO.core.graph_rag.grapy_builder --dry-run

# 初始化结构并分批导入，结束时标记导入完成。
python -m experiments.KEO.core.graph_rag.grapy_builder

# 同步节点向量，完成核验后开放检索。
python -m experiments.KEO.core.graph_rag.node_index
```

grapy_builder会初始化约束和控制记录，不强制要求另跑初始化入口。也可用 `python -m experiments.KEO.core.graph_rag.neo4j_store` 单独检查初始化。

Browser核查：

```cypher
MATCH (s:KEOKnowledgeBase {name:'main'})
RETURN s.status, s.phase, s.import_complete;
```

导入完成、待同步时为updating/idle/true；全部同步完成后为ready/idle/true。三元组入库不代表向量已经准备好。API只在ready且配置一致时提供检索。

当前test样本为500个建库案例、6095条三元组，eval_ids为空，仅用于联调，不是正式独立评估数据。

### 6.2 知识库已经ready后的日常调用

只需启动Docker与实验Neo4j、激活venv，再调用GraphRAG或运行API命令：

```powershell
python -m experiments.KEO.core.graph_rag.api --question-id q001 --question-type case_judgment --query "对方称完成任务可以返款，要求下载应用并不断充值。请判断诈骗类型、指出依据并提出应对措施。"
```

不要每次提问都重导JSONL或重新计算全量向量。日常查询会为问题计算Embedding，但不会重新建图。CLI不会自动逐题读取questions.jsonl，批量流程由调用方实现。

## 7. A、D向C提供的输入

### 7.1 A的数据包：格式1.1

| 文件 | 内容和约定 |
| --- | --- |
| 案例数据.json | id、案情描述、案件类别；供B文本库、D和来源核查，C当前入口不直接导入该数组 |
| 三元组数据.jsonl | triple_id、source_id、source_type、fraud_category、head、relation、tail、factuality、evidence，以及可选confidence/status |
| 划分名单.json | build_ids与eval_ids，不重复、不交叉；B/C使用同一建库范围 |

实体字段为id/name/type/role，证据单元为unit_id/text。当前C要求原实体ID和triple_id全局唯一并保持稳定；这比总文档中较宽泛的组合唯一约定更严格，交接时须确认。证据文本须非空，同一(source_id, unit_id)不能出现冲突文本。

人员不因同名合并，共享概念沿用A清洗名称及当前NodeMapper规则。只将build_ids入库，eval_ids不进入图谱/向量/证据。A需说明数据版本、失败或零三元组来源、重复案例处理及规范名称规则。改变名单不等于自动删除已有来源，应显式清理并同步。

### 7.2 D的问题：格式1.2

```json
{
  "question_id": "q001",
  "question_type": "case_judgment",
  "question": "对方要求下载应用充值，声称完成任务即可返款。请判断诈骗类型、指出依据并提出应对措施。"
}
```

| 字段 | 类型 | 约定 |
| --- | --- | --- |
| question_id | str | 唯一非空ID，不含首尾空白，C原样回显 |
| question_type | str | case_judgment或global_analysis |
| question | str | 非空完整问题；案例题包含案情正文与任务 |

JSONL每行一个完整对象。三种方法使用相同问题，不传gold_category、参考措施、出题source_id或统计摘要。D内部question_metadata.jsonl（格式1.5）只供出题和评价，不附加到问题中。输入模型虽忽略额外字段，调用方仍应遵守上述隔离约定。

global_analysis采用同一有限检索流程，不代表C会进行完整全库统计；题目不要将有限结果冒充完整数量、占比或排名。正式评价题应由D依据eval_ids提供，当前questionbank只作建库样本联调。

## 8. C对外暴露的Python接口

### 8.1 入口、参数和生命周期

```python
from experiments.KEO.core.graph_rag.api import GraphRAG

GraphRAG(graph_path, vector_dir, split_path, collection_name="keo_graph_nodes")
await graph_rag.retrieve(question_record)  # 返回普通dict
await graph_rag.close()
```

| 初始化参数 | 类型 | 必填 | 当前实际含义 |
| --- | --- | --- | --- |
| graph_path | str | 是 | 兼容旧约定；当前不读取、不要求文件存在 |
| vector_dir | str | 是 | 已完成同步的Chroma目录 |
| split_path | str | 是 | 与当前图谱建库一致的划分名单 |
| collection_name | str | 否 | 默认keo_graph_nodes；必须与已同步集合一致 |

Neo4j和Embedding由配置加载，不通过每道题传密钥。实例延迟连接，在同一事件循环中可复用多题；内部锁按实例串行执行。结束使用async with或finally调用close释放资源，不删除持久化数据。关闭后请创建新实例。

### 8.2 单题调用示例

```python
import asyncio
import json
from experiments.KEO.core.graph_rag.api import GraphRAG

async def main():
    async with GraphRAG(
        graph_path="experiments/KEO/output/test/graph.json",
        vector_dir="experiments/KEO/output/test/node_vectors",
        split_path="experiments/KEO/output/test/划分名单.json",
        collection_name="keo_graph_nodes",
    ) as graph_rag:
        question = {
            "question_id": "q001",
            "question_type": "case_judgment",
            "question": "对方称账户异常，要求共享屏幕并转账。请判断诈骗类型、说明依据并提出措施。",
        }
        result = await graph_rag.retrieve(question)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # B将question与result交给其公共回答模块。

if __name__ == "__main__":
    asyncio.run(main())
```

B若已经处于async服务中，直接await，不在运行中的事件循环内再调用asyncio.run。B公共生成模块的函数名由B提供，这里不虚构一个已经实现的生成接口。

### 8.3 格式1.3返回值

以下示例仅表示结构，不是一次实际检索结果：

```json
{
  "question_id": "q001",
  "method": "graph_rag",
  "status": "ok",
  "context": "[E001] 来源案例：case_000001……原文证据：对方要求下载应用充值。",
  "evidence": [{
    "evidence_id": "E001",
    "source_id": "case_000001",
    "text": "对方要求下载应用充值。",
    "triple_ids": ["case_000001_T001"]
  }],
  "elapsed_ms": 120.5,
  "error": null
}
```

| 返回字段 | 类型 | 说明 |
| --- | --- | --- |
| question_id | str | 原问题ID |
| method | str | 固定graph_rag |
| status | str | ok、empty、error |
| context | str | 实际交给回答模型的上下文，带证据引用、来源与事实状态 |
| evidence | list | 实际采用的原文证据，不是模型生成的解释 |
| elapsed_ms | number | 整个retrieve耗时，单位毫秒；含首次初始化及等待，不含离线建库和最终答案生成 |
| error | dict或null | 错误类型与可读说明，正常和empty时为null |

证据字段：evidence_id为本次结果内的E001等编号；source_id必须属于build_ids；text保持原文；triple_ids为对应原事实ID列表。同一原文单元可支持多个事实，按案例与unit_id去重。

| 情况 | status | context/evidence | error |
| --- | --- | --- | --- |
| 有可用上下文与证据 | ok | 非空且引用一致 | null |
| 正常执行但无可用证据上下文 | empty | 空字符串/空列表 | null |
| 输入缺失、题型错误或正文空白 | error | 空字符串/空列表 | type=INVALID_INPUT |
| 未ready、索引不一致、网络/存储异常或版本变化 | error | 空字符串/空列表 | type=RETRIEVAL_ERROR |

error的message不会返回原始凭据或部分成功上下文。无有效question_id时，错误结果可能回显空字符串，批量调用方应写入独立输入错误报告，不能补造正式题号。构造对象、配置模块导入等阶段的异常不属于retrieve内部错误返回范围；取消任务会继续向上传播。

当前没有最低相似度阈值，无关文本也可能产生近邻；不能用“随机问一句无关话”断言应该empty，也不能把empty解释为没有诈骗风险。

## 9. B公共回答模块和D评价如何接入

1. D提供相同格式1.2题目给三种方法。
2. B调用C的retrieve获取格式1.3；C不独立生成最终答案。
3. B将原问题及C的context和证据引用交给共同回答模型。
4. B写出格式1.4的answers.jsonl，再交D评价。

格式1.4中的retrieval取C结果的status、context、evidence、elapsed_ms、error五项；run_id、model_id、answer_text、predicted_category、cited_evidence_ids、parse_status、generation_elapsed_ms由B维护。D按run_id + question_id + method + model_id关联回答，不按文件行号匹配。

检索error不能当empty或悄悄改走文本RAG；若阻止生成，B按总文档保留失败回答记录和retrieval.error。直接回答不经过C，其retrieval.status使用not_applicable，而不是C的empty。

B需确认实际公共生成模块路径/签名、统一模型和提示、上下文预算、引用规则、超时和失败保存方式；C只承诺上述检索接口。D维护gold_category等内部资料，不进入生成输入。当前上下文未主动附加历史fraud_category标签；是否提供类别提示必须由B/C共同统一约定。

批量使用时，调用方逐行读取questions.jsonl、检查重复ID，复用实例逐次await，按question_id保存结果；可另存graph_retrieval_results.jsonl。当前没有接受--questions参数的批量CLI，不能把单题入口当成已实现的批量程序。

## 10. 更新、恢复与一致性

Neo4jStore支持显式事实/实体/证据更新、按事实和来源删除，但这些属于C内部维护API，不要求B直接调用。普通write_batch仅做新增与同内容重跑，冲突不隐式覆盖。更新后知识库进入updating，再完成节点同步才能ready。

同步失败后，先处理网络或服务错误，确认旧进程退出，再恢复：

```powershell
python -m experiments.KEO.core.graph_rag.node_index --resume
```

--resume是向量恢复参数，不是grapy_builder现有的导入恢复CLI参数。导入中断需按存储层维护所有者流程恢复，不能手动SET ready或import_complete绕过检查。

模型/维度/模板或索引目标变化，使用新的空集合，并显式重建：

```powershell
# 先在配置中设置新的KEO_CHROMA_COLLECTION。
python -m experiments.KEO.core.graph_rag.node_index --rebuild
```

重建中断可在目标相同、旧进程已退出时使用--rebuild --resume。不要修改旧集合指纹伪装兼容，也不要同步未完成时开放检索。查询跨种子、子图、证据和最终API输出检查同一知识库版本；单写入维护期间不应同时运行导入、同步或另一个重建器。

## 11. 联调检查与常见问题

| 检查项 | 期望 |
| --- | --- |
| 配置和依赖 | 正确venv，必填变量齐全，NetworkX和Chroma等可导入 |
| 服务 | 实验Neo4j可连接，Embedding可调用，集合已同步 |
| 状态 | ready / idle / import_complete=true，待同步任务为0 |
| 数据 | 仅build_ids入库，测试题元数据不进入模型 |
| 返回结构 | 格式1.3字段齐全，引用与证据一致，预算不超限 |
| 错误行为 | 空与失败分开，集合缺失不自动创建，版本变化拒绝输出 |
| 生成对接 | B使用实际context/evidence生成并保存格式1.4 |

前期出现过代理TLS EOF和Embedding参数400。EOF时检查HTTP_PROXY/HTTPS_PROXY/ALL_PROXY与代理软件；单文本float请求成功并不代表所有批量一定成功，400应检查模型、输入、批量及服务参数。现有Embedding封装已显式发送encoding_format=float；不要通过关闭TLS证书验证解决连接问题。

公共API为避免泄露信息只记录异常类型，定位时可用node_index、graph_retriever、context_builder各层CLI查看具体异常，不要把含密钥的配置或日志交给其他组员。

本文整理时没有重新运行建库、Embedding、检索或端到端验证。接口已存在不代表全量性能、正式评价与所有恢复场景已验收；正式实验应保留环境版本、数据版本、索引配置及实际联调记录。
