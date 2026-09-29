#!/usr/bin/env python3
"""Offline contract tests for the Jev adapter; no API key or network is needed."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jev_rank  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []
        self.config = {"model": "jev-1.13.0"}

    def list_models(self):
        return {"models": [{"name": "jev-latest"}]}, {"attempts": 1, "latency_ms": 1}

    def answers_for(self, state, questions):
        answers = {}
        for question_id, question in questions.items():
            if question["type"] == "choice":
                choice = next(iter(question["criteria"]))
                answers[question_id] = {"type": "choice", "choice": choice, "probabilities": {key: float(key == choice) for key in question["criteria"]}, "confidence": 1.0}
            elif question["type"] == "score":
                answers[question_id] = {"type": "score", "score": 2.0, "legend": {"0": "x"}, "probabilities": {str(i): float(i == 2) for i in range(len(question["criteria"]))}, "confidence": 1.0}
            else:
                answers[question_id] = {"type": "noul", "noul": 0.0}
        return answers

    def evaluate(self, state, questions):
        self.calls.append((state, questions))
        answers = self.answers_for(state, questions)
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 9, "output_tokens": 1}}, {"attempts": 1, "latency_ms": 1}


PROFILE = {
    "schema_version": "paper-radar-profile-v1",
    "line_name": "test",
    "seed_ids": ["W0"],
    "positive_axes": [{"id": "method", "description": "Uses a specified method"}],
    "negative_axes": [],
    "logical_form": "Transfer a defined method to a defined research problem.",
    "layer_targets": {"core": "2-4", "adjacent": "2-3", "cross_domain": "0-2", "canonical": "1-2"},
    "language": "en",
}
CONFIG = {"api_key": "test", "model": "jev-1.13.0", "mode": "shadow", "scope": jev_rank.ALLOWED_SCOPE, "min_confidence": None}


def candidate(paper_id, **extra):
    base = {"id": paper_id, "doi": f"10.1/{paper_id}", "title": f"Title {paper_id}", "year": 2026,
            "abstract": "An English abstract. It has a second sentence.", "abstract_language": "en"}
    base.update(extra)
    return base


class JevRankTests(unittest.TestCase):
    def test_dotenv_is_loaded_but_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TYPESAFE_API_KEY=file-key\nJEV_MODEL=file-model\n", encoding="utf-8")
            with patch.dict(os.environ, {"TYPESAFE_API_KEY": "environment-key"}, clear=True):
                config = jev_rank.load_config(env_file)
            self.assertEqual(config["api_key"], "environment-key")
            self.assertEqual(config["model"], "file-model")

    def test_dotenv_inline_comments_are_stripped_only_when_unquoted(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text('A=abc  # note\nB="x # kept"\nC=abc#nospace\nD=  # empty\n', encoding="utf-8")
            values = jev_rank.read_dotenv(env_file)
        self.assertEqual(values, {"A": "abc", "B": "x # kept", "C": "abc#nospace", "D": ""})

    def test_min_confidence_must_be_between_zero_and_one(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TYPESAFE_API_KEY=k\nJEV_MIN_CONFIDENCE=1.5\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(jev_rank.ConfigError):
                jev_rank.load_config(env_file)
            env_file.write_text("TYPESAFE_API_KEY=k\nJEV_MIN_CONFIDENCE=0.6\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(jev_rank.load_config(env_file)["min_confidence"], 0.6)

    def test_smoke_writes_audit_without_credentials(self):
        config = {"api_key": "not-written", "model": "jev-1.13.0", "mode": "shadow", "scope": jev_rank.ALLOWED_SCOPE}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            record, path = jev_rank.run_smoke(config, directory, FakeClient())
            content = path.read_text(encoding="utf-8")
        self.assertEqual(record["result"]["model"], "jev-1.13.0")
        self.assertNotIn("not-written", content)
        self.assertEqual(record["sent_scope"], "synthetic_english_abstract_only")

    def test_remote_state_contains_only_public_card_fields_and_minimal_profile(self):
        item = candidate("W1", channel="search:private query words", cites_seed="W999", private_note="local only")
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            run, _ = jev_rank.run_rank(CONFIG, PROFILE, [item], directory, client=fake)
        sent_card = fake.calls[0][0]["candidate"]
        sent_profile = fake.calls[0][0]["profile"]
        self.assertEqual(set(sent_card), set(jev_rank.REMOTE_CARD_FIELDS))
        for private in ("channel", "cites_seed", "private_note", "hard_risk"):
            self.assertNotIn(private, sent_card)
        self.assertEqual(set(sent_profile), {"positive_axes", "negative_axes", "logical_form", "language"})
        self.assertNotIn("private query words", json.dumps(fake.calls[0][0]))
        # 本地审计记录保留完整卡片，方便复核
        self.assertEqual(run["records"][0]["candidate_card"]["channel"], "search:private query words")
        self.assertEqual(run["records"][0]["decision"]["route"], "human_review")

    def test_ineligible_candidates_are_skipped_without_stopping_the_run(self):
        items = [candidate("W1"), {"id": "doi:10.1/s2", "title": "From S2", "abstract": "English text."},
                 candidate("W3", abstract=""), candidate("W4")]
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            run, path = jev_rank.run_rank(CONFIG, PROFILE, items, directory, limit=2, client=fake)
            self.assertTrue(path.exists())
        self.assertEqual([r["candidate_id"] for r in run["records"]], ["W1", "W4"])
        self.assertEqual({s["candidate_id"]: s["reasons"] for s in run["skipped"]},
                         {"doi:10.1/s2": ["abstract_language_not_en"], "W3": ["no_abstract"]})
        self.assertEqual(run["status"], "complete")

    def test_out_of_option_answers_are_flagged_and_never_cached(self):
        class Weird(FakeClient):
            def answers_for(self, state, questions):
                answers = super().answers_for(state, questions)
                answers["layer"] = {"type": "choice", "choice": "definitely_core", "confidence": 0.05}
                answers["fit"] = {"type": "score", "score": 17}
                return answers
        with tempfile.TemporaryDirectory() as directory:
            run, _ = jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1")], directory, client=Weird())
            cached = list((Path(directory) / "cache").glob("*.json")) if (Path(directory) / "cache").exists() else []
        record = run["records"][0]
        self.assertIn("layer:out_of_options", record["problems"])
        self.assertIn("fit:score_out_of_range", record["problems"])
        self.assertEqual(record["decision"]["priority"], 0.0)
        self.assertEqual(cached, [])

    def test_valid_answers_are_cached_and_reused(self):
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1")], directory, client=fake)
            run, _ = jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1")], directory, client=fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(run["records"][0]["transport"]["cache_hit"])

    def test_review_mode_orders_by_priority_and_shadow_keeps_input_order(self):
        class Graded(FakeClient):
            def answers_for(self, state, questions):
                answers = super().answers_for(state, questions)
                fit = {"W1": 1.0, "W2": 3.0, "W3": 2.0}[state["candidate"]["id"]]
                answers["fit"] = {"type": "score", "score": fit, "confidence": 1.0, "probabilities": {str(i): float(i == fit) for i in range(4)}}
                return answers
        items = [candidate("W1"), candidate("W2"), candidate("W3")]
        with tempfile.TemporaryDirectory() as directory:
            shadow, _ = jev_rank.run_rank(CONFIG, PROFILE, items, directory, client=Graded())
        with tempfile.TemporaryDirectory() as directory:
            review, _ = jev_rank.run_rank(dict(CONFIG, mode="review"), PROFILE, items, directory, client=Graded())
        self.assertEqual([r["candidate_id"] for r in shadow["records"]], ["W1", "W2", "W3"])
        self.assertEqual([r["candidate_id"] for r in review["records"]], ["W2", "W3", "W1"])
        self.assertEqual([r["review_order"] for r in review["records"]], [1, 2, 3])

    def test_low_confidence_is_flagged_only_when_threshold_configured(self):
        class Unsure(FakeClient):
            def answers_for(self, state, questions):
                answers = super().answers_for(state, questions)
                answers["layer"]["confidence"] = 0.3
                return answers
        with tempfile.TemporaryDirectory() as directory:
            unset, _ = jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1")], directory, client=Unsure())
        with tempfile.TemporaryDirectory() as directory:
            gated, _ = jev_rank.run_rank(dict(CONFIG, min_confidence=0.6), PROFILE, [candidate("W1")], directory, client=Unsure())
        self.assertNotIn("low_confidence", unset["records"][0]["decision"]["flags"])
        self.assertIn("low_confidence", gated["records"][0]["decision"]["flags"])

    def test_failure_midway_writes_partial_run_then_raises(self):
        class Flaky(FakeClient):
            def evaluate(self, state, questions):
                if self.calls:
                    raise jev_rank.ApiError(529, "TypeSafe API 返回 HTTP 529")
                return super().evaluate(state, questions)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(jev_rank.ApiError):
                jev_rank.run_rank(CONFIG, PROFILE, [candidate("W1"), candidate("W2")], directory, client=Flaky())
            runs = list((Path(directory) / "runs").glob("rank-run-*.json"))
            self.assertEqual(len(runs), 1)
            partial = json.loads(runs[0].read_text(encoding="utf-8"))
        self.assertEqual(partial["status"], "failed_partial")
        self.assertEqual([r["candidate_id"] for r in partial["records"]], ["W1"])
        self.assertIn("ApiError", partial["error"])

    def test_audit_filename_uses_utc(self):
        before = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        name = jev_rank.audit_path("x", "rank-run").name
        stamp = dt.datetime.strptime(name.split("-")[2], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        self.assertLessEqual(abs((stamp - before).total_seconds()), 5)


if __name__ == "__main__":
    unittest.main()
