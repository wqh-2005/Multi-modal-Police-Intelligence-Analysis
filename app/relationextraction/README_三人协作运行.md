# 案例数据集三人协作抽取使用说明

本文档用于学生A、学生B、学生C共同处理 `诈骗案例数据集_带编号.json`。案例总数为82,210篇，三个区间互不重叠，也不会遗漏。

## 1. 任务划分

| 学生 | `start-index` | `limit` | 实际案例编号 | 文档数 | 独立输出根目录 |
|---|---:|---:|---|---:|---|
| 学生A | 0 | 27404 | `case_000001`～`case_027404` | 27,404 | `output/student_a` |
| 学生B | 27404 | 27403 | `case_027405`～`case_054807` | 27,403 | `output/student_b` |
| 学生C | 54807 | 27403 | `case_054808`～`case_082210` | 27,403 | `output/student_c` |

注意：`--start-index` 从0开始计数，因此学生B从27404开始，对应文件中的第27,405篇案例。任何人都不要自行修改自己的起始位置和数量。

## 2. 三个人共同完成的准备工作

### 2.1 需要同步的新文件

项目其他文件已经一致时，至少要把下面内容同步给另外两位学生：

```text
app/relationextraction/
data/raw/诈骗案例数据集_带编号.json
requirements.txt
```

不要发送自己的 `.env`，尤其不要通过聊天软件发送API密钥。每个人在自己的项目根目录中配置 `.env`。

### 2.2 统一配置

三个人必须使用完全相同的模型、Schema和提示词。以下示例使用当前模型：

```dotenv
EXTRACTION_API_KEY=各自的硅基流动API密钥
EXTRACTION_BASE_URL=https://api.siliconflow.cn/v1
EXTRACTION_MODEL=Qwen/Qwen3.5-35B-A3B
LLM_MAX_TOKENS=4096
LLM_TIMEOUT=240
LLM_MAX_RETRIES=3
```

如果负责人最终决定使用 `Qwen/Qwen3.5-35B-A3B`，三个人必须在开始正式任务前一起改成该模型，不能有人使用2.5、有人使用3.5。

每个配置键只保留一行，推荐使用普通的 `KEY=value` 格式，不要同时保留 `EXTRACTION_MODEL=...` 和 `$env:EXTRACTION_MODEL=...`。

三个人可以使用不同API密钥。若共用同一个密钥，账户余额、TPM/RPM限流和费用也是共享的，三个程序同时运行时更容易触发限流。

### 2.3 进入项目并检查

每个人都打开PowerShell并执行：

```powershell
Set-Location "D:\Multi-modal-Police-Intelligence-Analysis"
python -m app.relationextraction.code.llmuse check
```

检查结果必须包含：

```text
status: ok
case: 82210
all: 83248
api_called: false
```

`check` 不会调用大模型，不产生API费用。如果这里失败，不要开始正式任务。

### 2.4 核对共同文件

如需确认三个人拿到的是同一版本，可执行：

```powershell
Get-FileHash -Algorithm SHA256 "data\raw\诈骗案例数据集_带编号.json"
Get-FileHash -Algorithm SHA256 "app\relationextraction\schemas\实体类型体系_v0.1_中文可读版.json"
Get-FileHash -Algorithm SHA256 "app\relationextraction\schemas\关系类型体系_v0.1_中文可读版.json"
Get-FileHash -Algorithm SHA256 "app\relationextraction\prompts\一体化三元组抽取_v1.0.txt"
```

当前正确哈希如下：

```text
BDB7DE566D0EB988B7EE258C28EF9EF4E4EB24FFCA397139F8F209D1A6B4411C  诈骗案例数据集_带编号.json
DFE428D08DC4D577A9DF4C17F7AC45600A1F9EDA6A803DCABA54B005DB958588  实体类型体系_v0.1_中文可读版.json
FB0897AA54DD4471E753359323F313BD91608BE2D95C1114367D1CA681185AFB  关系类型体系_v0.1_中文可读版.json
1C5DB2350D02C0022E11A89CF32B7944EA8AE3795CE32AE5D6E0658685D6F2FE  一体化三元组抽取_v1.0.txt
```

## 3. 学生A操作步骤

### 3.1 先测试第一篇

```powershell
python -m app.relationextraction.code.llmuse run --source-type case --start-index 0 --limit 1 --output "app\relationextraction\output\student_a"
```

测试结果位于：

