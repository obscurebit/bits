import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_queue.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("prepare_queue_under_test", MODULE_PATH)
prepare_queue = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(prepare_queue)


class PrepareQueueTests(unittest.TestCase):
    def test_prepare_date_sets_reliable_story_defaults(self) -> None:
        target_date = date(2026, 4, 22)
        date_str = target_date.strftime("%Y-%m-%d")
        captured = {}

        def fake_run(cmd, env):
            captured["cmd"] = cmd
            captured["env"] = env.copy()
            entry_dir = Path(env[prepare_queue.OUTPUT_ROOT_ENV])
            story_dir = entry_dir / "docs" / "bits" / "posts"
            links_dir = entry_dir / "docs" / "links" / "posts"
            story_dir.mkdir(parents=True, exist_ok=True)
            links_dir.mkdir(parents=True, exist_ok=True)
            (story_dir / f"{date_str}-queued-story.md").write_text('theme: "municipal weirdness"\n')
            (links_dir / f"{date_str}-daily-links.md").write_text('theme: "municipal weirdness"\n')
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(prepare_queue.subprocess, "run", side_effect=fake_run):
                    result = prepare_queue.prepare_date(target_date, force=True)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(result, 0)
            self.assertEqual(captured["env"]["STORY_CANDIDATES"], "1")
            self.assertEqual(captured["env"]["STORY_MODEL_ROUTING"], "0")
            self.assertEqual(captured["env"]["OPENAI_REQUEST_TIMEOUT"], "90")
            self.assertEqual(captured["env"]["ALLOW_CROSS_THEME_CORPUS_LINKS"], "1")

            manifest = json.loads((root / "data" / "edition_queue" / date_str / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "prepared")
            self.assertEqual(manifest["theme"], "municipal weirdness")


if __name__ == "__main__":
    unittest.main()
