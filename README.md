# 多模态警务智能研判系统

**MPIA（Multi-modal Police Intelligence Analysis）**

面向警务反诈场景的多模态智能研判系统：接收文本 / 图片 / 音频 / 视频等异构输入，自动完成**识别 → 抽取 → 建图 → 研判 → 预警**的端到端分析，输出诈骗研判结果与分级预警。

## 功能简介

系统按流水线串联四个核心模块：

```
 输入 (text / image / audio / video)
        │
        ▼
┌──────────────┐  格式1.2  ┌──────────────┐  格式1.3  ┌──────────────┐  格式1.4  ┌──────────────┐
│ ① 多模态识别  │ ────────► │ ② 知识抽取    │ ────────► │ ③ 知识图谱存储 │ ────────► │ ④ 智能研判+预警│
│ OCR / ASR /   │           │ LLM 三元组抽取 │           │ Neo4j 落图    │           │ RAG + LLM 研判│
│ AI 换脸检测   │           │              │           │              │           │              │
└──────────────┘           └──────────────┘           └──────────────┘           └──────────────┘
```

| 模块 | 能力 | 关键实现 |
| --- | --- | --- |
| **① 多模态识别** | 文本直通；图片 OCR；音频/视频语音转写；视频 AI 换脸检测 | PaddleX OCR、SiliconFlow ASR、百度云 Face API |
| **② 知识抽取** | 从文本抽取「主体—关系—客体」三元组 | LLM（Qwen）结构化抽取 |
| **③ 知识图谱存储** | 三元组落库，推断受害者 / 嫌疑人 / 资金流水 | Neo4j 图数据库 |
| **④ 智能研判** | 检索相似案例，研判是否涉诈并生成分级预警 | ChromaDB 向量检索 + LLM 研判 |

对外仅暴露一条**端到端流水线接口** `POST /api/v1/pipeline`，同时提供历史案件查询接口；各分模块接口默认注释，可按需在 `app/main.py` 中启用。

## 技术栈

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.10.x | 运行环境 |
| FastAPI | 0.140.0 | Web 框架 |
| PaddleX / PaddlePaddle | 3.7.2 / 3.3.0 | 图片 OCR |
| OpenAI SDK / LangChain | 2.49.0 / 1.3.14 | LLM 调用与编排 |
| Neo4j | 5.x（Docker）/ driver 6.2.0 | 图数据库 |
| ChromaDB | 1.5.9 | 向量数据库（RAG） |
| 硅基流动 SiliconFlow | — | LLM / Embedding 服务（OpenAI 兼容协议） |
| 百度云 Face API | — | AI 换脸检测 |

## 目录结构

```text
.
├── app/
│   ├── main.py                    # 入口：端到端流水线 + 历史记录接口
│   ├── api/                       # 路由层
│   │   ├── multimodal_api.py      #   模块一：/multimodal/analyze
│   │   ├── knowledge.py           #   模块二/三：/api/v1/knowledge/*
│   │   └── intelligentjudge.py    #   模块四：/api/v1/judge*
│   ├── core/                      # 业务逻辑层
│   │   ├── multimodal/            #   ① 多模态识别（OCR/ASR/换脸）
│   │   ├── knowledge/             #   ② 知识抽取、③ 知识图谱存储
│   │   ├── judgment/              #   ④ 智能研判（RAG + LLM）
│   │   └── alertoutput/           #   ⑤ 预警输出
│   ├── config/                    # 配置管理（.env 读取）
│   └── models/                    # Pydantic 数据模型
├── data/                          # 案件文件落盘（按日期/案件组织）
├── docs/                          # 接口与数据结构设计文档
├── output/                        # ChromaDB 向量库持久化目录
├── text/                          # 测试数据与脚本
├── tools/                         # 调试与测试脚本
├── system_prompt.txt              # 研判 LLM 系统提示词（必需，勿删）
├── .env.example                   # 环境变量示例
├── requirements.txt               # 依赖清单
├── run.sh                         # Linux/macOS 一键启动脚本
└── test_main.http                 # HTTP 快速测试
```

---

## 快速启动

### 0. 前置要求

