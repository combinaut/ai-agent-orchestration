"""close_worker.add_line and recovery_line: the Closed workers section of a status file."""

import unittest
from datetime import UTC, datetime

from test_support import IsolatedTestCase

import close_worker as cw

NEW = "- NEW"


def found(**kwargs):
    return {
        "pane": "wW:p1",
        "name": "fix-it",
        "worktree": "/wt",
        "branch": "feat/x",
        "session": "abc",
        "agent": "claude",
        **kwargs,
    }


class AddLineTests(IsolatedTestCase):
    def test_a_missing_section_is_added_at_the_end(self):
        self.assertEqual(
            cw.add_line("# A\n\n## Work\n- x\n", NEW),
            "# A\n\n## Work\n- x\n\n## Closed workers\n- NEW\n",
        )

    def test_the_new_line_follows_the_last_line_of_the_section(self):
        text = "## Closed workers\nnote\n- a\n\n## Next\n- x\n"
        self.assertEqual(
            cw.add_line(text, NEW),
            "## Closed workers\nnote\n- a\n- NEW\n\n## Next\n- x\n",
        )

    def test_an_empty_section_gets_the_line_under_its_heading(self):
        self.assertEqual(
            cw.add_line("## Closed workers\n## Next\n- x\n", NEW),
            "## Closed workers\n- NEW\n## Next\n- x\n",
        )

    def test_only_dash_lines_count_toward_the_cap_of_ten(self):
        lines = [f"- e{i}" for i in range(10)]
        lines.insert(4, "note")
        text = "## Closed workers\n" + "\n".join(lines) + "\n"
        expected = lines[1:] + [NEW]
        self.assertEqual(
            cw.add_line(text, NEW), "## Closed workers\n" + "\n".join(expected) + "\n"
        )

    def test_nine_entries_and_a_note_keep_every_entry(self):
        lines = [f"- e{i}" for i in range(9)] + ["note"]
        text = "## Closed workers\n" + "\n".join(lines) + "\n"
        self.assertEqual(
            cw.add_line(text, NEW),
            "## Closed workers\n" + "\n".join(lines + [NEW]) + "\n",
        )

    def test_a_single_hash_heading_ends_the_section(self):
        items = "".join(f"- w{i}\n" for i in range(12))
        text = f"## Closed workers\n- a\n# Waits on the user\n{items}"
        self.assertEqual(
            cw.add_line(text, NEW),
            f"## Closed workers\n- a\n- NEW\n# Waits on the user\n{items}",
        )

    def test_a_deeper_heading_ends_the_section(self):
        text = "## Closed workers\n- a\n### More\n- m1\n- m2\n"
        self.assertEqual(
            cw.add_line(text, NEW),
            "## Closed workers\n- a\n- NEW\n### More\n- m1\n- m2\n",
        )

    def test_a_file_without_a_final_newline_keeps_none(self):
        self.assertEqual(
            cw.add_line("## Closed workers\n- 1", NEW), "## Closed workers\n- 1\n- NEW"
        )


class RecoveryLineTests(IsolatedTestCase):
    NOW = datetime(2026, 10, 22, 11, 40, tzinfo=UTC)

    def line(self, task="t", report="", **kwargs):
        return cw.recovery_line(found(**kwargs), task, report, self.NOW)

    def test_every_field_is_one_line(self):
        line = self.line(
            task="a\nb",
            report="/r\n## Waits on the user",
            name="n\nm",
            worktree="/w\n/t",
            branch="b\nc",
            session="s\nt",
        )
        self.assertNotIn("\n", line)
        self.assertEqual(line.count("\n"), 0)

    def test_the_resume_command_follows_the_agent(self):
        self.assertTrue(
            self.line(agent="claude").endswith("resume: claude --resume abc")
        )
        self.assertTrue(self.line(agent="codex").endswith("resume: codex resume abc"))
        self.assertTrue(self.line(agent="pi").endswith("resume: session abc"))
        self.assertTrue(self.line(agent="").endswith("resume: session abc"))


if __name__ == "__main__":
    unittest.main()
