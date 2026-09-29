#!/usr/bin/env python3
"""Offline regression checks for state handling, retries, exit codes and multi-seed merging."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import fetch  # noqa: E402


def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPTS / "fetch.py"), *args], capture_output=True, text=True, cwd=cwd)


class FetchRegressionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("config.read_dotenv", return_value={}).start()
        patch.dict(os.environ, {"OPENALEX_API_KEY": ""}).start()

    def run_main(self, *args):
        with patch.object(sys, "argv", ["fetch.py", *args]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                fetch.main()
        return output.getvalue()

    def test_seen_is_idempotent_and_feedback_is_history(self):
        with tempfile.TemporaryDirectory() as directory:
            seen = str(Path(directory) / "seen.json")
            self.run_main("seen", "init", seen)
            self.run_main("seen", "add", seen, "W1", "A paper")
            self.run_main("seen", "add", seen, "W1", "A paper")
            self.run_main("seen", "feedback", seen, "W1", "up")
            self.run_main("seen", "feedback", seen, "W1", "down")
            state = json.loads(Path(seen).read_text())
            self.assertEqual(len(state), 1)
            self.assertEqual([item["value"] for item in state[0]["feedback"]], ["up", "down"])

    def test_candidates_and_seen_deduplicate_normalized_dois(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            candidates = directory / "candidates.json"
            seen = str(directory / "seen.json")
            fetch.save([
                {"id": "W1", "doi": "10.1/example"},
                {"id": "W2", "doi": "https://doi.org/10.1/EXAMPLE"},
            ], str(candidates))
            self.assertEqual(len(json.loads(candidates.read_text())), 1)
            self.run_main("seen", "init", seen)
            self.run_main("seen", "add", seen, "W1", "A paper", "--doi", "10.1/example")
            self.run_main("seen", "add", seen, "W2", "The same paper", "--doi", "https://doi.org/10.1/EXAMPLE")
            self.assertEqual(len(json.loads(Path(seen).read_text())), 1)

    def test_archive_only_removes_eligible_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            candidates = directory / "candidates.json"
            seen = directory / "seen.json"
            fetch.write_json([{"id": "W-seen"}], str(seen))
            fetch.write_json([
                {"id": "W-seen", "year": 2024, "channel": "cites:W0"},
                {"id": "W-old", "year": 2010, "channel": "search:test"},
                {"id": "W-keep", "year": 2024, "channel": "refs"},
            ], str(candidates))
            fetch.archive_pool(str(candidates), maximum=2)
            active = json.loads(candidates.read_text())
            archived = list((directory / "archive").glob("candidates-*.json"))
            self.assertEqual({item["id"] for item in active}, {"W-old", "W-keep"})
            self.assertEqual(len(archived), 1)
            self.assertEqual(json.loads(archived[0].read_text())["records"][0]["id"], "W-seen")

    def test_bibtex_keys_are_unique_and_values_are_escaped(self):
        work = {
            "id": "https://openalex.org/W1",
            "display_name": "A {test}",
            "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
            "publication_year": 2026,
            "doi": "https://doi.org/10.1/x",
            "primary_location": {},
            "best_oa_location": {},
            "abstract_inverted_index": {},
            "type": "preprint",
        }
        keys = set()
        first = fetch.bibtex(work, used_keys=keys)
        second = fetch.bibtex(work, used_keys=keys)
        self.assertIn("@misc{Lovelace2026,", first)
        self.assertIn("@misc{Lovelace2026a,", second)
        self.assertIn(r"A \{test\}", first)

    def test_s2_merges_all_positive_seed_results(self):
        def fake_get(url, headers=None):
            title = "first" if "/p1?" in url else "second"
            return {"recommendedPapers": [{
                "paperId": title,
                "title": title,
                "year": 2026,
                "authors": [],
                "externalIds": {},
            }]}

        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "test-key"}, clear=False), patch.object(fetch, "get", fake_get):
            output = self.run_main("s2", "--pos", "p1,p2", "--limit", "6", "--noabstract")
        self.assertIn("title: first", output)
        self.assertIn("title: second", output)

    @unittest.skipIf(fetch.fcntl is None, "fcntl unavailable on this platform")
    def test_concurrent_savers_do_not_lose_records(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = Path(directory) / "candidates.json"
            code = ("import sys, io, contextlib; sys.path.insert(0, sys.argv[1]); import fetch\n"
                    "for i in range(40):\n"
                    "    with contextlib.redirect_stdout(io.StringIO()):\n"
                    "        fetch.save([{'id': sys.argv[2] + str(i), 'doi': ''}], sys.argv[3])\n")
            workers = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), prefix, str(pool)]) for prefix in "ABC"]
            for worker in workers:
                self.assertEqual(worker.wait(), 0)
            self.assertEqual(len(json.loads(pool.read_text())), 120)

    def test_seed_identifiers_block_s2_and_doi_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "seeds.txt").write_text(
                "# Transformer\nW2626778328 10.48550/arXiv.1706.03762 arXiv:1706.03762  # 同一粒种子\nW111\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                fetch.save([
                    {"id": "W2626778328", "doi": ""},
                    {"id": "arXiv:1706.03762", "doi": "10.48550/arXiv.1706.03762", "channel": "s2"},
                    {"id": "doi:10.48550/arxiv.1706.03762", "doi": "https://doi.org/10.48550/ARXIV.1706.03762"},
                    {"id": "W111", "doi": None},
                    {"id": "W5", "doi": "10.1/keep"},
                ], str(directory / "candidates.json"))
            self.assertIn("种子剔除 4", output.getvalue())
            self.assertEqual([c["id"] for c in json.loads((directory / "candidates.json").read_text())], ["W5"])

    def test_record_without_abstract_has_no_abstract_language(self):
        work = {"id": "https://openalex.org/W1", "display_name": "T", "language": "en", "abstract_inverted_index": None}
        self.assertIsNone(fetch.record(work, "search:x")["abstract_language"])
        work["abstract_inverted_index"] = {"Hello": [0], "world.": [1]}
        self.assertEqual(fetch.record(work, "search:x")["abstract_language"], "en")

    def test_get_retries_transient_errors_then_succeeds(self):
        calls, delays = [], []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(request.full_url, 503, "busy", {"Retry-After": "0"}, None)
            return io.BytesIO(b'{"ok": true}')

        with patch.object(fetch.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(fetch.get("https://api.openalex.org/works", sleeper=delays.append), {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(delays), 2)

    def test_get_does_not_retry_client_errors(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)

        with patch.object(fetch.urllib.request, "urlopen", fake_urlopen), self.assertRaises(urllib.error.HTTPError):
            fetch.get("https://api.openalex.org/works/W0", sleeper=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_exit_codes_separate_usage_state_and_success(self):
        with tempfile.TemporaryDirectory() as directory:
            seen = str(Path(directory) / "seen.json")
            self.assertEqual(run_cli("search", "llm", "--limit").returncode, 64)
            self.assertEqual(run_cli("search", "llm", "--limit", "ten").returncode, 64)
            self.assertEqual(run_cli("search", "llm", "--limit", "0").returncode, 64)
            self.assertEqual(run_cli("no-such-command").returncode, 64)
            self.assertEqual(run_cli("work").returncode, 64)
            self.assertEqual(run_cli("seen", "init", seen).returncode, 0)
            self.assertEqual(run_cli("seen", "init", seen).returncode, 65)
            self.assertEqual(run_cli("seen", "feedback", seen, "W404", "up").returncode, 65)
            Path(seen).write_text("{broken", encoding="utf-8")
            self.assertEqual(run_cli("seen", "list", seen).returncode, 65)


if __name__ == "__main__":
    unittest.main()
