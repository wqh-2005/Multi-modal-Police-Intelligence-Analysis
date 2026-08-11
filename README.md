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



