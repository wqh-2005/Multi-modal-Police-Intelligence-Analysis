# 中文案例弱监督试验

这次训练使用 Qwen 抽取结果作为伪标签，未经过人工审核。验证和测试衡量与伪标签的一致性，不是人工金标准确率，也不能作为最终论文中大模型优劣的证据。

## 输入与转换范围

- 原文：主项目 `data/raw/诈骗案例数据集_带编号.json` 的前 10,000 条。
- 伪标签：主项目 `app/relationextraction/output/case/triples.jsonl` 中对应案例的三元组。
- 初步检查：122,858 条输入三元组，43,718 条的头尾名称都能直接在原文找到，79,140 条不能。后续窗口和 token 对齐检查还会进一步过滤。
- ARETO 是连续片段抽取模型。不会把“受害人”“未知”等推断名称强行替换成原文，也不会自动解决共指。
- 关系类别使用现有诈骗关系体系的中文关系名称。人物角色、事实状态、证据和置信度不纳入模型输出；事实状态分布记录在报告中。

数据集位于 `datasets/case_weak_v1`，包含 `train.json`、`dev.json`、`test.json`、`rel2id.json`、`split_manifest.json`、`preparation_report.json` 和 `excluded_triples.jsonl`。

先按去除空白后完全相同的原文分组，以随机种子 2026 按组划分约 80%/10%/10%，再切窗口。同一文档和完全重复文本不会跨集合；未做近似重复去重。窗口长度不超过 256 token，步长 128；只保留实体在同一窗口且 token 可定位的三元组。

这是**含可定位正标签的窗口子集**：没有保留标签的窗口不用于这次试验，不把它们视为可信负例。跨窗口、非连续、共指或归一化名称无法直接处理。窗口重叠会造成同一三元组重复计数，现有指标是窗口级指标，而不是完整案例级指标。论文对比前需人工金标、完整文档汇总去重和统一评价协议。

## 中文模型与代码

复用本机已有的 `hfl/chinese-macbert-base` 缓存，复制到 `models/chinese-macbert-base`；缓存版本为 `a986e004d2a7f2a1c2f5a3edef4e20604a974ed1`。它是 BERT 架构的中文编码器，ARETO 抽取网络保持不变。此配置应称为“中文编码器适配后的 ARETO”，不同于论文英文编码器设置。

分词器读取对应模型目录的配置。训练根据验证集 F1 保存权重，已删除按测试集 F1 额外选权重的逻辑。另修正无预测时的 precision、最后一条数据无效时丢弃剩余 batch 的问题，以及平均训练损失的 batch 计数。

## PowerShell 命令

完整训练由你启动。先运行小样本，确认反向传播、保存和评估：

```powershell
Set-Location "D:\Multi-modal-Police-Intelligence-Analysis\experiments\ARETO"

.\.venv\Scripts\python.exe check_setup.py --dataset case_weak_v1 --bert_dir ".\models\chinese-macbert-base" --max_len 256

.\.venv\Scripts\python.exe run.py --dataset case_weak_v1 --bert_dir ".\models\chinese-macbert-base" --device cuda --max_len 256 --batch_size 2 --num_train_epochs 1 --train_samples 8 --eval_samples 2 --file_id case_weak_smoke

# 小样本通过后，先做 5 轮试验，观察学习情况和每轮耗时
.\.venv\Scripts\python.exe run.py --dataset case_weak_v1 --bert_dir ".\models\chinese-macbert-base" --device cuda --max_len 256 --batch_size 2 --num_train_epochs 5 --file_id case_weak_001

# 对最优验证集权重单独进行伪标签测试集评估
.\.venv\Scripts\python.exe run.py --dataset case_weak_v1 --bert_dir ".\models\chinese-macbert-base" --device cuda --max_len 256 --train test --file_id case_weak_001
```

8GB 显存如不足，改成 `--batch_size 1`。耗时以实际第一轮为准；不要按英文数据的小样本耗时估算。当前无断点续训，改变轮数重新运行会从预训练中文编码器开始，应换 `--file_id`。

准备脚本为 `prepare_case_weak.py`。它拒绝覆盖已有数据集；需要重新生成时请给不同的 `--dataset case_weak_v2`，并检查报告与版本差异。
