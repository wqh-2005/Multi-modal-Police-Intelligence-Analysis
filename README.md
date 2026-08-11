# **多模态警务智能研判系统**

**MPIA (Multi-modal Police Intelligence Analysis)**

# 项目概括

+ 前端框架: Vue
+ 后端框架: FastAPI
+ 主要语言: Python
+ LLM SDK: OpenAI
+ OCR 引擎: PaddleX
+ AI 换脸检测: 百度云 Face API

# 快速启动

主文件路径: app/main.py，且代码中创建的实例名为 app = FastAPI()：

在终端执行：(**--reload**: **热重载模式**)

```
uvicorn app.main:app --reload
```

# 开发调试

==访问端口+/docs可以快速测试接口==	例如:

> http://127.0.0.1:8000/docs

# 项目版本管理

| 组件 | 版本 | 说明 |
| ---- | ---- | ---- |
| Python | 3.10.11 | 运行环境 |
| FastAPI | 0.140.0 | Web 框架 |
| Pydantic | 2.13.4 | 数据校验与配置 |
| OpenAI | 2.49.0 | LLM 推理调用 |
| PaddleX | 3.7.2 | OCR 图片文字识别 |
| PaddlePaddle | 3.3.0 (GPU) | 深度学习框架 |
| Uvicorn | 0.51.0 | ASGI 服务器 |

# 项目结构

```text
.
├── app/                           # 应用核心目录
│   ├── main.py                    # FastAPI 入口文件
│   ├── api/                       # 接口路由层
│   │   └── multimodal_api.py      #   多模态模块接口 (/multimodal/analyze)
│   ├── core/                      # 核心业务逻辑
│   │   └── multimodal/            #   用户输入 → 多模态输出模块
│   │       ├── service.py         #     核心服务：批量任务编排与调度
│   │       ├── ocr_engine.py      #     OCR 引擎：图片文字识别 (PaddleX)
│   │       ├── deepfake_engine.py #     AI 换脸识别引擎 (百度云 API)
│   │       ├── tools.py           #     工具函数：文件后缀提取等
│   │       └── base64.py          #     Base64 编解码工具
│   ├── config/                    # 配置管理
│   │   └── setting.py             #   环境变量与全局配置
│   └── models/                    # Pydantic 数据模型
│       └── multimodal_schema.py   #   多模态输入/输出数据模型
├── data/                          # 测试案件数据 (按日期组织)
│   └── <YYYY-MM-DD>/              #   日期目录
│       └── <案件名>/              #     案件目录 (图片/视频/音频/文本)
├── docs/                          # 设计文档
│   ├── 数据结构文档.pdf
│   └── 接口设计文档/              #   接口设计文档目录
│       ├── 智能研判系统-接口设计.pdf
│       ├── 用户输入到多模态输出接口设计.md
│       └── 用户输入到多模态输出接口设计 v2.0.md
├── Knowledges_base/               # 本地知识库
│   ├── VectorStore/               #   向量数据库文件
│   └── raw_data/                  #   原始数据文档
├── .env                           # 环境变量配置文件
├── .env.example                   # 环境变量配置示例文件
├── .gitignore                     # Git 忽略文件
├── README.md                      # 项目说明文件
├── requirements.txt               # 项目依赖清单
└── test_main.http                 # 快速测试文件 (HTTP 请求)
```



# 系统测试指南

## 一、环境准备

### 1.1 依赖安装

```powershell
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
.\venv\Scripts\activate

```

