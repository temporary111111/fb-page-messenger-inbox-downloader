import tempfile
import threading
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from inbox_archive.database import Database


class BindingConcurrencyTests(unittest.TestCase):
    def test_concurrent_first_starts_claim_exactly_one_page(self):
        with tempfile.TemporaryDirectory() as directory:
            for attempt in range(8):
                root=Path(directory)/str(attempt)
                barrier=threading.Barrier(6)
                def claim(index):
                    barrier.wait()
                    try:
                        Database(root,str(index))
                        return str(index)
                    except ValueError:
                        return None
                    except sqlite3.OperationalError as exc:
                        if 'locked' not in str(exc).lower():
                            raise
                        return None  # A busy first initializer may safely retry; it cannot claim another Page.
                with ThreadPoolExecutor(max_workers=6) as pool:
                    winners=[value for value in pool.map(claim,range(6)) if value is not None]
                self.assertEqual(len(winners),1)
                self.assertEqual(Database(root,winners[0]).page_id,winners[0])


if __name__ == '__main__':
    unittest.main()
