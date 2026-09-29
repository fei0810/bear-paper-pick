import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch
import jev_rank
import sift as radar
import screening
from test_jev_rank import FakeClient, PROFILE, CONFIG, candidate


class ReleaseTests(unittest.TestCase):
    def test_all_ranking_probabilities_are_validated(self):
        questions = jev_rank.questions_for(PROFILE, jev_rank.public_candidate_card(candidate("W1")))
        good = FakeClient().answers_for({}, questions)
        self.assertEqual(jev_rank.answer_problems(good, questions), [])
        for bad in (float("nan"), float("inf"), -1, 2, True, "1"):
            value = copy.deepcopy(good)
            value["layer"]["probabilities"]["core"] = bad
            with self.subTest(value=bad):
                self.assertIn("layer:invalid_distribution", jev_rank.answer_problems(value, questions))
        bad = copy.deepcopy(good)
        bad["layer"]["probabilities"] = {"invented_option": 1}
        self.assertIn("layer:invalid_distribution", jev_rank.answer_problems(bad, questions))

    def test_real_two_decimal_responses_fit_rounding_bounds(self):
        path = Path(__file__).parent / "fixtures/jev_rounded_responses.json"
        for fixture in json.loads(path.read_text()):
            self.assertEqual(jev_rank.answer_problems(fixture["answers"], fixture["questions"]), [])

    def test_malformed_answer_cannot_crash_decision(self):
        decision = jev_rank.decision_from({"negative_conflict": "bad", "fit": None}, ["bad"])
        self.assertEqual(decision["priority"], 0)

    def test_model_mismatch_never_enters_cache(self):
        class Different(FakeClient):
            def evaluate(self, state, questions):
                result, metadata = super().evaluate(state, questions)
                result["model"] = "other-model"
                return result, metadata
        with tempfile.TemporaryDirectory() as directory:
            run, _ = jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1")], directory, client=Different())
            self.assertIn("response_model_mismatch", run["records"][0]["problems"])
            self.assertEqual(list(Path(directory).glob("cache/*.json")), [])

    def test_corrupt_cache_is_refetched(self):
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1")], directory, client=fake)
            next(Path(directory).glob("cache/*.json")).write_text("{broken")
            run, _ = jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1")], directory, client=fake)
            self.assertFalse(run["records"][0]["transport"]["cache_hit"])
            self.assertEqual(len(fake.calls), 2)

    def test_plan_has_no_credentials_or_network_and_filters_seen_seeds(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(jev_rank.JevClient, "request", side_effect=AssertionError("network")):
            plan, path = jev_rank.run_plan(PROFILE, [candidate("W0"), candidate("W1"), candidate("W2")], directory, seen=[candidate("W1")])
            self.assertEqual(len(plan["requests"]), 1)
            self.assertEqual(plan["requests"][0]["state"]["candidate"]["id"], "W2")
            self.assertNotIn("api_key", path.read_text())

    def test_arxiv_versions_and_openalex_urls_are_identity_equivalent(self):
        self.assertEqual(fetch.identity_keys("https://openalex.org/W123"), fetch.identity_keys("W123"))
        self.assertEqual(fetch.identity_keys("https://arxiv.org/abs/1706.03762v2"), fetch.identity_keys("10.48550/arxiv.1706.03762"))

    def test_duplicate_hits_merge_provenance(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / "candidates.json"
            fetch.save([candidate("W1", channel="cites:W0", cites_seed="W0")], path)
            fetch.save([candidate("W1", channel="cites:W2", cites_seed="W2")], path)
            result = json.loads(path.read_text())
            self.assertEqual(len(result), 1)
            self.assertEqual(set(result[0]["cites_seeds"]), {"W0", "W2"})
            self.assertEqual(len(result[0]["provenance"]), 2)

    def test_paging_honors_limit_above_one_hundred(self):
        calls = []
        def page(url):
            calls.append(url)
            if len(calls) == 1:
                return {"meta": {"count": 140, "next_cursor": "next"}, "results": [{"id": str(i)} for i in range(100)]}
            return {"meta": {"count": 140, "next_cursor": None}, "results": [{"id": str(i)} for i in range(100, 140)]}
        with patch.object(fetch, "get", page), contextlib.redirect_stdout(io.StringIO()):
            result = fetch.list_works({"search": "a,b"}, 140)
        self.assertEqual(len(result["results"]), 140)
        self.assertFalse(result["meta"]["truncated"])
        self.assertEqual(len(calls), 2)

    def test_search_comma_is_a_query_value_and_save_is_compact(self):
        calls = []
        work = {"id": "https://openalex.org/W1", "display_name": "Exact title", "abstract_inverted_index": {"PRIVATE_CONTEXT_SENTINEL": [0]}}
        def get(url):
            calls.append(url)
            return {"meta": {"count": 1}, "results": [work]}
        with tempfile.TemporaryDirectory() as directory, patch.object(fetch, "get", get), patch.object(sys, "argv", ["fetch.py", "search", "a,b", "--save", str(Path(directory) / "candidates.json")]), contextlib.redirect_stdout(io.StringIO()) as output:
            fetch.main()
        self.assertIn("search=a%2Cb", calls[0])
        self.assertNotIn("PRIVATE_CONTEXT_SENTINEL", output.getvalue())

    def make_delivery(self, directory):
        directory = Path(directory)
        pool = [candidate("W1", authors=["Ana Muñoz"], title="BERT & A_1 {test}", publication_date="2025-06-01")]
        profile = copy.deepcopy(PROFILE)
        profile["layer_targets"] = {x: "0-2" for x in radar.LAYERS}
        selection = {"line": "test", "run_id": "sample", "as_of": "2026-09-29", "picks": [{"id": "W1", "layer": "core", "why": "Useful bounded relevance."}]}
        fetch.write_json(pool, directory / "candidates.json")
        fetch.write_json(profile, directory / "profile.json")
        channels = {x: [{"status": "skipped", "reason": "Offline fixture"}] for x in radar.LAYERS}
        return pool, profile, selection, channels

    def test_render_register_and_repeat_are_closed_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool, profile, selection, channels = self.make_delivery(root)
            output = root / "delivery"
            radar.render(selection, pool, profile, set(), [], channels, output, screening.make_packet(pool, profile, [], reason="Offline basic fixture"))
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["selected_metadata"][0]["title"], pool[0]["title"])
            bib = (output / "references.bib").read_text()
            self.assertIn("Munoz2026", bib)
            self.assertIn(r"title = {{BERT \& A\_1 \{test\}}}", bib)
            for _ in range(2): radar.register(output / "receipt.json", root / "candidates.json", root / "profile.json")
            seen = json.loads((root / "seen.json").read_text())
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["title"], pool[0]["title"])
            self.assertTrue(any("已推送" in e for e in radar.validate(selection, pool, profile, set(), seen)))

    def test_invalid_selection_is_rejected_before_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool, profile, selection, channels = self.make_delivery(root)
            selection["picks"].append(dict(selection["picks"][0], layer="cross_domain"))
            errors = radar.validate(selection, pool, profile, set(), pool)
            self.assertTrue(any("已推送" in e for e in errors))
            self.assertTrue(any("重复" in e for e in errors))
            self.assertTrue(any("同构" in e for e in errors))
            with self.assertRaises(ValueError): radar.render(selection, pool, profile, set(), [], channels, root / "bad", screening.make_packet(pool, profile, [], reason="Offline basic fixture"))
            self.assertFalse((root / "bad").exists())

    def test_mutated_digest_cannot_be_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool, profile, selection, channels = self.make_delivery(root)
            radar.render(selection, pool, profile, set(), [], channels, root / "delivery", screening.make_packet(pool, profile, [], reason="Offline basic fixture"))
            (root / "delivery/digest.md").write_text("changed")
            with self.assertRaisesRegex(ValueError, "改动"):
                radar.register(root / "delivery/receipt.json", root / "candidates.json", root / "profile.json")
            self.assertFalse((root / "seen.json").exists())

    def test_dates_diversity_and_shortfalls_are_checked(self):
        pool = [candidate(f"W{i}", publication_date="2026-08-01", cites_seeds=["W0"]) for i in range(1, 4)]
        selection = {"line": "test", "run_id": "x", "as_of": "2026-09-29", "picks": [{"id": p["id"], "layer": "core", "why": "x"} for p in pool]}
        errors = radar.validate(selection, pool, PROFILE, set(), [])
        self.assertTrue(any("上限 2" in e for e in errors))
        self.assertTrue(any("不足" in e for e in errors))
        pool[0]["publication_date"] = "2099-01-01"
        self.assertTrue(any("晚于" in e for e in radar.validate(selection, pool, PROFILE, set(), [])))

    def test_missing_channel_report_is_visible_error(self):
        with self.assertRaisesRegex(ValueError, "缺层"):
            radar.channel_summary([], ["core=no key"])

    def test_crossref_month_only_issue_does_not_turn_into_day_one(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / "candidates.json"
            fetch.write_json([candidate("W1", publication_date="2025-08-01")], path)
            response = {"message": {"DOI": "10.1/W1", "title": ["<i>Title</i> W1"], "published": {"date-parts": [[2025, 8]]}, "published-online": {"date-parts": [[2025, 8, 4]]}}}
            with patch.object(fetch, "get", return_value=response):
                result = fetch.verify_metadata(path, ["W1"])[0]
            self.assertEqual(result["publication_date"], "2025-08-04")
            self.assertEqual(result["publication_date_original"], "2025-08-01")
            self.assertFalse(result["metadata_conflict"])

    def test_crossref_identity_mismatch_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / "candidates.json"
            fetch.write_json([candidate("W1")], path)
            with patch.object(fetch, "get", return_value={"message": {"DOI": "10.1/W1", "title": ["Different work"]}}):
                result = fetch.verify_metadata(path, ["W1"])[0]
            self.assertTrue(result["metadata_conflict"])
            eligible, skipped, _ = jev_rank.preflight(PROFILE, [result], directory)
            self.assertEqual(eligible, [])
            self.assertIn("metadata_requires_review", skipped[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
