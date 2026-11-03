import unittest

import render


class RenderTest(unittest.TestCase):
    def test_lists_items(self):
        text = render.render_order({"id": "A1", "status": "shipped", "items": ["mug"]})
        self.assertIn("Order A1 (shipped)", text)
        self.assertIn("  - mug", text)


if __name__ == "__main__":
    unittest.main()
