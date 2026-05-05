import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "book_build.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("book_build_under_test", MODULE_PATH)
book_build = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = book_build
SPEC.loader.exec_module(book_build)


class BookBuildTests(unittest.TestCase):
    def test_build_book_writes_draft_artifacts_and_reports_incomplete_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            volume_dir = root / "book" / "volume-1"
            posts_dir = root / "docs" / "bits" / "posts"
            output_dir = root / "book-output" / "volume-1"
            volume_dir.mkdir(parents=True)
            posts_dir.mkdir(parents=True)

            (volume_dir / "manifest.yaml").write_text(
                """
volume: 1
title: "256 Bits"
target_entry_count: 2
canonical_url_base: "https://example.test/bits/posts"
release_outputs:
  manuscript: "book-output/volume-1/book.md"
  validation_report: "book-output/volume-1/report.md"
  source_manifest: "book-output/volume-1/source.json"
  art_briefs: "book-output/volume-1/art.yaml"
  qr_targets: "book-output/volume-1/qr.csv"
  gumroad_readme: "book-output/volume-1/gumroad/README.txt"
selected_entries: []
"""
            )
            (volume_dir / "art_manifest.yaml").write_text("entries: []\n")
            (posts_dir / "2026-01-30-first.md").write_text(
                """---
date: 2026-01-30
title: "First"
theme: "signals"
---

# First

This is a test bit with enough words to avoid the short-entry warning. It has an original body, a small speculative setup, and a quiet ending for the book builder to ingest during tests.
"""
            )

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                entries, warnings = book_build.build_book(volume_dir, output_dir)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(len(entries), 1)
            self.assertIn("Volume has 1 selected entries; target is 2.", warnings)
            self.assertTrue((output_dir / "book.md").exists())
            self.assertTrue((output_dir / "report.md").exists())
            self.assertTrue((output_dir / "source.json").exists())
            self.assertTrue((output_dir / "art.yaml").exists())
            self.assertTrue((output_dir / "qr.csv").exists())
            self.assertTrue((output_dir / "gumroad" / "README.txt").exists())

    def test_byte_indexes_are_hexadecimal(self) -> None:
        posts = [
            book_build.BitPost(
                path=Path(f"docs/bits/posts/2026-01-{index + 1:02d}-slug-{index}.md"),
                slug=f"slug-{index}",
                date=f"2026-01-{index + 1:02d}",
                title=f"Title {index}",
                description="",
                theme="",
                body="word " * 120,
            )
            for index in range(17)
        ]
        manifest = {"target_entry_count": 17, "canonical_url_base": "https://example.test"}
        entries, warnings = book_build.build_entries(manifest, posts, {})
        self.assertEqual(entries[0].byte_index, "00")
        self.assertEqual(entries[15].byte_index, "0F")
        self.assertEqual(entries[16].byte_index, "10")
        self.assertEqual(entries[16].layout_mode, "archive")
        self.assertEqual(warnings, [])

    def test_clean_title_and_strip_site_chrome_prepare_book_text(self) -> None:
        self.assertEqual(book_build.clean_title("**Basement Lattice**"), "Basement Lattice")
        self.assertEqual(
            book_build.strip_site_chrome("Story paragraph.\n\n<div style=\"display: flex;\">nav</div>"),
            "Story paragraph.",
        )

    def test_extract_generation_ref_from_story_footer(self) -> None:
        body = """
Story paragraph.

<div style="display: flex;">
  <a href="https://github.com/obscurebit/b1ts/tree/f8f1a62" class="story-gen-link">
    gen:f8f1a62
  </a>
</div>
"""
        self.assertEqual(
            book_build.extract_generation_ref(body),
            ("f8f1a62", "https://github.com/obscurebit/b1ts/tree/f8f1a62"),
        )

    def test_name_collisions_flag_repeated_full_names_and_warn_on_single_names(self) -> None:
        posts = [
            book_build.BitPost(
                path=Path("one.md"),
                slug="one",
                date="2026-01-01",
                title="One",
                description="",
                theme="",
                body="Mara Vale opened the drawer, and Piotr waited.",
            ),
            book_build.BitPost(
                path=Path("two.md"),
                slug="two",
                date="2026-01-02",
                title="Two",
                description="",
                theme="",
                body="Mara Vale signed the form while Piotr listened.",
            ),
            book_build.BitPost(
                path=Path("three.md"),
                slug="three",
                date="2026-01-03",
                title="Three",
                description="",
                theme="",
                body="The receipt belonged to Piotr.",
            ),
        ]
        entries, _warnings = book_build.build_entries({"target_entry_count": 3}, posts, {})
        blockers, warnings = book_build.validate_name_collisions(entries, {})
        self.assertTrue(any("Mara Vale" in blocker for blocker in blockers))
        self.assertTrue(any("Piotr" in warning for warning in warnings))

    def test_infer_layout_mode_uses_story_signals(self) -> None:
        post = book_build.BitPost(
            path=Path("signal.md"),
            slug="signal",
            date="2026-01-01",
            title="Router Static",
            description="",
            theme="signal from nowhere",
            body="The radio transmission arrived through the router.",
        )
        self.assertEqual(book_build.infer_layout_mode(post), "signal")


if __name__ == "__main__":
    unittest.main()
