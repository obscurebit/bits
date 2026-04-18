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


if __name__ == "__main__":
    unittest.main()
