"""D 的数据泄漏检查：测试集与建库集是否重叠。"""
from experiments.KEO.configs import config


def check_source_id_overlap() -> int:
    splits = config.load_splits()
    build_ids = set(splits["build_ids"])

    test_ids = set(
        line.strip()
        for line in config.D_TEST_IDS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )

    overlap = build_ids & test_ids

    if overlap:
        print(f"❌ 数据泄漏：{len(overlap)} 个测试集 source_id 出现在建库集：")
        for sid in sorted(overlap)[:10]:
            print(f"   {sid}")
        return 1
    else:
        print(f"✅ source_id 层面无重叠（测试集 {len(test_ids)} 题 vs 建库集 {len(build_ids)} 题）")
        return 0


if __name__ == "__main__":
    raise SystemExit(check_source_id_overlap())