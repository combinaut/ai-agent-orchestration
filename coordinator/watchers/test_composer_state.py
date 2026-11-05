import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedTestCase

spec = importlib.util.spec_from_file_location(
    "composer_state", Path(__file__).parents[1] / "composer-state.py"
)
composer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(composer)

RULE = "\x1b[0m\x1b[38;2;136;136;136m" + "─" * 40 + "\x1b[0m\r"
FOOTER = (
    "  \x1b[0m\x1b[38;5;2mHaiku 5.5\x1b[0m\x1b[38;2;153;153;153m \x1b[0m"
    "\x1b[2m\x1b[38;2;153;153;153m|\x1b[0m\r\n"
    "  \x1b[0m\x1b[38;2;255;193;7m⏵⏵ auto mode on\x1b[0m"
)


def screen(*composer_lines, above="\x1b[0m\x1b[38;2;153;153;153m✻ Crunched\x1b[0m\r"):
    return "\n".join([above, RULE, *composer_lines, RULE, FOOTER])


class TypedTextTest(IsolatedTestCase):
    def test_no_input_is_empty(self):
        self.assertEqual(composer.typed_text(screen("❯\xa0\r")), "")

    def test_dim_first_session_suggestion_is_empty(self):
        line = '❯\xa0\x1b[0m\x1b[2mTry "create a util logging.py that..."\x1b[0m\r'
        self.assertEqual(composer.typed_text(screen(line)), "")

    def test_dim_next_prompt_suggestion_is_empty(self):
        line = "❯\xa0\x1b[0m\x1b[2mcontinue with step 2\x1b[0m\r"
        self.assertEqual(composer.typed_text(screen(line)), "")

    def test_typed_text_counts(self):
        self.assertEqual(
            composer.typed_text(screen("❯\xa0hello typed\r")), "hello typed"
        )

    def test_pending_paste_counts(self):
        line = "❯\xa0[Pasted text #1 +29 lines]\r"
        self.assertEqual(
            composer.typed_text(screen(line)), "[Pasted text #1 +29 lines]"
        )

    def test_multiline_input_counts_every_line(self):
        self.assertEqual(
            composer.typed_text(screen("❯\xa0first\r", "  second\r")), "first\nsecond"
        )

    def test_typed_text_keeps_only_the_undimmed_part(self):
        line = "❯\xa0abc\x1b[2m def\x1b[0m\r"
        self.assertEqual(composer.typed_text(screen(line)), "abc")

    def test_colour_arguments_are_not_dim(self):
        for color in ("38;5;2", "38;2;2;2;2", "48;5;2", "1;38;2;2;3;4"):
            with self.subTest(color=color):
                line = f"❯\xa0\x1b[{color}mtyped\x1b[0m\r"
                self.assertEqual(composer.typed_text(screen(line)), "typed")

    def test_sgr_22_ends_dim(self):
        line = "❯\xa0\x1b[2mghost\x1b[22mreal\x1b[0m\r"
        self.assertEqual(composer.typed_text(screen(line)), "real")

    def test_dim_carries_over_a_line_break(self):
        self.assertEqual(
            composer.typed_text(
                screen("❯\xa0\x1b[2mghost\r", "  still ghost\x1b[0m\r")
            ),
            "",
        )

    def test_bash_mode_counts_as_typed(self):
        pink = "\x1b[0m\x1b[38;2;253;93;177m!\xa0\x1b[0m\r"
        self.assertEqual(composer.typed_text(screen(pink)), "!")

    def test_a_typed_rule_stays_inside_the_composer(self):
        typed = composer.typed_text(
            screen("❯\xa0above\r", "─" * 10 + "\r", "  below\r")
        )
        self.assertEqual(typed, "above\n" + "─" * 10 + "\nbelow")

    def test_transcript_prompt_does_not_count(self):
        above = "❯\xa0an earlier message\r"
        self.assertEqual(composer.typed_text(screen("❯\xa0\r", above=above)), "")

    def test_no_composer_is_none(self):
        self.assertIsNone(composer.typed_text("› Ask Codex to do anything\n"))
        self.assertIsNone(
            composer.typed_text(RULE + "\nDo you want to proceed?\n" + RULE)
        )


class MainTest(IsolatedTestCase):
    def run_main(self, stdout="", error=None):
        result = subprocess.CompletedProcess([], 0, stdout=stdout)
        with (
            patch.object(
                composer.subprocess, "run", side_effect=error, return_value=result
            ) as run,
            patch("builtins.print") as printed,
        ):
            code = composer.main(["composer-state.py", "w1:p2"])
        return code, printed.call_args_list[-1].args[0], run

    def test_empty_reads_the_visible_screen_with_ansi(self):
        code, line, run = self.run_main(screen("❯\xa0\x1b[2mghost\x1b[0m\r"))
        self.assertEqual((code, line), (0, "empty"))
        self.assertEqual(
            run.call_args.args[0][1:],
            ["pane", "read", "w1:p2", "--source", "visible", "--ansi"],
        )

    def test_typed_exits_one(self):
        code, line, _ = self.run_main(screen("❯\xa0hello\r"))
        self.assertEqual((code, line), (1, "typed: hello"))

    def test_no_composer_exits_two(self):
        self.assertEqual(self.run_main("plain shell\n")[:2], (2, "unknown"))

    def test_failed_read_exits_two(self):
        failure = subprocess.CalledProcessError(1, "herdr")
        self.assertEqual(self.run_main(error=failure)[:2], (2, "unknown"))
