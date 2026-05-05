import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts" / "book_render.py"
SPEC = importlib.util.spec_from_file_location("book_render_under_test", MODULE_PATH)
book_render = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = book_render
SPEC.loader.exec_module(book_render)


class BookRenderTests(unittest.TestCase):
    def test_excerpt_skips_heading_and_truncates_words(self) -> None:
        text = "# Title\n\none two three four five"
        self.assertEqual(book_render.excerpt(text, 3), "one two three...")

    def test_css_contains_named_palette_colors(self) -> None:
        design = {
            "trim": {
                "width": "7in",
                "height": "10in",
                "margin_inner": "1in",
                "margin_outer": "1in",
                "margin_top": "1in",
                "margin_bottom": "1in",
            },
            "typography": {
                "display": "Georgia",
                "body": "Georgia",
                "sans": "Arial",
                "mono": "Menlo",
            },
            "palettes": {
                "light": {
                    "paper": "#fff",
                    "paper_alt": "#eee",
                    "ink": "#111",
                    "muted": "#555",
                    "rule": "#ccc",
                    "accent": "#f00",
                    "accent_2": "#0f0",
                    "accent_3": "#00f",
                    "plate_a": "#aaa",
                    "plate_b": "#bbb",
                    "plate_c": "#ccc",
                }
            },
        }
        css = book_render.css_for(design, "light")
        self.assertIn("#fff", css)
        self.assertIn("Georgia", css)


if __name__ == "__main__":
    unittest.main()
