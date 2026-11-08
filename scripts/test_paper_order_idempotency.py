import unittest
from paper_order_idempotency import PaperOrderBook

class OrderTests(unittest.TestCase):
    def test_replay(self):
        book=PaperOrderBook()
        order={"client_order_id":"a","symbol":"BTC","side":"buy","quantity":"1"}
        self.assertTrue(book.accept(order))
        self.assertFalse(book.accept(order))
    def test_collision(self):
        book=PaperOrderBook()
        book.accept({"client_order_id":"a","symbol":"BTC","side":"buy","quantity":"1"})
        with self.assertRaises(ValueError):
            book.accept({"client_order_id":"a","symbol":"BTC","side":"buy","quantity":"2"})
    def test_invalid(self):
        with self.assertRaises(ValueError):
            PaperOrderBook().accept({"client_order_id":"a","symbol":"BTC","side":"buy","quantity":"NaN"})
if __name__ == "__main__": unittest.main()
