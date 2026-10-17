import unittest

from math_utils import inclusive_sum


class InclusiveSumTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(inclusive_sum(0), 0)

    def test_one(self):
        self.assertEqual(inclusive_sum(1), 1)

    def test_three(self):
        self.assertEqual(inclusive_sum(3), 6)
