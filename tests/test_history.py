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

    def test_remove_drops_only_the_named_entries(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'ANI_PY_HIST_DIR': td}, clear=False):
            store = ani_py.HistoryStore()
            store.update(ani_py.Anime('frieren-999', 'Frieren'), '1', completed=True)
            store.update(ani_py.Anime('one-piece-100', 'One Piece'), '4')
            store.update(ani_py.Anime('bleach-1', 'Bleach'), '2')

            removed = store.remove([ani_py.HistoryEntry('4', 'hianime', 'one-piece-100', 'One Piece')])
            self.assertEqual(removed, 1)

            remaining = store.load()
            self.assertEqual(
                [(e.provider_id, e.episode, e.completed) for e in remaining],
                [('frieren-999', '1', True), ('bleach-1', '2', False)],
            )

    def test_remove_absent_entry_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'ANI_PY_HIST_DIR': td}, clear=False):
            store = ani_py.HistoryStore()
            store.update(ani_py.Anime('frieren-999', 'Frieren'), '1')
            before = store.path.read_text(encoding="utf-8")

            self.assertEqual(store.remove([ani_py.HistoryEntry('9', 'hianime', 'naruto-20', 'Naruto')]), 0)
            self.assertEqual(store.remove([]), 0)
            self.assertEqual(store.path.read_text(encoding="utf-8"), before)

    def test_remove_matches_legacy_three_column_rows(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'ANI_PY_HIST_DIR': td}, clear=False):
            store = ani_py.HistoryStore()
            store.path.write_text("3\tfrieren-999\tFrieren\n", encoding="utf-8")

            removed = store.remove([ani_py.HistoryEntry('3', 'hianime', 'frieren-999', 'Frieren')])
            self.assertEqual(removed, 1)
            self.assertEqual(store.load(), [])


if __name__ == '__main__':
    unittest.main()
