import unittest
from validate_event_provenance import validate_events

class EventTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_events([{"id":"1","source":"official","source_url":"https://example.org/a","published_at":"2020-01-01T00:00:00Z"}]), 1)
    def test_missing_source(self):
        with self.assertRaises(ValueError):
            validate_events([{"id":"1"}])
    def test_duplicate(self):
        e={"id":"1","source":"official","source_url":"https://example.org/a","published_at":"2020-01-01T00:00:00Z"}
        with self.assertRaises(ValueError):
            validate_events([e,e])
if __name__ == "__main__": unittest.main()
