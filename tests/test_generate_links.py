import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_links.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("generate_links_under_test", MODULE_PATH)
generate_links = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(generate_links)


class GenerateLinksRedditDiscoveryTests(unittest.TestCase):
    def test_reddit_stays_disallowed_as_final_link_by_default(self) -> None:
        self.assertTrue(generate_links.is_disallowed_domain("reddit.com"))
        self.assertTrue(generate_links.is_disallowed_domain("old.reddit.com"))

    def test_reddit_final_links_can_be_explicitly_enabled(self) -> None:
        with mock.patch.object(generate_links, "ALLOW_REDDIT_FINAL_LINKS", True):
            self.assertFalse(generate_links.is_disallowed_domain("old.reddit.com"))

    def test_reddit_lane_query_renders_subreddit(self) -> None:
        query = generate_links.build_lane_query(
            'site:reddit.com/r/{subreddit} "{theme_name}" "{primary_term}"',
            {"name": "forgotten technology", "links": "obsolete computers, dead standards"},
            "reddit-discovery",
            subreddit="vintagecomputing",
        )

        self.assertEqual(query, 'site:reddit.com/r/vintagecomputing "forgotten technology" "forgotten"')

    def test_unwrap_reddit_outbound_url(self) -> None:
        wrapped = "https://old.reddit.com/out?url=https%3A%2F%2Fexample.com%2Fmanual.pdf"

        self.assertEqual(
            generate_links.unwrap_reddit_outbound_url(wrapped),
            "https://example.com/manual.pdf",
        )

    def test_dedupe_still_filters_reddit_threads_from_candidates(self) -> None:
        urls = [
            "https://old.reddit.com/r/vintagecomputing/comments/abc123/a_thread/",
            "https://example.com/odd-history",
        ]

        self.assertEqual(generate_links.dedupe_candidate_urls(urls), ["https://example.com/odd-history"])


if __name__ == "__main__":
    unittest.main()
