import copy
import io
import json
import os
import tempfile
import traceback
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import main as crawler


SCHOLAR_ID = "mJhOACUAAAAJ"
PAYLOAD = {
    "author": {"name": "Bing-Bing Jiang", "interests": [{"title": "Learning"}]},
    "cited_by": {
        "table": [
            {"citations": {"all": "2,100", "since_2021": 1500}},
            {"h_index": {"all": 24, "since_2021": 21}},
            {"i10_index": {"all": 50, "since_2021": 40}},
        ]
    },
    "articles": [
        {
            "citation_id": f"{SCHOLAR_ID}:paper",
            "title": "Multi-view learning",
            "authors": "B Jiang",
            "year": "2026",
            "cited_by": {"total": 12, "link": "https://scholar.google.com/cites"},
        }
    ],
}


class CitationCrawlerTests(unittest.TestCase):
    def test_serpapi_metrics_and_publications_preserve_existing_entries(self):
        previous = {"citedby": 2046, "publications": {"old-paper": {"num_citations": 3}}}
        snapshot = copy.deepcopy(previous)
        author = crawler.parse_serpapi_author(PAYLOAD, SCHOLAR_ID, previous)
        self.assertEqual(author["citedby"], 2100)
        self.assertEqual(author["citedby5y"], 1500)
        self.assertEqual(author["hindex"], 24)
        self.assertEqual(author["hindex5y"], 21)
        self.assertEqual(author["interests"], ["Learning"])
        self.assertEqual(author["publications"][f"{SCHOLAR_ID}:paper"]["num_citations"], 12)
        self.assertIn("old-paper", author["publications"])
        self.assertEqual(previous, snapshot)

    def test_missing_optional_metrics(self):
        author = crawler.parse_serpapi_author(
            {"cited_by": {"table": [{"citations": {"all": 10}}]}}, SCHOLAR_ID, {}
        )
        self.assertEqual(author["citedby"], 10)
        self.assertEqual(author["publications"], {})

    def test_invalid_api_data_is_rejected(self):
        for payload in ([], {}, {"error": "API quota exceeded"}):
            with self.subTest(payload=payload):
                with self.assertRaises((RuntimeError, ValueError)):
                    crawler.parse_serpapi_author(payload, SCHOLAR_ID, {})
        for count in (True, -1, "unknown"):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    crawler.parse_serpapi_number(count, "citations")

    def test_direct_profile_parses_metrics_and_retains_old_papers(self):
        html = '''
        <div id="gsc_prf_in">Bing-Bing Jiang</div>
        <table id="gsc_rsb_st">
          <tr><td class="gsc_rsb_sc1">Citations</td>
            <td class="gsc_rsb_std">2,100</td><td class="gsc_rsb_std">1,500</td></tr>
        </table>
        <table><tr class="gsc_a_tr">
          <td><a class="gsc_a_at" href="/citations?citation_for_view=mJhOACUAAAAJ:paper">Paper</a>
            <div class="gs_gray">B Jiang</div><div class="gs_gray">Journal</div></td>
          <td><a class="gsc_a_ac" href="/cites">12</a></td>
          <td class="gsc_a_y"><span>2026</span></td>
        </tr></table>
        '''
        author = crawler.parse_profile(
            html, SCHOLAR_ID, "https://scholar.google.com", {"publications": {"old": {}}}
        )
        self.assertEqual(author["citedby"], 2100)
        self.assertEqual(author["citedby5y"], 1500)
        self.assertEqual(author["publications"][f"{SCHOLAR_ID}:paper"]["num_citations"], 12)
        self.assertIn("old", author["publications"])

    def test_blocked_or_incomplete_profiles_are_rejected(self):
        for html in ('<div id="gs_captcha_ccl"></div>', "automated queries", "Empty page"):
            with self.subTest(html=html):
                with self.assertRaises((RuntimeError, ValueError)):
                    crawler.parse_profile(html, SCHOLAR_ID, "https://scholar.google.com", {})

    def test_citation_drop_is_rejected(self):
        crawler.validate_citations(2040, {"citedby": 2046})
        with self.assertRaises(ValueError):
            crawler.validate_citations(0, {"citedby": 2046})

    def test_serpapi_request_uses_author_engine(self):
        response = Mock(status_code=200)
        response.json.return_value = PAYLOAD
        with patch.object(crawler.requests, "get", return_value=response) as request:
            author = crawler.fetch_author_via_serpapi(SCHOLAR_ID, "test-key", {})
        self.assertEqual(author["citedby"], 2100)
        self.assertEqual(request.call_args.kwargs["params"]["engine"], "google_scholar_author")
        self.assertEqual(request.call_args.kwargs["params"]["author_id"], SCHOLAR_ID)

    def test_network_failure_does_not_expose_key_in_traceback(self):
        secret = "private-test-key"
        error = requests.ConnectionError(f"https://serpapi.com/search?api_key={secret}")
        with patch.object(crawler.requests, "get", side_effect=error):
            try:
                crawler.fetch_author_via_serpapi(SCHOLAR_ID, secret, {})
            except RuntimeError:
                self.assertNotIn(secret, traceback.format_exc())
            else:
                self.fail("Network failure should raise RuntimeError")

    def test_api_http_and_json_errors_are_rejected(self):
        for response in (Mock(status_code=429), Mock(status_code=200)):
            response.json.side_effect = ValueError("Invalid JSON")
            with self.subTest(status=response.status_code):
                with patch.object(crawler.requests, "get", return_value=response):
                    with self.assertRaises(RuntimeError):
                        crawler.fetch_author_via_serpapi(SCHOLAR_ID, "test-key", {})

    def test_main_routes_requests_and_writes_compatible_json(self):
        for key in ("test-key", ""):
            with self.subTest(key=bool(key)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = {"SERPAPI_KEY": key, "GOOGLE_SCHOLAR_ID": f"https://scholar.google.com/citations?user={SCHOLAR_ID}&hl=en"}
                author = crawler.parse_serpapi_author(PAYLOAD, SCHOLAR_ID, {})
                with patch.dict(os.environ, env, clear=True), patch.object(crawler, "Path", side_effect=lambda p: root / p):
                    with patch.object(crawler, "fetch_author_via_serpapi", return_value=author) as api:
                        with patch.object(crawler, "fetch_author", return_value=author) as direct, redirect_stdout(io.StringIO()):
                            crawler.main()
                if key:
                    api.assert_called_once_with(SCHOLAR_ID, key, {})
                    direct.assert_not_called()
                else:
                    direct.assert_called_once_with(SCHOLAR_ID, {})
                    api.assert_not_called()
                profile = json.loads((root / "results/gs_data.json").read_text())
                badge = json.loads((root / "results/gs_data_shieldsio.json").read_text())
                self.assertEqual(profile["citedby"], 2100)
                self.assertIn("updated", profile)
                self.assertEqual(badge, {"schemaVersion": 1, "label": "citations", "message": "2100"})

    def test_failed_fetch_keeps_published_data_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "results"
            results.mkdir()
            published = results / "gs_data.json"
            published.write_text('{"citedby": 2046}')
            with patch.dict(os.environ, {"SERPAPI_KEY": "test-key"}, clear=True):
                with patch.object(crawler, "Path", side_effect=lambda p: root / p):
                    with patch.object(crawler, "fetch_author_via_serpapi", side_effect=RuntimeError("API error")) as api:
                        with self.assertRaises(RuntimeError):
                            crawler.main()
            api.assert_called_once_with(SCHOLAR_ID, "test-key", {})
            self.assertEqual(published.read_text(), '{"citedby": 2046}')


if __name__ == "__main__":
    unittest.main()
