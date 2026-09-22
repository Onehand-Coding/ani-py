import os
import tempfile
import unittest
from unittest.mock import patch

import ani_py


class TestHistoryStore(unittest.TestCase):
    def test_update_load_and_clear(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'ANI_PY_HIST_DIR': td}, clear=False):
            store = ani_py.HistoryStore()
            anime = ani_py.Anime('frieren-999', 'Frieren')
            store.update(anime, '1')
            entries = store.load()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].episode, '1')

            store.update(anime, '2')
            entries = store.load()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].episode, '2')

            store.clear()
            self.assertEqual(store.load(), [])

    def test_loads_legacy_three_column_history_as_hianime(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'ANI_PY_HIST_DIR': td}, clear=False):
            store = ani_py.HistoryStore()
            store.path.write_text("3\tfrieren-999\tFrieren\n", encoding="utf-8")
            entries = store.load()
            self.assertEqual(entries[0].provider, "hianime")
            self.assertEqual(entries[0].provider_id, "frieren-999")
            self.assertEqual(entries[0].episode, "3")


if __name__ == '__main__':
    unittest.main()
