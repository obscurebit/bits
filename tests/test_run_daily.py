import importlib.util
import json
import os
import sys
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_daily.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("run_daily_under_test", MODULE_PATH)
run_daily = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_daily)


class RunDailyFallbackTests(unittest.TestCase):
    def _completed(self, code: int) -> SimpleNamespace:
        return SimpleNamespace(returncode=code)

    def _run_in_temp_cwd(self, callback) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                callback(Path(tmpdir))
            finally:
                os.chdir(original_cwd)

    def test_auto_theme_falls_forward_for_links_and_reuses_successful_theme(self) -> None:
        first_theme = {"name": "counterfeit realities", "story": "s1", "links": "l1"}
        fallback_theme = {"name": "municipal weirdness", "story": "s2", "links": "l2"}
        executed = []

        def fake_run(command, env, timeout=None):
            script_name = Path(command[1]).name
            theme_name = json.loads(env["THEME_JSON"])["name"]
            executed.append((script_name, theme_name))
            if script_name == "generate_links.py" and theme_name == "counterfeit realities":
                return self._completed(1)
            return self._completed(0)

        args = SimpleNamespace(
            theme_json=None,
            date="2026-04-17",
            skip_story=False,
            skip_links=False,
            skip_landing=False,
        )

        def exercise(_root: Path) -> None:
            with mock.patch.object(run_daily, "parse_args", return_value=args), \
                 mock.patch.object(run_daily, "load_theme_override", return_value=None), \
                 mock.patch.object(run_daily, "build_theme_candidates", return_value=[
                     (first_theme, "rotating theme for 2026-04-17"),
                     (fallback_theme, "rotating fallback +1 for 2026-04-17"),
                 ]), \
                 mock.patch.object(run_daily.subprocess, "run", side_effect=fake_run):
                run_daily.main()

        self._run_in_temp_cwd(exercise)

        self.assertEqual(
            executed,
            [
                ("generate_links.py", "counterfeit realities"),
                ("generate_links.py", "municipal weirdness"),
                ("generate_story.py", "municipal weirdness"),
                ("update_landing.py", "municipal weirdness"),
            ],
        )

    def test_explicit_theme_does_not_fallback(self) -> None:
        explicit_theme = {"name": "counterfeit realities", "story": "s1", "links": "l1"}
        executed = []

        def fake_run(command, env, timeout=None):
            executed.append((Path(command[1]).name, json.loads(env["THEME_JSON"])["name"]))
            return self._completed(7)

        args = SimpleNamespace(
            theme_json='{"name":"counterfeit realities"}',
            date="2026-04-17",
            skip_story=False,
            skip_links=False,
            skip_landing=False,
        )

        def exercise(_root: Path) -> None:
            with mock.patch.object(run_daily, "parse_args", return_value=args), \
                 mock.patch.object(run_daily, "load_theme_override", return_value=explicit_theme), \
                 mock.patch.object(run_daily.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(SystemExit) as raised:
                    run_daily.main()
            self.assertEqual(raised.exception.code, 7)

        self._run_in_temp_cwd(exercise)
        self.assertEqual(executed, [("generate_links.py", "counterfeit realities")])

    def test_empty_links_fallback_allows_story_to_continue(self) -> None:
        explicit_theme = {"name": "parallel dimensions", "story": "s1", "links": "l1"}
        executed = []

        def fake_run(command, env, timeout=None):
            script_name = Path(command[1]).name
            executed.append(script_name)
            if script_name == "generate_links.py":
                return self._completed(7)
            return self._completed(0)

        args = SimpleNamespace(
            theme_json='{"name":"parallel dimensions"}',
            date="2026-05-17",
            skip_story=False,
            skip_links=False,
            skip_landing=False,
        )

        def exercise(root: Path) -> None:
            with mock.patch.object(run_daily, "parse_args", return_value=args), \
                 mock.patch.object(run_daily, "load_theme_override", return_value=explicit_theme), \
                 mock.patch.object(run_daily, "ALLOW_EMPTY_LINKS", True), \
                 mock.patch.object(run_daily.subprocess, "run", side_effect=fake_run):
                run_daily.main()

            links_path = root / "docs" / "links" / "posts" / "2026-05-17-daily-links.md"
            context_path = root / "data" / "discovery" / "story_context" / "2026-05-17-links.json"
            self.assertTrue(links_path.exists())
            self.assertTrue(context_path.exists())
            self.assertIn("intentionally empty", links_path.read_text())

        self._run_in_temp_cwd(exercise)
        self.assertEqual(executed, ["generate_links.py", "generate_story.py", "update_landing.py"])

    def test_fallback_story_allows_landing_to_continue(self) -> None:
        explicit_theme = {"name": "parallel dimensions", "story": "s1", "links": "l1"}
        executed = []

        def fake_run(command, env, timeout=None):
            script_name = Path(command[1]).name
            executed.append(script_name)
            if script_name == "generate_story.py":
                return self._completed(124)
            return self._completed(0)

        args = SimpleNamespace(
            theme_json='{"name":"parallel dimensions"}',
            date="2026-05-17",
            skip_story=False,
            skip_links=False,
            skip_landing=False,
        )

        def exercise(root: Path) -> None:
            with mock.patch.object(run_daily, "parse_args", return_value=args), \
                 mock.patch.object(run_daily, "load_theme_override", return_value=explicit_theme), \
                 mock.patch.object(run_daily, "ALLOW_FALLBACK_STORY", True), \
                 mock.patch.object(run_daily.subprocess, "run", side_effect=fake_run):
                run_daily.main()

            story_files = list((root / "docs" / "bits" / "posts").glob("2026-05-17-*.md"))
            self.assertEqual(len(story_files), 1)
            story_text = story_files[0].read_text()
            self.assertIn('author: "fallback-local"', story_text)
            self.assertIn("The Spare Edition", story_text)

        self._run_in_temp_cwd(exercise)
        self.assertEqual(executed, ["generate_links.py", "generate_story.py", "update_landing.py"])

    def test_existing_links_skip_link_generation(self) -> None:
        fallback_theme = {"name": "municipal weirdness", "story": "s2", "links": "l2"}
        executed = []

        def fake_run(command, env, timeout=None):
            executed.append((Path(command[1]).name, json.loads(env["THEME_JSON"])["name"], timeout))
            return self._completed(0)

        args = SimpleNamespace(
            theme_json=None,
            date="2026-04-16",
            skip_story=False,
            skip_links=False,
            skip_landing=False,
        )

        def exercise(root: Path) -> None:
            links_dir = root / "docs" / "links" / "posts"
            links_dir.mkdir(parents=True, exist_ok=True)
            (links_dir / "2026-04-16-daily-links.md").write_text("stub")

            with mock.patch.object(run_daily, "parse_args", return_value=args), \
                 mock.patch.object(run_daily, "load_theme_override", return_value=None), \
                 mock.patch.object(run_daily, "build_theme_candidates", return_value=[
                     (fallback_theme, "rotating fallback +1 for 2026-04-16"),
                 ]), \
                 mock.patch.object(run_daily.subprocess, "run", side_effect=fake_run):
                run_daily.main()

        self._run_in_temp_cwd(exercise)

        self.assertEqual(
            executed,
            [
                ("generate_story.py", "municipal weirdness", run_daily.STORY_STEP_TIMEOUT_SECONDS),
                ("update_landing.py", "municipal weirdness", run_daily.LANDING_STEP_TIMEOUT_SECONDS),
            ],
        )

    def test_parse_ai_theme_response_accepts_json_array(self) -> None:
        content = json.dumps([
            {
                "name": "lost maintenance rooms",
                "story": "service workers discover that a locked utility room has been recording favors, debts, and repairs no resident remembers requesting",
                "links": "utility rooms, building maintenance logs, local infrastructure archives, janitor manuals",
            }
        ])

        themes = run_daily.parse_ai_theme_response(content, limit=2)

        self.assertEqual(themes[0]["name"], "lost maintenance rooms")
        self.assertIn("maintenance logs", themes[0]["links"])

    def test_ai_theme_candidates_are_inserted_before_rotating_fallbacks(self) -> None:
        base = ({"name": "parallel dimensions", "story": "s1", "links": "l1"}, "rotating theme")
        rotating = ({"name": "edge of maps", "story": "s2", "links": "l2"}, "rotating fallback")
        ai = ({"name": "lost maintenance rooms", "story": "s3", "links": "l3"}, "AI fallback")

        with mock.patch.object(run_daily, "AI_THEME_FALLBACKS", 1), \
             mock.patch.object(run_daily, "generate_ai_theme_candidates", return_value=[ai]):
            enriched = run_daily.enrich_theme_candidates_with_ai([base, rotating], run_daily.resolve_target_date("2026-05-17"))

        self.assertEqual([theme["name"] for theme, _label in enriched], [
            "parallel dimensions",
            "lost maintenance rooms",
            "edge of maps",
        ])

    def test_load_themes_normalizes_yaml_date_override_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            themes_path = Path(tmpdir) / "themes.yaml"
            themes_path.write_text(
                """
themes:
  - name: "rotating"
    story: "s"
    links: "l"
overrides:
  2026-12-25:
    name: "gift economies"
    story: "holiday story"
    links: "holiday links"
"""
            )

            with mock.patch.object(run_daily, "THEMES_FILE", themes_path):
                config = run_daily.load_themes()

        self.assertIn("2026-12-25", config["overrides"])
        self.assertEqual(config["overrides"]["2026-12-25"]["name"], "gift economies")


if __name__ == "__main__":
    unittest.main()
