# LLM关系抽取离线批处理

三人共同运行完整案例数据集时，请阅读：[README_三人协作运行.md](README_三人协作运行.md)。

本模块用于从原始对话和案例数据中批量抽取中文三元组，不替换
`app/core/knowledge/extraction_service.py` 的在线API实现。

## 目录职责

- `code/config.py`：仓库路径和LLM配置。
- `code/llmclient.py`：单篇结构化LLM调用。
- `code/llmuse.py`：流式批处理、断点续跑、状态和汇总。
- `prompts/`、`schemas/`：正式抽取使用的提示词和Schema。
- `build_gold.py`、`expand_test_data.py`、`select_v3_candidates.py`、`清洗.py`：历史实验工具，不是正式运行入口。

## 使用方法

所有命令都在仓库根目录执行。

```powershell
# 检查.env、资源文件和83248篇原始数据，不调用API
python -m app.relationextraction.code.llmuse check

# 先真实测试1篇（会调用API）
python -m app.relationextraction.code.llmuse run --limit 1

# 再测试20篇；成功的第1篇会自动跳过
python -m app.relationextraction.code.llmuse run --limit 20

# 查看这20篇的进度和Token统计
python -m app.relationextraction.code.llmuse status --limit 20
```

每次 `run` 正常结束后，会在对应来源目录中自动生成 `triples.jsonl` 和 `summary.json`，无需再手动汇总。`export` 命令仅用于运行被中断后、希望立即重建当前汇总文件的情况。

全量运行默认单并发：

```powershell
python -m app.relationextraction.code.llmuse run
```

确认API限流与费用后，可在1至8之间调整并发，例如：

```powershell
python -m app.relationextraction.code.llmuse run --concurrency 3
```

按来源、范围运行：

```powershell
python -m app.relationextraction.code.llmuse run --source-type case --start-index 10000 --limit 1000
```

默认输出根目录为 `app/relationextraction/output`，其中对话结果完整存放在 `output/dialogue`，案例结果完整存放在 `output/case`。每个来源目录分别包含 `run_manifest.json`、`summary.json`、`triples.jsonl`、`by_document/` 和 `failures/`；单篇文件继续按哈希分片保存。中断后重新执行同一命令即可续跑。程序按单篇文档维护缓存：新增数据只处理新增篇目；原文未变且抽取配置一致时自动跳过；同一编号的原文、模型、Schema或提示词变化时自动重跑该篇。运行清单会保留配置变更历史，不再因为全量文件哈希变化而阻止运行。

## 配置

读取仓库根目录 `.env`，兼容标准 `KEY=value` 和PowerShell的 `$env:KEY=value`。推荐设置：

```dotenv
EXTRACTION_API_KEY=...
EXTRACTION_BASE_URL=...
EXTRACTION_MODEL=...
LLM_MAX_TOKENS=4096
LLM_TIMEOUT=240
LLM_MAX_RETRIES=3
```

重复键会给出警告，同一 `.env` 文件中最后一项生效；进程环境变量优先。