| 依赖 | 说明 |
| --- | --- |
| Python 3.10 | 推荐 3.10.11 |
| Docker Desktop | 用于运行 Neo4j（Windows/macOS 直接装 Desktop，Linux 装 docker-engine） |
| 硅基流动 API Key | 在 [siliconflow.cn](https://siliconflow.cn) 注册获取（LLM 与 Embedding 共用） |
| （可选）百度云 Face API Key | 仅视频 AI 换脸检测需要 |

### 1. 获取代码并安装依赖

```bash
# 进入项目目录
cd multimodal-police-analysis

# 创建并激活虚拟环境
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

# 安装基础依赖
pip install -r requirements.txt
```

> **PaddlePaddle 安装说明**（`requirements.txt` 默认使用 GPU 版）：
> - 有 NVIDIA GPU 且 CUDA 12.x：`pip install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/`
> - 无 GPU 或仅做 CPU 推理：`pip install paddlepaddle==3.3.0`（替换掉 `paddlepaddle-gpu`）

**补装视频处理依赖**（`requirements.txt` 未包含，视频输入必需）：

```bash
pip install moviepy
```

`moviepy` 提取音轨依赖 `ffmpeg`，请确保系统已安装并加入 PATH（`ffmpeg -version` 可验证）。

### 2. 配置环境变量

```bash
# 复制示例配置
cp .env.example .env        # Windows 用 copy .env.example .env
```

编辑 `.env`，至少保证以下配置正确（`#` 为必填）：

```ini
# ===== 通用 LLM（硅基流动，OpenAI 兼容）=====
SILICONFLOW_API_KEY=sk-xxxx                     # 你的硅基流动 API Key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
BASE_URL=https://api.siliconflow.cn/v1
TIMEOUT=120

# ===== 模块一：多模态（音频转写）=====
AUDIO_MODEL=FunAudioLLM/SenseVoiceSmall
# 视频 AI 换脸检测（可选，不填则跳过换脸检测）
BAIDU_API_KEY=
BAIDU_SECRET_KEY=

# ===== 模块二/三：知识抽取 + Neo4j =====
EXTRACTION_MODEL=Qwen/Qwen2.5-32B-Instruct
EXTRACTION_TEMPERATURE=0
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=12345678                        # 必须与第 3 步 Docker 密码一致

# ===== 模块四：智能研判（RAG + LLM）=====
JUDGMENT_API_KEY=sk-xxxx                       # 同硅基流动 API Key
JUDGMENT_BASE_URL=https://api.siliconflow.cn/v1
LLMMODEL=Qwen/Qwen2.5-32B-Instruct
RAGENGING_MODEL=BAAI/bge-m3
EXAMPLE_JSON_PATH=./诈骗案例数据集_重分类.json   # 外挂知识库 JSON
JSON_PROCESSED=./output
RAG_COLLECTION=my_knowledge_base
RAG_TOP_K=2
LLM_MAX_TOKENS=1000
```

> 注意：`.env.example` 中的 `EXTRACTION_MODEL`、`EXTRACTION_TEMPERATURE` 等字段为占位文本，必须替换为上方真实值，否则启动时 `float()` 解析会报错。

### 3. 启动 Neo4j（Docker）

```bash
# 拉取镜像（国内可用 DaoCloud 镜像源，二选一）
docker pull neo4j:5
# 国内镜像源：
# docker pull m.daocloud.io/docker.io/library/neo4j:5

# 启动容器（密码必须与 .env 的 NEO4J_PASSWORD 一致）
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/12345678 \
  neo4j:5
```

验证：浏览器访问 `http://localhost:7474`，用 `neo4j / 12345678` 登录；或访问 `GET /api/v1/knowledge/health`（需先启用该路由）。

### 4. 启动服务

```bash
# Windows (PowerShell) 与 Linux/macOS 通用
uvicorn app.main:app --reload --port 8000
```

Linux/macOS 也可使用一键脚本（自动起 Neo4j + 服务）：

```bash
./run.sh start      # 启动；./run.sh stop 停止 Neo4j
```

### 5. 验证

浏览器打开 **http://127.0.0.1:8000/docs**（Swagger 交互式文档），执行一条端到端测试：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "TEST-001",
    "inputs": [
      {"type": "text", "content": "对方自称是公安局的，说我涉嫌洗钱，让我转账到安全账户"}
    ]
  }'
