import unittest

from test_support import IsolatedTestCase
import reminder_picker as picker


class PickerTest(IsolatedTestCase):
    def test_picks_map_to_fixed_durations(self):
        self.assertEqual([picker.remind_until(f"pick{n}", 1000) for n in range(1, 5)], [1900, 4600, 8200, 15400])

    def test_typed_values_need_minutes_or_hours(self):
        self.assertEqual(picker.remind_until(" 30M ", 1000), 2800)
        self.assertEqual(picker.remind_until("2h", 0), 7200)
        for bad in ("", "0m", "5", "5d", "12345m"):
            self.assertIsNone(picker.remind_until(bad, 1000))

    def test_entry_editing_ignores_control_characters(self):
        self.assertEqual(picker.entry_event("\r"), ("submit", None))
        self.assertEqual(picker.entry_event("\x7f"), ("backspace", None))
        self.assertIsNone(picker.entry_event("\x01"))
        self.assertEqual(picker.edit_entry("3", "char", "0"), "30")
        self.assertEqual(picker.edit_entry("30", "backspace", None), "3")

    def test_pick_lines_offer_every_choice_and_the_type_prompt_shows_the_entry(self):
        lines, spans = picker.picker_lines({"mode": "pick", "entry": ""}, 80)
        self.assertEqual([action for *_, action in spans], [action for _, action, _ in picker.PICKS])
        typed, spans = picker.picker_lines({"mode": "type", "entry": "15"}, 80)
        self.assertEqual(spans, [])
        self.assertIn("15", typed[0])


if __name__ == "__main__":
    unittest.main()
