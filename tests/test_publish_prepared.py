import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "publish_prepared.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("publish_prepared_under_test", MODULE_PATH)
publish_prepared = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(publish_prepared)


class PublishPreparedTests(unittest.TestCase):
    def test_copy_prepared_files_replaces_same_date_outputs(self) -> None:
        target_date = date(2026, 4, 20)
        date_str = target_date.strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue_story_dir = root / "data" / "edition_queue" / date_str / "docs" / "bits" / "posts"
            queue_links_dir = root / "data" / "edition_queue" / date_str / "docs" / "links" / "posts"
            published_story_dir = root / "docs" / "bits" / "posts"
            published_links_dir = root / "docs" / "links" / "posts"

            queue_story_dir.mkdir(parents=True, exist_ok=True)
            queue_links_dir.mkdir(parents=True, exist_ok=True)
            published_story_dir.mkdir(parents=True, exist_ok=True)
            published_links_dir.mkdir(parents=True, exist_ok=True)

            staged_story = queue_story_dir / f"{date_str}-fresh-story.md"
            staged_links = queue_links_dir / f"{date_str}-daily-links.md"
            old_story = published_story_dir / f"{date_str}-stale-story.md"
            old_links = published_links_dir / f"{date_str}-daily-links.md"

            staged_story.write_text("fresh story")
            staged_links.write_text("fresh links")
            old_story.write_text("stale story")
            old_links.write_text("stale links")

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                story_target, links_target = publish_prepared.copy_prepared_files(target_date)
                self.assertEqual(story_target.read_text(), "fresh story")
                self.assertEqual(links_target.read_text(), "fresh links")
                self.assertFalse(old_story.exists())
                self.assertEqual(story_target.name, staged_story.name)
                self.assertEqual(links_target.name, staged_links.name)
            finally:
                os.chdir(original_cwd)

    def test_copy_prepared_files_prepares_missing_queue_entry(self) -> None:
        target_date = date(2026, 4, 25)
        date_str = target_date.strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            def fake_prepare_date(prepared_date: date) -> int:
                self.assertEqual(prepared_date, target_date)
                queue_story_dir = root / "data" / "edition_queue" / date_str / "docs" / "bits" / "posts"
                queue_links_dir = root / "data" / "edition_queue" / date_str / "docs" / "links" / "posts"
                queue_story_dir.mkdir(parents=True, exist_ok=True)
                queue_links_dir.mkdir(parents=True, exist_ok=True)
                (queue_story_dir / f"{date_str}-rescued-story.md").write_text("rescued story")
                (queue_links_dir / f"{date_str}-daily-links.md").write_text("rescued links")
                return 0

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(publish_prepared, "prepare_date", side_effect=fake_prepare_date):
                    story_target, links_target = publish_prepared.copy_prepared_files(target_date)
                self.assertEqual(story_target.read_text(), "rescued story")
                self.assertEqual(links_target.read_text(), "rescued links")
            finally:
                os.chdir(original_cwd)

    def test_copy_prepared_files_reports_missing_parts_after_prepare_failure(self) -> None:
        target_date = date(2026, 4, 27)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(publish_prepared, "prepare_date", return_value=9):
                    with self.assertRaises(FileNotFoundError) as raised:
                        publish_prepared.copy_prepared_files(target_date)
                message = str(raised.exception)
                self.assertIn("missing: story, links", message)
                self.assertIn("exit code: 9", message)
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
