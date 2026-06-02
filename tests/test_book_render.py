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

    def test_render_story_body_preserves_blocks_and_inline_emphasis(self) -> None:
        text = "# Title\n\nFirst *line* here.\n\nSecond **line** here."
        html = book_render.render_story_body(text, 12)
        self.assertIn("<p>First <em>line</em> here.</p>", html)
        self.assertIn("<p>Second <strong>line</strong> here.</p>", html)

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
        self.assertIn("art-layout-continuation-strip", css)
        self.assertIn("art-layout-receipt-strip", css)

    def test_print_fit_script_measures_entry_body_overflow(self) -> None:
        script = book_render.print_fit_script()

        self.assertIn('querySelectorAll(".entry")', script)
        self.assertIn('querySelector(".entry-body")', script)
        self.assertIn("scrollHeight > body.clientHeight", script)
        self.assertIn("fit-overflow-risk", script)

    def test_pdf_profile_css_keeps_review_unchanged(self) -> None:
        self.assertEqual(book_render.pdf_profile_css("review"), "")

    def test_pdf_profile_css_strips_expensive_print_effects(self) -> None:
        css = book_render.pdf_profile_css("download")

        self.assertIn("mix-blend-mode: normal", css)
        self.assertIn("backdrop-filter: none", css)
        self.assertIn("mask-image: none", css)
        self.assertIn(".pdf-profile-download .plate.has-art::after", css)
        self.assertIn(".pdf-profile-download .page", css)
        self.assertIn("background: var(--paper)", css)

    def test_download_profile_uses_smaller_images(self) -> None:
        profile = book_render.PDF_IMAGE_PROFILES["download"]

        self.assertEqual(profile["quality"], 45)
        self.assertEqual(profile["max_long_edge"], 700)

    def test_spread_split_keeps_both_pages_populated(self) -> None:
        text = "\n\n".join(
            [
                " ".join(["alpha"] * 180),
                " ".join(["bravo"] * 160),
                " ".join(["charlie"] * 180),
            ]
        )

        first, second = book_render.split_story_for_spread(text, 520)

        self.assertGreater(sum(len(block.split()) for block in first), 0)
        self.assertGreater(sum(len(block.split()) for block in second), 0)
        self.assertLess(sum(len(block.split()) for block in first), 520)

    def test_spread_split_preserves_story_order_after_break(self) -> None:
        text = "\n\n".join(
            [
                " ".join(["one"] * 80),
                " ".join(["two"] * 80),
                " ".join(["three"] * 80),
                "four",
                "five",
            ]
        )

        first, second = book_render.split_story_for_spread(text, 242, first_page_ratio=0.5)

        self.assertEqual(first, [" ".join(["one"] * 80), " ".join(["two"] * 80)])
        self.assertEqual(second[-2:], ["four", "five"])

    def test_spread_selection_promotes_long_story_before_threshold(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="sample",
            date="2026-01-01",
            title="Sample",
            description="",
            theme="signal",
            body=" ".join(["word"] * 701),
        )
        entry = book_render.book_build.BookEntry(
            byte_index="00",
            section_code="0",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="signal",
            validation_notes=[],
        )

        self.assertTrue(book_render.is_spread_entry(entry, {"layout": {"two_page_spread_min_words": 700}}))
        self.assertTrue(book_render.is_spread_entry(entry, {"layout": {"two_page_spread_min_words": 800}}))

    def test_medium_story_can_be_forced_into_spread_before_threshold(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="sample",
            date="2026-01-01",
            title="Sample",
            description="",
            theme="archive",
            body=" ".join(["word"] * 527),
        )
        entry = book_render.book_build.BookEntry(
            byte_index="22",
            section_code="2",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="archive",
            validation_notes=[],
        )
        design = {
            "layout": {
                "single_page_full_words": 360,
                "two_page_spread_min_words": 700,
                "two_page_spread_entries": ["22"],
            }
        }

        self.assertLess(book_render.entry_excerpt_word_limit(entry, design), 527)
        self.assertTrue(book_render.is_excerpted(entry, design))
        self.assertTrue(book_render.is_spread_entry(entry, design))

    def test_layout_override_adds_variant_class(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="love-letters-from-the-router",
            date="2026-01-01",
            title="Sample",
            description="",
            theme="signal",
            body=" ".join(["word"] * 40),
        )
        entry = book_render.book_build.BookEntry(
            byte_index="04",
            section_code="0",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="signal",
            validation_notes=[],
        )

        classes = book_render.entry_classes(entry, {"layout": {"layout_overrides": {"04": "signal-broadside"}}})

        self.assertIn("variant-signal-broadside", classes)

    def test_plate_identity_uses_handpicked_variant(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="frostbite-protocol",
            date="2026-01-01",
            title="Frostbite Protocol",
            description="",
            theme="cold",
            body="word",
        )
        entry = book_render.book_build.BookEntry(
            byte_index="01",
            section_code="0",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="protocol",
            validation_notes=[],
        )
        design = {"layout": {"layout_overrides": {"01": "frostbite-core"}}}

        plate = book_render.plate_html(entry, design)

        self.assertIn(">ICE<", plate)
        self.assertIn("Cryo Core Plate", plate)

    def test_entry_footer_renders_real_qr_link(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="2026-05-27-the-leverage-of-frayed-plastic",
            date="2026-05-27",
            title="The Leverage of Frayed Plastic",
            description="",
            theme="quantum mysteries",
            body="word",
        )
        target = "https://bits.obscurebit.com/bits/posts/2026-05-27-the-leverage-of-frayed-plastic/"
        entry = book_render.book_build.BookEntry(
            byte_index="B6",
            section_code="B",
            bit=bit,
            qr_target=target,
            art_status="draft",
            art_lane="auto",
            layout_mode="glitch",
            validation_notes=[],
        )

        footer = book_render.entry_foot_html(entry, 235)

        self.assertIn(f'href="{target}"', footer)
        self.assertIn('class="qr-code"', footer)
        self.assertIn('class="qr-modules"', footer)
        self.assertIn('viewBox="0 0 45 45"', footer)
        self.assertIn('bits.obscurebit.com / bit B6', footer)
        self.assertNotIn("<i>QR</i>", footer)

    def test_visible_art_labels_hide_internal_review_status(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="sample",
            date="2026-01-01",
            title="Sample",
            description="",
            theme="signal",
            body="word",
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="https://bits.obscurebit.com/bits/posts/sample/",
            art_status="needs_human_review",
            art_lane="auto_draft",
            layout_mode="signal",
            validation_notes=[],
        )

        visible_html = book_render.entry_foot_html(entry, 1) + book_render.plate_html(entry, {})

        self.assertNotIn("needs_human_review", visible_html)
        self.assertNotIn("human review", visible_html.lower())
        self.assertNotIn("Auto Draft", visible_html)
        self.assertIn("Signal Plate", visible_html)

    def test_plate_identity_accepts_multiple_art_titles(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="sample",
            date="2026-01-01",
            title="Sample",
            description="",
            theme="signal",
            body="word",
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="signal",
            validation_notes=[],
        )
        design = {"layout": {"layout_overrides": {"AA": {"variant": "static-tower", "art_titles": ["Signal", "Tower"]}}}}

        self.assertIn("Signal / Tower Plate", book_render.plate_html(entry, design))

    def test_layout_override_can_hide_teaser_and_set_type(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="sample",
            date="2026-01-01",
            title="Sample",
            description="",
            theme="signal",
            body="word",
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="signal",
            validation_notes=[],
        )
        design = {
            "layout": {
                "layout_overrides": {
                    "AA": {
                        "variant": "static-tower",
                        "teaser": False,
                        "body_size": "9px",
                        "body_font": "Test Mono",
                    }
                }
            }
        }

        self.assertFalse(book_render.teaser_enabled(entry, design))
        self.assertIn("no-teaser", book_render.entry_classes(entry, design))
        self.assertIn("--story-body-size: 9px", book_render.section_open_tag(entry, design))
        self.assertIn("--story-body-font: Test Mono", book_render.section_open_tag(entry, design))

    def test_spread_entries_hide_teaser_by_default_but_can_opt_in(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="sample",
            date="2026-01-01",
            title="Sample",
            description="",
            theme="signal",
            body=" ".join(["word"] * 520),
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="signal",
            validation_notes=[],
        )
        design = {"layout": {"single_page_full_words": 360}}

        self.assertFalse(book_render.teaser_enabled(entry, design))
        self.assertIn("no-teaser", book_render.entry_classes(entry, design))

        opt_in = {"layout": {"single_page_full_words": 360, "layout_overrides": {"AA": {"teaser": True}}}}
        self.assertTrue(book_render.teaser_enabled(entry, opt_in))
        self.assertNotIn("no-teaser", book_render.entry_classes(entry, opt_in))

    def test_sectioned_story_uses_own_single_page_limit(self) -> None:
        text = "\n\n---\n\n".join([" ".join(["word"] * 120)] * 3)
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="sectioned",
            date="2026-01-01",
            title="Sectioned",
            description="",
            theme="protocol",
            body=text,
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="protocol",
            validation_notes=[],
        )
        design = {"layout": {"single_page_full_words": 520, "sectioned_single_page_full_words": 340}}

        self.assertTrue(book_render.is_spread_entry(entry, design))

    def test_spread_selection_uses_mode_specific_single_page_limit(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="field-note",
            date="2026-01-01",
            title="Field Note",
            description="",
            theme="field",
            body=" ".join(["word"] * 380),
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="field_note",
            validation_notes=[],
        )
        design = {"layout": {"single_page_full_words": 520, "single_page_full_words_by_mode": {"field_note": 360}}}

        self.assertTrue(book_render.is_spread_entry(entry, design))

    def test_layout_override_can_set_story_page_budgets(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="budgeted",
            date="2026-01-01",
            title="Budgeted",
            description="",
            theme="signal",
            body=" ".join(["word"] * 540),
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="signal",
            validation_notes=[],
        )
        design = {"layout": {"layout_overrides": {"AA": {"first_page_words": 300, "continuation_words": 400}}}}

        self.assertEqual(book_render.story_page_budgets(entry, design), (300, 400))

    def test_layout_override_can_force_single_page(self) -> None:
        bit = book_render.book_build.BitPost(
            path=Path("sample.md"),
            slug="single",
            date="2026-01-01",
            title="Single",
            description="",
            theme="field",
            body=" ".join(["word"] * 480),
        )
        entry = book_render.book_build.BookEntry(
            byte_index="AA",
            section_code="A",
            bit=bit,
            qr_target="",
            art_status="missing",
            art_lane="auto",
            layout_mode="field_note",
            validation_notes=[],
        )
        design = {"layout": {"single_page_full_words_by_mode": {"field_note": 360}, "layout_overrides": {"AA": {"force_single_page": True}}}}

        self.assertFalse(book_render.is_spread_entry(entry, design))

    def test_divider_blocks_render_as_rules(self) -> None:
        blocks = book_render.story_blocks("First\n\n---\n\nSecond", 20, include_dividers=True)

        self.assertEqual(blocks, ["First", book_render.STORY_DIVIDER, "Second"])
        self.assertIn('class="story-divider"', book_render.render_story_blocks(blocks))

    def test_generation_artifact_blocks_after_divider_are_omitted(self) -> None:
        text = "Story ending.\n\n---\n\n**Note**: This explains the prompt.\n\nNot story."

        self.assertEqual(book_render.story_blocks(text, 50, include_dividers=True), ["Story ending."])
        self.assertFalse(book_render.has_story_dividers(text))

    def test_generation_artifact_labels_are_filtered(self) -> None:
        text = "\n\n".join(
            [
                "Story ending.",
                "---",
                "**Strange image**: a prompt note",
                "**Unpredicted sentence**: another prompt note",
                "[**Final Annotation:** not part of the story]",
            ]
        )

        self.assertEqual(book_render.story_blocks(text, 50, include_dividers=True), ["Story ending."])

    def test_pull_quote_does_not_stop_at_title_abbreviation(self) -> None:
        text = "The ice crackled like static as Dr. Ewa Nowak scraped her scalpel against the subject’s femur."

        quote = book_render.pull_quote(text)

        self.assertIn("Dr. Ewa Nowak", quote)
        self.assertNotEqual(quote, "The ice crackled like static as Dr.")

    def test_short_tail_pages_are_merged_when_continuation_can_absorb_them(self) -> None:
        pages = [["one " * 10], ["two " * 350], ["tail " * 17]]

        balanced = book_render.rebalance_short_tail_pages(pages, 240, 360)

        self.assertEqual(len(balanced), 2)
        self.assertGreater(book_render.page_word_count(balanced[-1]), 360)

    def test_short_two_page_tail_is_rebalanced_from_previous_page(self) -> None:
        pages = [["one " * 200, "two " * 80, "three " * 20], ["tail " * 60]]

        balanced = book_render.rebalance_short_tail_pages(pages, 300, 620)

        self.assertEqual(len(balanced), 2)
        self.assertGreaterEqual(book_render.page_word_count(balanced[-1]), 120)
        self.assertLess(book_render.page_word_count(balanced[0]), 300)


if __name__ == "__main__":
    unittest.main()
