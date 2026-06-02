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
            "protagonist_archetype": "Service worker with informal knowledge that management undervalues",
            "setting_family": "Back-of-house workplace where unofficial rules matter more than posted rules",
            "object_family": "A mundane access object: card, key, badge, ticket, stamp, or wristband",
            "object_behavior": "It works correctly for the wrong person",
            "conflict_engine": "A humane exception threatens a system people depend on",
            "relationship_pressure": "Two coworkers need each other but disagree about what the job owes them",
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
        self.assertIn("Protagonist archetype: Service worker", prompt)
        self.assertIn("Setting family: Back-of-house workplace", prompt)
        self.assertIn("Object family: A mundane access object", prompt)
        self.assertIn("Object behavior: It works correctly for the wrong person", prompt)
        self.assertIn("Conflict engine: A humane exception", prompt)
        self.assertIn("Relationship pressure: Two coworkers", prompt)
        self.assertIn("substitute crossing guard", prompt)
        self.assertIn("cracked handheld stop sign", prompt)
        self.assertIn("Specifically avoid today: teacups, archives", prompt)

    def test_style_modifier_bank_has_sixty_four_voice_profiles(self) -> None:
        modifiers = generate_story.load_style_modifiers()

        self.assertEqual(len(modifiers["voice_profile"]), 64)
        self.assertTrue(all("write like" not in item.lower() for item in modifiers["voice_profile"]))

    def test_specific_premise_keys_are_replaced_by_composable_layers(self) -> None:
        modifiers = generate_story.load_style_modifiers()

        self.assertNotIn("setting", modifiers)
        self.assertNotIn("conflict", modifiers)
        self.assertNotIn("protagonist", modifiers)
        self.assertNotIn("anchor_object", modifiers)
        for key in [
            "setting_family",
            "setting_texture",
            "conflict_engine",
            "protagonist_archetype",
            "protagonist_pressure",
            "object_family",
            "object_behavior",
            "relationship_pressure",
        ]:
            self.assertGreaterEqual(len(modifiers[key]), 12)

    def test_model_routing_uses_composable_style_keys(self) -> None:
        model, reason = generate_story.select_story_model(
            {"name": "maintenance myths", "story": "repair cultures under pressure"},
            {
                "genre": "Quiet literary realism with one impossible pressure point",
                "setting_family": "Care institution where routine compassion collides with policy",
                "protagonist_archetype": "Caretaker responsible for both people and a failing system",
            },
        )

        self.assertIn("mistral-large", model)
        self.assertTrue(reason.startswith("grounded-human:"))


if __name__ == "__main__":
    unittest.main()
