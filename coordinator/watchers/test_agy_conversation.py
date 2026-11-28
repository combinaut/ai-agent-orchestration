"""Conversation resolution from agy's local SQLite store."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_support import IsolatedTestCase

import agy_conversation


class ConversationResolutionTests(IsolatedTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "conversations"
        self.store.mkdir()

    def conversation(self, identifier, folder):
        database = self.store / f"{identifier}.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE trajectory_metadata_blob (id TEXT PRIMARY KEY, data BLOB)"
            )
            connection.execute(
                "INSERT INTO trajectory_metadata_blob VALUES ('main', ?)",
                (f"file://{folder}".encode(),),
            )
            connection.commit()
        finally:
            connection.close()

    def test_resolves_the_one_conversation_for_a_worktree(self):
        folder = self.root / "one"
        self.conversation("11111111-1111-1111-1111-111111111111", folder)

        self.assertEqual(
            agy_conversation.resolve(folder, self.store),
            "11111111-1111-1111-1111-111111111111",
        )

    def test_uses_the_panes_worktree_when_multiple_conversations_are_live(self):
        first = self.root / "first"
        second = self.root / "second"
        self.conversation("11111111-1111-1111-1111-111111111111", first)
        self.conversation("22222222-2222-2222-2222-222222222222", second)

        self.assertEqual(
            agy_conversation.resolve(second, self.store),
            "22222222-2222-2222-2222-222222222222",
        )

    def test_returns_unknown_when_no_conversation_matches_the_worktree(self):
        self.conversation("11111111-1111-1111-1111-111111111111", self.root / "other")

        self.assertIsNone(agy_conversation.resolve(self.root / "missing", self.store))

    def test_does_not_match_a_worktree_name_prefix(self):
        folder = self.root / "worktree"
        self.conversation(
            "11111111-1111-1111-1111-111111111111", self.root / "worktree-copy"
        )

        self.assertIsNone(agy_conversation.resolve(folder, self.store))

    def test_returns_unknown_when_multiple_conversations_match_the_worktree(self):
        folder = self.root / "shared"
        self.conversation("11111111-1111-1111-1111-111111111111", folder)
        self.conversation("22222222-2222-2222-2222-222222222222", folder)

        self.assertIsNone(agy_conversation.resolve(folder, self.store))


if __name__ == "__main__":
    unittest.main()