```text
app/relationextraction/output/student_a/case/
```

确认终端显示 `completed: 1`、`failed: 0` 后再开始正式运行。

### 3.2 正式启动命令

```powershell
python -m app.relationextraction.code.llmuse run --source-type case --start-index 0 --limit 27404 --output "app\relationextraction\output\student_a"
```

之前测试成功的第一篇会自动跳过，不会重复收费。

### 3.3 查看学生A进度

另开一个PowerShell窗口，在项目根目录执行：

```powershell
python -m app.relationextraction.code.llmuse status --source-type case --start-index 0 --limit 27404 --output "app\relationextraction\output\student_a"
```

完成时应为 `documents: 27404`、`success: 27404`、`failed: 0`、`pending: 0`。

## 4. 学生B操作步骤

### 4.1 先测试自己区间的第一篇

```powershell
python -m app.relationextraction.code.llmuse run --source-type case --start-index 27404 --limit 1 --output "app\relationextraction\output\student_b"
```

该命令处理的是 `case_027405`。确认成功后再正式运行。

### 4.2 正式启动命令

```powershell
python -m app.relationextraction.code.llmuse run --source-type case --start-index 27404 --limit 27403 --output "app\relationextraction\output\student_b"
```

### 4.3 查看学生B进度

```powershell
python -m app.relationextraction.code.llmuse status --source-type case --start-index 27404 --limit 27403 --output "app\relationextraction\output\student_b"
```

完成时应为 `documents: 27403`、`success: 27403`、`failed: 0`、`pending: 0`。

## 5. 学生C操作步骤

### 5.1 先测试自己区间的第一篇

```powershell
python -m app.relationextraction.code.llmuse run --source-type case --start-index 54807 --limit 1 --output "app\relationextraction\output\student_c"
```

该命令处理的是 `case_054808`。确认成功后再正式运行。

### 5.2 正式启动命令

```powershell
python -m app.relationextraction.code.llmuse run --source-type case --start-index 54807 --limit 27403 --output "app\relationextraction\output\student_c"
```

### 5.3 查看学生C进度

```powershell
python -m app.relationextraction.code.llmuse status --source-type case --start-index 54807 --limit 27403 --output "app\relationextraction\output\student_c"
```

完成时应为 `documents: 27403`、`success: 27403`、`failed: 0`、`pending: 0`。

## 6. 中断、失败和续跑

### 6.1 可以中断吗

可以。需要停止时在运行窗口按一次 `Ctrl+C`。单篇结果采用原子写入，已经成功保存的文档不会丢失。

### 6.2 如何继续

重新执行本人第3.2、4.2或5.2节中的同一条正式启动命令。程序会跳过成功且配置未变化的文档，只处理失败、未处理或发生变化的文档。

不要改变下面任何内容：

- `--start-index`；
- `--limit`；
- `--output`；
- `.env`中的模型；
- Schema和提示词。

### 6.3 出现少量失败怎么办

先执行本人的 `status` 命令确认失败数，然后直接重新执行正式启动命令。成功结果会跳过，失败结果会重新请求。不要使用 `--overwrite`，否则会重新调用已经成功的文档。

## 7. 输出文件说明

以学生A为例：

```text
output/student_a/case/
├── by_document/       # 每篇成功结果，按哈希分片
├── failures/          # 最后一次仍失败的文档
├── triples.jsonl      # 学生A区间的全部三元组
├── summary.json       # 成功、失败、待处理和三元组数量
└── run_manifest.json  # 本次数据、模型、Prompt和Schema记录
```

学生B、学生C的结构相同。`run_manifest.json` 不属于最终三元组，但建议随结果一起交付，用于证明三个人使用了相同模型和资源版本。

## 8. 每个人完成后的交付要求

完成后先确认 `failed: 0`、`pending: 0`。然后分别压缩并交付以下整个目录：

```text
学生A：app/relationextraction/output/student_a/case
学生B：app/relationextraction/output/student_b/case
学生C：app/relationextraction/output/student_c/case
```

不要只发送 `triples.jsonl`，还要保留 `by_document`、`summary.json` 和 `run_manifest.json`，方便最终核对、去重和重新汇总。不要打包或发送 `.env`。

三个分区的文档数之和必须为：

```text
27404 + 27403 + 27403 = 82210
```

最终合并前，负责人必须确认三份 `summary.json` 的 `success` 总和为82,210，且三份结果的 `failed` 和 `pending` 都是0。
