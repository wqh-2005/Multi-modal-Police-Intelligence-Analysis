# ARETO 本地运行说明

中文案例伪标签试验及命令见 [README.case-weak.md](README.case-weak.md)。

项目路径：`D:\Multi-modal-Police-Intelligence-Analysis\experiments\ARETO`。

上游仓库：https://github.com/104wucan/ARETO 。拉取版本：`54d3c94c74d72c4bec2c783076f040eeef61b176`。

## 环境与数据

`.venv\Scripts\python.exe` 使用 Python 3.12.7，通过 `--system-site-packages` 复用 `D:\ana` 的 CUDA PyTorch 和 Transformers，避免重复下载大型 GPU 包。因此它依赖本机 `D:\ana`；迁移机器需重建环境。实际依赖版本见 `environment-verified.txt`。

自带数据集是 `datasets\WebNLG_star`，共训练 5,019 条、验证 500 条、测试 703 条、171 种关系。原始数据未修改。原 README 的 `nyt` 和源代码默认的 `NYT_star` 在仓库中不存在；默认参数已改为 `WebNLG_star`。入口是 `run.py`，仓库没有 `train.py`。

本地 BERT 位于 `models\bert-base-cased`，来自官方 `google-bert/bert-base-cased`，包含配置、词表和 safetensors 权重。训练从本地目录加载。

## PowerShell 命令

无需激活虚拟环境，直接使用它的 Python：

```powershell
Set-Location "D:\Multi-modal-Police-Intelligence-Analysis\experiments\ARETO"

# 环境、数据、模型加载和 GPU 前向计算检查，不训练
.\.venv\Scripts\python.exe check_setup.py

# 建议先自行跑一次小样本训练，检查反向传播、保存和评估
.\.venv\Scripts\python.exe run.py --dataset WebNLG_star --device cuda --batch_size 2 --num_train_epochs 1 --train_samples 8 --eval_samples 2 --file_id smoke

# 完整数据集训练
.\.venv\Scripts\python.exe run.py --dataset WebNLG_star --device cuda --batch_size 2 --num_train_epochs 100 --file_id webnlg_001

# 用完整训练得到的最优验证集权重评估完整测试集
.\.venv\Scripts\python.exe run.py --dataset WebNLG_star --device cuda --train test --file_id webnlg_001
```

结果写入 `output\WebNLG_star\<file_id>`，包括配置、日志、最优验证集权重 `pytorch_model.bin` 和验证/测试预测文件。不同实验请换 `--file_id`，避免混用输出。

RTX 4060 Laptop GPU 为 8GB 显存，默认 batch size 改为 2；若训练仍显存不足，可用 `--batch_size 1`。原作者默认 batch size 为 64，本机的小 batch 可能影响收敛与论文指标。前向检查通过不代表完整训练或论文指标复现完成。

## 模型路径与依赖

本机模型已下载。需要重新下载时运行：

```powershell
.\.venv\Scripts\python.exe download_bert.py
```

另行下载时，把官方 `bert-base-cased` 的 `config.json`、`vocab.txt` 和 `model.safetensors` 或 `pytorch_model.bin` 放在同一目录，给训练命令增加 `--bert_dir "D:\你的模型目录\bert-base-cased"` 即可。路径以脚本目录为基准，不依赖当前工作目录。

`requirements.txt` 保留上游的环境清单（包含无关依赖）。新建环境可参考精简的 `requirements-reproduction.txt`，CUDA 版 PyTorch 应按机器驱动安装。

## 本地兼容修改

- 修正默认数据集、入口说明和作者电脑上的 BERT 绝对路径；增加设备选择和样本限制参数。
- 将已废弃的 `np.int` 改为 `np.int64`。
- 新版 Transformers 已移除原 AdamW 导入，改用 `torch.optim.AdamW`。优化器实现与上游不同，严格数值复现时需考虑此差异。
- 原依赖未包含 `bert4keras`，且代码要求分词器返回三项，而公开接口返回两项。新增 `tokenizer.py`，用 Transformers 的 cased BERT WordPiece 提供 token ID、segment ID、attention mask 和评估所需字符偏移；未与作者未公开的定制分词器逐 token 验证等价性。
- `model.py` 网络结构保持不变。辅助脚本 `testsre.py` 等仍保留原作者路径，本机统一使用 `run.py`。

初次加载时提示 ARETO 的新增抽取层未从 BERT 权重初始化是预期现象；这些层需要后续训练。
