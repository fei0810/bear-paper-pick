"""Offline tests of the Jev-to-delivery contract; never a new-user acceptance."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch
import jev_rank
import screening
import sift
from test_jev_rank import FakeClient, PROFILE, candidate


class ScreeningTests(unittest.TestCase):
    def inputs(self, root, extra=None):
        pool = [candidate("W1", publication_date="2026-01-01")]
        if extra: pool.extend(extra)
        profile = copy.deepcopy(PROFILE)
        profile["layer_targets"] = {x: "0-3" for x in sift.LAYERS}
        fetch.write_json(pool, root / "candidates.json")
        fetch.write_json(profile, root / "profile.json")
        selection = {"line": "test", "run_id": "test", "as_of": "2026-09-29", "picks": [{"id": "W1", "layer": "core", "why": "Matches the defined method."}]}
        return pool, profile, selection

    def screen(self, root, name="review", **kwargs):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test", "JEV_MODE": "shadow"}, clear=True):
            return sift.screen(root / "candidates.json", root / "profile.json", root / name, **kwargs)

    def test_default_workflow_uses_jev_and_carries_evidence_to_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, selection = self.inputs(root)
            fake = FakeClient()
            packet = sift.read(self.screen(root, client=fake))
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(packet["jev_run"]["configuration"]["mode"], "review")
            channels = {x: [{"status": "skipped", "reason": "Offline fixture"}] for x in sift.LAYERS}
            sift.render(selection, pool, profile, set(), [], channels, root / "digest", packet)
            text = (root / "digest/digest.md").read_text()
            self.assertIn("Jev 判断", text)
            self.assertIn("An English abstract.", text)
            for _ in range(2): sift.register(root / "digest/receipt.json", root / "candidates.json", root / "profile.json")
            self.assertEqual(len(sift.read(root / "seen.json")), 1)
            bound = sift.read(root / "digest/screening.json")
            bound["mode"] = "basic"
            fetch.write_json(bound, root / "digest/screening.json")
            with self.assertRaisesRegex(ValueError, "改动"):
                sift.register(root / "digest/receipt.json", root / "candidates.json", root / "profile.json")

    def test_disagreement_requires_reason_and_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, selection = self.inputs(root)
            packet = sift.read(self.screen(root, client=FakeClient()))
            selection["picks"][0]["layer"] = "adjacent"
            self.assertTrue(any("screening_note" in e for e in screening.validate_picks(selection, packet, pool, profile)))
            selection["picks"][0]["screening_note"] = "The abstract describes a neighboring object, not the target object."
            self.assertEqual(screening.validate_picks(selection, packet, pool, profile), [])
            self.assertIn("采用说明", " ".join(screening.pick_evidence(selection["picks"][0], packet)))

    def test_pool_and_profile_changes_invalidate_old_screening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, _ = self.inputs(root)
            packet = sift.read(self.screen(root, client=FakeClient()))
            changed = copy.deepcopy(pool)
            changed[0]["title"] = "Different paper"
            self.assertTrue(screening.validate_packet(packet, changed, profile))
            changed_profile = copy.deepcopy(profile)
            changed_profile["logical_form"] = "Different target"
            self.assertTrue(screening.validate_packet(packet, pool, changed_profile))

    def test_invalid_and_unassessed_picks_need_explicit_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, selection = self.inputs(root, [candidate("W2", abstract="", abstract_language=None)])
            packet = sift.read(self.screen(root, client=FakeClient()))
            selection["picks"][0]["id"] = "W2"
            self.assertTrue(any("未经 Jev" in e for e in screening.validate_picks(selection, packet, pool, profile)))
            selection["picks"][0]["screening_note"] = "Publisher page independently reviewed; abstract unavailable."
            self.assertEqual(screening.validate_picks(selection, packet, pool, profile), [])

    def test_missing_key_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            self.inputs(root)
            with self.assertRaises(jev_rank.ConfigError):
                sift.screen(root / "candidates.json", root / "profile.json", root / "out", env_file=root / "absent")
            self.assertFalse((root / "out/screening.json").exists())

    def test_basic_is_explicit_and_has_no_network(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(jev_rank.JevClient, "request", side_effect=AssertionError("network")):
            root = Path(tmp)
            self.inputs(root)
            with self.assertRaisesRegex(ValueError, "原因"):
                self.screen(root, mode="basic")
            packet = sift.read(self.screen(root, mode="basic", reason="User chose offline review"))
            self.assertEqual(packet["mode"], "basic")
            self.assertIsNone(packet["jev_run"])
            self.assertIn("未调用 Jev", screening.summary(packet))

    def test_plan_is_bounded_and_uses_external_config_model_without_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(jev_rank.JevClient, "request", side_effect=AssertionError("network")):
            root = Path(tmp)
            self.inputs(root, [candidate("W2"), candidate("W3")])
            env = root / "local.env"
            env.write_text("JEV_MODEL=example-pinned-model\n")
            with patch.dict(os.environ, {"BEAR_SIFT_ENV_FILE": str(env)}, clear=True):
                path = sift.screen(root / "candidates.json", root / "profile.json", root / "out", plan_only=True, limit=1)
            plan = sift.read(path)
            self.assertEqual(plan["network_calls"], 0)
            self.assertEqual(len(plan["requests"]), 1)
            self.assertEqual(len(plan["deferred"]), 2)
            self.assertEqual(plan["requests"][0]["model"], "example-pinned-model")

    def test_budget_skips_deferred_and_reuses_cache_across_output_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, _ = self.inputs(root, [candidate("W2")])
            fake = FakeClient()
            first = sift.read(self.screen(root, client=fake, limit=1))
            second = sift.read(self.screen(root, name="r2", client=fake, limit=1))
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(len(first["jev_run"]["deferred"]), 1)
            self.assertTrue(second["jev_run"]["records"][0]["transport"]["cache_hit"])
            with self.assertRaisesRegex(ValueError, "已存在"):
                self.screen(root, client=fake)

    def test_failure_preserves_partial_without_complete_packet(self):
        class Failing(FakeClient):
            def evaluate(self, state, questions):
                raise jev_rank.ApiError(503, "offline failure")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.inputs(root)
            with self.assertRaises(jev_rank.ApiError): self.screen(root, client=Failing())
            self.assertFalse((root / "review/screening.json").exists())
            runs = [sift.read(p) for p in root.rglob("rank-run*.json")]
            self.assertEqual(runs[0]["status"], "failed_partial")

    def test_legacy_delivery_receipt_can_still_be_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, selection = self.inputs(root)
            packet = screening.make_packet(pool, profile, [], reason="Compatibility fixture")
            channels = {x: [{"status": "skipped", "reason": "Offline fixture"}] for x in sift.LAYERS}
            receipt = sift.render(selection, pool, profile, set(), [], channels, root / "digest", packet)
            receipt["schema_version"] = "paper-radar-delivery-v1"
            del receipt["files"]["screening.json"]
            fetch.write_json(receipt, root / "digest/receipt.json")
            self.assertEqual(sift.register(root / "digest/receipt.json", root / "candidates.json", root / "profile.json"), 1)


    def test_changed_decision_cannot_be_used_as_valid_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, _ = self.inputs(root)
            packet = sift.read(self.screen(root, client=FakeClient()))
            packet["jev_run"]["records"][0]["decision"]["priority"] = 99
            self.assertTrue(any("排序" in e for e in screening.validate_packet(packet, pool, profile)))

    def test_metadata_refresh_can_rebind_without_new_remote_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool, profile, _ = self.inputs(root)
            fake = FakeClient()
            self.screen(root, client=fake)
            pool[0]["publication_date"] = "2026-01-02"
            fetch.write_json(pool, root / "candidates.json")
            packet = sift.read(self.screen(root, name="r2", client=fake))
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(screening.validate_packet(packet, pool, profile), [])


if __name__ == "__main__":
    unittest.main()
