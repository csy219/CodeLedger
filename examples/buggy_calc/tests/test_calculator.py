# 这个测试一开始会失败：
# add(2, 3) 返回 -1

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculator import add, multiply


class TestCalculator(unittest.TestCase):
    def test_add(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_multiply(self) -> None:
        self.assertEqual(multiply(2, 3), 6)


if __name__ == "__main__":
    unittest.main()


