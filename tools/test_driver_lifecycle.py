"""回归测试：storage_service.py 驱动生命周期（_create_driver / _reset_driver / _get_driver）。

覆盖 649a630 新增的两个函数：
- _create_driver: 独立创建 Neo4j 驱动实例
- _reset_driver: 关闭并重置驱动（仅测试用）
- _get_driver 的失效自动重建（TestClient 事件循环变更场景）

运行: .venv/bin/python -m unittest tools.test_driver_lifecycle -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.knowledge.storage_service import (
    _create_driver,
    _get_driver,
    _reset_driver,
    _driver_instance,
)


class TestDriverLifecycle(unittest.TestCase):
    def setUp(self):
        _reset_driver()

    def tearDown(self):
        _reset_driver()

    def test_create_driver_returns_instance(self):
        d = _create_driver()
        self.assertIsNotNone(d)
        self.assertEqual(type(d).__name__, "AsyncBoltDriver")

    def test_get_driver_creates_singleton(self):
        d1 = _get_driver()
        d2 = _get_driver()
        self.assertIs(d1, d2, "连续调用应复用同一驱动实例")

    def test_get_driver_rebuilds_after_invalidation(self):
        """TestClient 事件循环变更后驱动失效（_pool=None），应自动重建。"""
        d1 = _get_driver()
        d1._pool = None  # 模拟失效
        d2 = _get_driver()
        self.assertIsNot(d1, d2, "失效后应重建新驱动")

    def test_reset_driver_clears_instance(self):
        _get_driver()
        _reset_driver()
        self.assertIsNone(_driver_instance, "重置后驱动应置空")

    def test_get_driver_after_reset_rebuilds(self):
        d1 = _get_driver()
        _reset_driver()
        d2 = _get_driver()
        self.assertIsNot(d1, d2, "重置后应重建新驱动")


if __name__ == "__main__":
    unittest.main()
