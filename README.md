# **多模态警务智能研判系统**

**MPIA (Multi-modal Police Intelligence Analysis)**

# 项目概括

+ 前端框架:Vue
+ 后端框架:FastAPI
+ 主要语言:python
+ LLM编排框架:langchain

# 快速启动

主文件路径: app/main.py，且代码中创建的实例名为 app = FastAPI()：

在终端执行：(**--reload**: **热重载模式**)

```
uvicorn app.main:app --reload
```

# 项目版本管理

| python | 3.10.11 |
| ------ | ------- |
|        |         |
|        |         |
|        |         |

# 项目结构

```text
.
├── app/                        # 应用核心目录
│   ├── main.py                 # FastAPI 入口文件
│   ├── api/                    # 接口路由层
│   │   └── judge_api.py        # 判定逻辑相关接口
│   ├── core/                   # 核心业务逻辑 (原 JudgerCode)
│   │   ├── judger.py           # 逻辑判定核心
│   │   ├── llm_client.py       # 大模型客户端
│   │   ├── rag_engine.py       # RAG 检索引擎
│   │   └── knowledge_graph.py  # 知识图谱处理
│   ├── config/                 # 配置管理
│   │   └── settings.py         # 环境变量与全局配置
│   └── models/                 # Pydantic 数据模型
│       └── response_models.py  # 统一响应格式
├── docs						# 文件
|	├── 数据结构文档.pdf			
| 	└── 接口设计文档				
├── Knowledge_base/             # 本地知识库
│   ├── VectorStore/            # 向量数据库文件
│   └── raw_data/               # 原始数据文档
├── .env                        # 环境变量配置文件
├── .env.example				# 环境变量配置示例文件
├── .gitignore					# 忽略文件
├── README.MD					# readme文件
├── requirements.txt            # 项目依赖清单
└── test_main.http				# 快速测试文件