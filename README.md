# **多模态警务智能研判系统**

**MPIA (Multi-modal Police Intelligence Analysis)**

# 项目概括

+ 前端框架: Vue
+ 后端框架: FastAPI
+ 主要语言: Python
+ LLM SDK: OpenAI / LangChain（硅基流动 Qwen API）
+ OCR 引擎: PaddleX
+ AI 换脸检测: 百度云 Face API
+ 图数据库: Neo4j（知识图谱存储）

# 模块组成

| 模块 | 功能 | 数据格式 | 主要代码 |
| ---- | ---- | ---- | ---- |
| 模块一 多模态 | 文本/图片/音频/视频 → 识别文本 + AI 换脸检测 | 1.1 → 1.2 | `app/core/multimodal/`、`app/api/multimodal_api.py` |
| 模块二 知识抽取 | 多模态文本 → LLM 抽取三元组 | 1.2 → 1.3 | `app/core/knowledge/extraction_service.py`、`app/api/knowledge.py` |
| 模块三 知识存储 | 三元组 → Neo4j 图数据库 + 结构化案件信息 | 1.3 → 1.4 | `app/core/knowledge/storage_service.py`、`app/api/knowledge.py` |

# 快速启动

## 方式一：一键脚本（推荐）

```
bash run.sh
```

自动完成：激活虚拟环境 → 启动 Neo4j（Docker 容器，若已运行则复用）→ 启动 FastAPI（热重载）。

## 方式二：手动启动

1. 启动 Neo4j（模块三依赖，端口 7687/7474）：

```bash
docker run -d --name mpia-neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/mpia2025 neo4j:5
```

2. 启动 FastAPI（主文件 `app/main.py`，实例名 `app = FastAPI()`，`--reload` 为热重载）：

```
uvicorn app.main:app --reload
```

3. 配置环境变量：复制 `.env.example` 为 `.env`，填入硅基流动密钥（`SILICONFLOW_API_KEY`，所有 LLM 调用统一使用）与百度云密钥（`BAIDU_API_KEY`/`BAIDU_SECRET_KEY`，AI 换脸检测）。

# 开发调试

==访问端口+/docs可以快速测试接口==	例如:

> http://127.0.0.1:8000/docs

# API 端点

| 方法 | 路径 | 输入 | 输出 | 说明 |
| ---- | ---- | ---- | ---- | ---- |
| POST | `/multimodal/analyze` | 格式 1.1 | 格式 1.2 | 模块一：多模态识别（文本/图片 OCR/音频转写/视频换脸检测） |
| POST | `/api/v1/knowledge/extract` | 格式 1.2 | 格式 1.3 | 模块二：知识抽取（LLM 三元组） |
| POST | `/api/v1/knowledge/store` | 格式 1.3 | 格式 1.4 | 模块三：写入 Neo4j 图数据库 |
| POST | `/api/v1/knowledge/pipeline` | 格式 1.2 | 格式 1.4 | 端到端：抽取 + 存储一步完成 |
| GET | `/api/v1/knowledge/health` | — | 健康状态 | 服务存活 + Neo4j 连通性 |

# 项目版本管理

| 组件 | 版本 | 说明 |
| ---- | ---- | ---- |
| Python | 3.10.11 | 运行环境 |
| FastAPI | 0.140.0 | Web 框架 |
| Pydantic | 2.13.4 | 数据校验与配置 |
| OpenAI | 2.49.0 | LLM 推理调用 |
| LangChain (core / openai) | 1.5.1 / 1.4.1 | 知识抽取 LLM 编排 |
| Neo4j | 6.2.0 | 图数据库驱动（模块三存储） |
| PaddleX | 3.7.2 | OCR 图片文字识别 |
| PaddlePaddle | 3.3.0 (GPU) | 深度学习框架（无 GPU 环境改用 CPU 版） |
| Uvicorn | 0.51.0 | ASGI 服务器 |

# 项目结构

```text
.
├── app/                           # 应用核心目录
│   ├── main.py                    # FastAPI 入口文件
│   ├── api/                       # 接口路由层
│   │   ├── multimodal_api.py      #   多模态模块接口 (/multimodal/analyze)
│   │   └── knowledge.py           #   知识抽取与图谱接口 (/api/v1/knowledge/*)
│   ├── core/                      # 核心业务逻辑
│   │   ├── multimodal/            #   模块一：用户输入 → 多模态输出
│   │   │   ├── service.py         #     核心服务：批量任务编排与调度
│   │   │   ├── ocr_engine.py      #     OCR 引擎：图片文字识别 (PaddleX)
│   │   │   ├── deepfake_engine.py #     AI 换脸识别引擎 (百度云 API)
│   │   │   ├── tools.py           #     工具函数：文件后缀提取等
│   │   │   └── base64.py          #     Base64 编解码工具
│   │   └── knowledge/             #   模块二/三：知识抽取与图谱存储
│   │       ├── extraction_service.py #   知识抽取（LLM 三元组）
│   │       └── storage_service.py    #   知识存储（Neo4j 图数据库）
│   ├── config/                    # 配置管理
│   │   ├── setting.py             #   模块一环境变量与全局配置
│   │   └── settings.py            #   模块二/三配置（LLM/Neo4j/ChromaDB）
│   └── models/                    # Pydantic 数据模型
│       ├── multimodal_schema.py   #   多模态输入/输出数据模型（格式 1.1/1.2）
│       └── knowledge_schema.py    #   知识抽取/存储数据模型（格式 1.3/1.4）
├── data/                          # 测试案件数据 (按日期组织)
│   └── <YYYY-MM-DD>/              #   日期目录
│       └── <案件名>/              #     案件目录 (图片/视频/音频/文本)
├── docs/                          # 设计文档
│   └── 接口设计文档/              #   接口设计文档目录
│       ├── 用户输入到多模态输出接口设计.md
│       ├── 用户输入到多模态输出接口设计 v2.0.md
│       ├── 知识抽取接口设计.md
│       └── 知识图谱存储接口设计.md
├── .env                           # 环境变量配置文件
├── .env.example                   # 环境变量配置示例文件
├── .gitignore                     # Git 忽略文件
├── README.md                      # 项目说明文件
├── requirements.txt               # 项目依赖清单
└── test_main.http                 # 快速测试文件 (HTTP 请求)
```
