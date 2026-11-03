import unittest

import orders


class OrdersTest(unittest.TestCase):
    def test_get_order(self):
        self.assertEqual(orders.get_order("A100")["status"], "shipped")

    def test_unknown_order(self):
        with self.assertRaises(KeyError):
            orders.get_order("nope")


if __name__ == "__main__":
    unittest.main()