> **注意**：`paddlepaddle-gpu` 需根据你的 CUDA 版本选择，详见 [PaddlePaddle 安装文档](https://www.paddlepaddle.org.cn/install/quick)。
> - 有 GPU（CUDA 12.x）：`pip install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/`
> - 无 GPU：`pip install paddlepaddle==3.3.0`

### 1.2 配置 .env 文件

复制 `.env.example` 为 `.env`，填写以下必要配置：


### 1.3 启动 Neo4j 数据库

```powershell
# 拉取镜像（仅首次）
docker pull m.daocloud.io/docker.io/library/neo4j:5.26.29

# 启动容器
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 ^
  -e NEO4J_AUTH=neo4j/password ^
  m.daocloud.io/docker.io/library/neo4j:5.26.29
```

### 1.4 启动 FastAPI 服务

```powershell
uvicorn app.main:app --reload --port 8000
```

访问 **http://127.0.0.1:8000/docs** 查看 Swagger 交互式文档。

---

## 二、分模块测试（逐个验证）

> 分模块测试时，需要取消 `app/main.py` 中各个模块路由的注释。

### 第一步：修改 main.py

将 `app/main.py` 中以下内容**取消注释**：

```python
# 文件顶部 import 区
from app.api.multimodal_api import router as multimodal_router   # 取消注释
from app.api.knowledge import router as knowledge_router         # 取消注释
from app.api.intelligentjudge import router as judge_router      # 取消注释

# 中间 app 创建区（恢复原写法）
app = FastAPI()

# 注册各模块路由
app.include_router(multimodal_router)     # 取消注释
app.include_router(knowledge_router)       # 取消注释
app.include_router(judge_router)           # 取消注释
```

**同时注释掉**流水线相关的代码（`app = FastAPI(title=...)` 到文件末尾的 `PipelineResponse` 和 `@app.post("/api/v1/pipeline")` 部分）。

### 第二步：模块一 — 多模态识别

**接口**：`POST /multimodal/analyze`

**测试样例**（纯文本）：

```json
{
  "case_id": "TEST-MULTI-001",
  "inputs": [
    {
      "type": "text",
      "content": "对方自称是公安局的，说我涉嫌洗钱，让我转账到安全账户"
    }
  ]
}
```

**预期结果**：返回 `outputs` 数组，其中 `status` 为 `done`，`text` 为输入原文。

**测试样例**（图片，需自行转 base64）：

```json
{
  "case_id": "TEST-MULTI-002",
  "inputs": [
    {
      "type": "image",
      "content": "data:image/jpeg;base64,/9j/4AAQ..."
    }
  ]
}
```

**预期结果**：OCR 识别出图片中的文字，返回 `confidence` 置信度。

---

### 第三步：模块二/三 — 知识抽取与图谱

先确认 Neo4j 连通：

**接口**：`GET /api/v1/knowledge/health`

**预期结果**：`{ "status": "ok", "neo4j_connected": true }`

#### 3.1 知识抽取

**接口**：`POST /api/v1/knowledge/extract`

**测试样例**：

```json
{
  "case_id": "TEST-EXT-001",
  "outputs": [
    {
      "text": "A：您好，这里是XX市公安局刑侦支队，请问是张先生吗？\nB：是的，什么事？\nA：我们查到你的身份证被冒用开设了一个银行账户，涉嫌洗钱，涉案金额高达200万元。\nB：不可能！我从来没有做过这种事！\nA：请配合调查，否则我们将冻结你名下所有账户，并下发逮捕令。\nB：那我要怎么证明清白？\nA：请下载我们的安全核查APP，将你所有存款转到指定安全账户进行资金核查。\nB：好吧，那我试试...",
      "type": "text",
      "status": "done"
    }
  ]
}
```

**预期结果**：返回 `triplets` 三元组列表，如 `[{ "subject": "A", "relation": "冒充", "object": "XX市公安局" }, ...]`。

#### 3.2 知识图谱存储

**接口**：`POST /api/v1/knowledge/store`

**测试样例**：**将上一步 `/extract` 的返回结果直接粘贴**。

**预期结果**：返回 `victim`、`suspect`、`relations`、`transactions` 等结构化数据（格式 1.4）。

#### 3.3 端到端（一步到位）

**接口**：`POST /api/v1/knowledge/pipeline`

**测试样例**：

```json
{
  "case_id": "TEST-PIPE-001",
  "outputs": [
    {
      "text": "报案人经人介绍认识了自称股票专家的案犯甲，案犯甲将其拉入微信群，称可投资电影赚钱。报案人信以为真，通过网银向案犯甲提供的账户转账66000元。后联系不上案犯甲，意识到被骗。",
      "type": "text",
      "status": "done"
    }
  ]
}
```

**预期结果**：一步完成抽取 + 存储，返回格式 1.4 数据。

---

### 第四步：模块四 — 智能研判

**接口**：`GET /api/v1/judge/health`

**预期结果**：`{ "status": "ok", "service": "intelligent-judgment" }`

#### 4.1 单案例研判

**接口**：`POST /api/v1/judge`

**测试样例**：**将 `/pipeline` 或 `/store` 的返回结果直接粘贴**。

**预期结果**：返回 `judgment`（是否诈骗、类型、置信度）+ `alerts`（预警列表）。

---

## 三、端到端流水线测试（全模块打通）

### 第三步：修改 main.py

将 `app/main.py` 恢复为流水线模式（注释掉各模块路由，启用流水线接口）：

```python
# 注释掉各模块路由
# from app.api.multimodal_api import router as multimodal_router
# from app.api.knowledge import router as knowledge_router
# from app.api.intelligentjudge import router as judge_router

# 保留流水线 app 和 /api/v1/pipeline 接口
```

### 测试接口

**接口**：`POST /api/v1/pipeline`

**测试样例**（冒充公检法诈骗）：

```json
{
  "case_id": "E2E-TEST-001",
  "inputs": [
    {
      "type": "text",
      "content": "A：您好，这里是XX市公安局刑侦支队，请问是张先生吗？\nB：是的，什么事？\nA：我们查到你的身份证被冒用开设了一个银行账户，涉嫌洗钱，涉案金额高达200万元。\nB：不可能！我从来没有做过这种事！\nA：请配合调查，否则我们将冻结你名下所有账户，并下发逮捕令。\nB：那我要怎么证明清白？\nA：请下载我们的安全核查APP，将你所有存款转到指定安全账户进行资金核查。\nB：好吧，那我试试..."
    }
  ]
}
```

**预期结果**：

```json
{
  "case_id": "E2E-TEST-001",
  "judgment": {
    "is_fraud": true,
    "fraud_type": "冒充公检法及政府机关类",
    "confidence": "高",
    "confidence_score": 0.96,
    "reason": "嫌疑人冒充公安局...",
    "warning": "⚠️ 立即停止转账..."
  },
  "alerts": [
    {
      "type": "fraud_warning",
      "level": "高",
      "title": "冒充公检法及政府机关类",
      "message": "您正在遭遇冒充公检法及政府机关类。"
    }
  ],
  "deepfake_detected": false,
  "elapsed_ms": 25000
}
```

**更多测试样例**：

```json
// 投资理财诈骗
{
  "case_id": "E2E-TEST-002",
  "inputs": [
    {
      "type": "text",
      "content": "报案人经人介绍认识了自称股票专家的案犯甲，案犯甲将其拉入微信群，称可投资电影赚钱。报案人信以为真，通过网银向案犯甲提供的账户转账66000元。后联系不上案犯甲，意识到被骗。"
    }
  ]
}

// 正常业务（无诈骗）
{
  "case_id": "E2E-TEST-003",
  "inputs": [
    {
      "type": "text",
      "content": "收到银行官方短信通知信用卡还款日提醒，登录银行APP确认后正常还款。"
    }
  ]
}
```

---

## 四、推荐测试顺序总结

```
1. 启动 Neo4j Docker
2. 配置 .env
3. 启动 FastAPI
4. GET  /api/v1/knowledge/health     → 确认 Neo4j 连通
5. POST /api/v1/knowledge/extract    → 文本抽取三元组
6. POST /api/v1/knowledge/store      → 三元组存入 Neo4j
7. POST /api/v1/knowledge/pipeline   → 抽取+存储一步到位
8. GET  /api/v1/judge/health         → 确认研判服务正常
9. POST /api/v1/judge                → 单案例研判
10. 恢复 main.py 为流水线模式
11. POST /api/v1/pipeline             → 端到端全链路验证
```