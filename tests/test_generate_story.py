import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_story.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("generate_story_under_test", MODULE_PATH)
generate_story = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(generate_story)


class GenerateStoryVarietyTests(unittest.TestCase):
    def test_parse_ai_variety_response_extracts_wrapped_json(self) -> None:
        response = """
        Sure:
        {
          "person": "a night-shift permit clerk who remembers every rejected form",
          "place": "the folding-table vestibule of a county annex",
          "object": "a laminated queue ticket with the ink rubbed away",
          "social_texture": "everyone is pretending the new rule is temporary",
          "formal_twist": "let one repeated bureaucratic phrase change meaning",
          "avoid": ["teacups", "archives", "mysterious signals"]
        }
        """

        parsed = generate_story.parse_ai_variety_response(response)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["object"], "a laminated queue ticket with the ink rubbed away")
        self.assertEqual(parsed["avoid"], ["teacups", "archives", "mysterious signals"])

    def test_parse_ai_variety_response_requires_core_specificity(self) -> None:
        response = '{"person": "a clerk", "place": "a lobby", "avoid": ["teacups"]}'

        self.assertIsNone(generate_story.parse_ai_variety_response(response))

    def test_build_story_prompt_includes_ai_variety_brief(self) -> None:
        style = {
            "genre": "domestic infrastructure fiction",
            "protagonist": "a building superintendent",
            "anchor_object": "an obsolete keycard",
            "voice_profile": "Blue-collar systems thinking: pipes, carts, tickets, locks, machines, and workarounds reveal the world",
        }
        variety = {
            "person": "a substitute crossing guard with two unpaid parking tickets",
            "place": "a school loading zone repainted during the night",
            "object": "a cracked handheld stop sign with a city inventory sticker",
            "social_texture": "parents rank each other by who knows the traffic officer",
            "formal_twist": "use three brief incident-log timestamps",
            "avoid": ["teacups", "archives"],
        }

        prompt, genre = generate_story.build_story_prompt(
            {"name": "municipal weirdness", "story": "small public systems behaving badly"},
            style,
            ai_variety=variety,
        )

        self.assertEqual(genre, "domestic infrastructure fiction")
        self.assertIn("AI VARIETY BRIEF", prompt)
        self.assertIn("Voice profile: Blue-collar systems thinking", prompt)
        self.assertIn("substitute crossing guard", prompt)
        self.assertIn("cracked handheld stop sign", prompt)
        self.assertIn("Specifically avoid today: teacups, archives", prompt)

    def test_style_modifier_bank_has_sixty_four_voice_profiles(self) -> None:
        modifiers = generate_story.load_style_modifiers()

        self.assertEqual(len(modifiers["voice_profile"]), 64)
        self.assertTrue(all("write like" not in item.lower() for item in modifiers["voice_profile"]))


if __name__ == "__main__":
    unittest.main()
