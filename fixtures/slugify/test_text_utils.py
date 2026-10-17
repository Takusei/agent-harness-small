import unittest

from text_utils import slugify


class SlugifyTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_whitespace(self):
        self.assertEqual(slugify("  Hello\t  World\n Again "), "hello-world-again")

    def test_empty(self):
        self.assertEqual(slugify("   "), "")