```

预期返回包含 `judgment`（研判结果）、`alerts`（预警列表）等字段的 JSON。

---

## 常见问题（错误排查）

| 报错 / 现象 | 原因 | 解决 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'moviepy'` | 视频处理依赖未安装 | `pip install moviepy`，并确保系统已装 `ffmpeg` |
| `ImportError` 或 `libcudart.so` 找不到 / PaddlePaddle 加载失败 | GPU 版 paddle 与本地 CUDA 不匹配 | 改用 CPU 版：`pip install paddlepaddle==3.3.0` |
| OCR 推理失败（Paddle 3.3.0 CPU 后端 PIR/oneDNN bug） | oneDNN 指令转换异常 | 设环境变量 `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=False` |
| 首次运行卡在下载 OCR 模型 / 下载失败 | PaddleX 默认模型源或缓存目录不可达 | 设 `PADDLE_PDX_MODEL_SOURCE=bos`、`PADDLE_PDX_CACHE_HOME=<项目内可写目录>` |
| `Neo4j ServiceUnavailable` / `couldn't connect`（接口返回 502） | Docker 未启动，或容器未运行 | 确认 `docker ps` 有 neo4j 容器；`docker start neo4j` |
| Neo4j 认证失败 `AuthError` | `.env` 的 `NEO4J_PASSWORD` 与容器密码不一致 | 统一密码后重启容器，或 `docker rm -f neo4j` 重建 |
| `提示词文件不存在` / `system_prompt.txt` | 根目录缺失该文件 | 恢复 `system_prompt.txt`（研判 LLM 必需） |
| `.env` 加载失败 / pydantic 校验报错（如 `EXTRACTION_TEMPERATURE`） | `.env` 未创建或含占位文本 | `cp .env.example .env` 并填入真实值 |
| 端口 8000 / 7687 / 7474 被占用 | 端口冲突 | `--port` 换端口；`docker run -p 新端口:7687` 并同步改 `.env` |
| 图片/视频识别返回「识别失败」 | OCR 未就绪或文件格式不支持 | 查看日志；确认文件为常见格式（jpg/png/mp4/mp3 等） |

---

## API 接口

> 分模块路由默认在 `app/main.py` 中**注释**，仅流水线与历史记录接口默认启用。如需分模块调试，取消 `app/main.py` 中对应 import 与 `include_router` 注释即可。

| 方法 | 路径 | 说明 | 默认启用 |
| --- | --- | --- | --- |
| POST | `/api/v1/pipeline` | 端到端流水线（多模态 → 抽取 → 存储 → 研判 → 预警） | ✅ |
| GET | `/api/v1/history` | 历史案件列表 | ✅ |
| GET | `/api/v1/history/{case_id}` | 历史案件详情 | ✅ |
| POST | `/multimodal/analyze` | 模块一：多模态识别 | 注释 |
| POST | `/api/v1/knowledge/extract` | 模块二：知识抽取 | 注释 |
| POST | `/api/v1/knowledge/store` | 模块三：知识存储 | 注释 |
| POST | `/api/v1/knowledge/pipeline` | 抽取 + 存储一步到位 | 注释 |
| GET | `/api/v1/knowledge/health` | Neo4j 连通性检查 | 注释 |
| POST | `/api/v1/judge` | 模块四：单案例研判 | 注释 |
| POST | `/api/v1/judge/batch` | 批量研判 | 注释 |
| GET | `/api/v1/judge/health` | 研判服务健康检查 | 注释 |

## 数据流与数据格式

模块间通过四种数据结构串联（定义见 `app/models/`，详见 `docs/` 接口文档）：

| 格式 | 含义 | 字段要点 |
| --- | --- | --- |
| 1.1 | 用户输入（前端 → 模块一） | `case_id` + `inputs[]`（`type` / `content`） |
| 1.2 | 多模态输出（模块一 → 模块二） | `outputs[]`（`text` / `status` / `deepfake_result`） |
| 1.3 | 知识抽取输出（模块二 → 模块三） | `triplets[]`（`subject` / `relation` / `object`） |
| 1.4 | 知识存储输出（模块三 → 模块四） | `victim` / `suspect` / `relations` / `transactions` |

## 设计文档

- `docs/接口设计文档/`：各模块接口设计（Markdown）
- `docs/智能研判数据结构说明.md`：研判数据结构说明

## 测试

- 接口快速测试：`test_main.http`（配合 VS Code REST Client 或 IDEA）
- 脚本测试：`tools/` 目录内含 `smoke_extraction.py`、`test_multimodal.py`、`stress_test.py` 等调试/压测脚本
- 端到端样例：`text/test_e2e_pipeline.py`
