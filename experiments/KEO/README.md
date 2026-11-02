# KEO 实验

复用已有诈骗案例和三元组，对比直接回答、文本 RAG 与图谱 RAG 的研判效果。

## 目录用途

```text
KEO/
├─ configs/          # 公共配置与各模块参数（对应早期 README 的 common/configs）
├─ docs/             # 分工说明、接口约定与运行说明（对应早期 README 的 common/docs）
├─ core/
│  ├─ data/          # A：数据对齐检查、去重、划分、名称清洗与 KEO 1.1 交付包导出（已实现）
│  ├─ text_rag/      # B：文本检索与直接回答基线
│  ├─ graph_rag/     # C：建图、实体检索、邻居扩展与上下文重建
│  └─ evaluation/    # D：测试题组织、参考答案管理与评价代码
├─ data/             # 本地数据（不入 git）：person_a/ 为 A 的冻结产物与交付包
└─ models/           # 回答模型、Embedding 与评判模型的调用封装
```

## A 模块入口（已实现，2026-11-02）

代码在 `experiments/KEO/core/data/`，本地数据在 `experiments/KEO/data/person_a/`（`KEO_DATA_ROOT` 可覆盖）。

```powershell
python -m experiments.KEO.core.data.build_dataset --help   # 重建对齐检查与 60/40 划分
python -m experiments.KEO.core.data.audit_entity_names     # 实体名称审计（只读）
python -m experiments.KEO.core.data.apply_name_cleaning    # 执行名称清洗，产出别名映射
python -m experiments.KEO.core.data.export_keo             # 导出 KEO 格式 1.1 共用交付包
python -m experiments.KEO.core.data.export_handoff         # 导出 B / C / D 角色分包
python -m experiments.KEO.core.data.freeze_dataset         # 重算 SHA256 并冻结 manifest
```

交付包（版本 `1.0-final`，已冻结）：

```text
experiments/KEO/data/person_a/handoff/
├── keo/                  # 共用包（B/C/D 同一份）
│   ├── 案例数据.json      # 82,210 条
│   ├── 三元组数据.jsonl   # 1,008,822 条
│   ├── 划分名单.json      # build 49,289 / eval 32,859
│   ├── 实体别名映射.json  # C 建图直接读取
│   └── 交付说明.md
├── b/  cases_build.jsonl / build_ids.txt / dev_ids.txt / manifest.json
├── c/  triples_build.jsonl / build_ids.txt / 实体别名映射.json / 归一化规则.md / manifest.json
└── d/  cases_eval.jsonl / eval_ids.txt / manifest.json
```

角色分包是共用包的切片，不重新抽取：B 拿建库原文 + 开发集，C 拿建库三元组 + 名称映射，D 只拿评估侧原文与真实类别。

版本、校验和与复现命令见 `experiments/KEO/data/person_a/冻结说明.md` 与 `dataset_manifest.json`。

## 协作约定

- 各自主要修改负责的模块，公共配置、模型接口及数据格式变更先沟通。
- 原始数据和已有抽取结果保留原位置，通过配置读取；共用固定的案例划分名单。
- 数据、模型权重、索引及运行结果保存在本地，不提交 GitHub；保存位置由配置指定。
