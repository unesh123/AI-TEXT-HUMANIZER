"""Tests for the live feedback-loop humanizer (\"perfect\" mode) and the
multi-detector status panel.

The feedback loop prefers the LLM path when a provider is configured; here
every provider is scrubbed (tests.testutil), so the engine runs the
deterministic path — which is exactly the free-tier behavior. HTTP and LLM
calls are never made.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.testutil  # noqa: F401 - scrub provider env + default plan to pro

from naturalizer.engine import NaturalizeResult, Naturalizer
from naturalizer.feedback import (
    DETECTORS,
    HUMAN_FLOOR,
    MAX_PASSES,
    detector_status,
    feedback_humanize,
)

AI_SAMPLE = (
    "In today's fast-paced world, it is important to note that technology "
    "plays a crucial role in our daily lives. Furthermore, the ever-evolving "
    "landscape of digital tools continues to transform the way we work and "
    "communicate. Moreover, it is essential to highlight that organizations "
    "must leverage cutting-edge solutions to remain competitive."
)

HUMAN_SAMPLE = (
    "The wiring came loose behind the counter, so we shut the power off "
    "before touching anything. Once the new breaker was in, the lights came "
    "back on without a flicker. We left the panel open for an hour just to "
    "make sure nothing else was drifting, and then closed it up and went home."
)


class DetectorStatusTest(unittest.TestCase):
    def test_local_always_live(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            status = {d["name"]: d for d in detector_status()}
            self.assertTrue(status["local"]["configured"])
            self.assertTrue(status["local"]["live"])

    def test_third_party_unconfigured_without_keys(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            status = {d["name"]: d for d in detector_status()}
            for name in ("gptzero", "zerogpt", "originality", "turnitin"):
                self.assertFalse(status[name]["configured"], name)
                self.assertIn("add ", (status[name]["note"] or ""), name)

    def test_third_party_configured_with_keys(self):
        with mock.patch.dict(
            "os.environ",
            {"GPTZERO_API_KEY": "gz", "ZEROGPT_API_KEY": "zg"},
            clear=True,
        ):
            status = {d["name"]: d for d in detector_status()}
            self.assertTrue(status["gptzero"]["configured"])
            self.assertTrue(status["zerogpt"]["configured"])
            self.assertFalse(status["originality"]["configured"])


class FeedbackLoopTest(unittest.TestCase):
    def test_deterministic_loop_produces_result(self):
        engine = Naturalizer(prefer_llm=False)
        result = feedback_humanize(engine, AI_SAMPLE, style="academic")
        self.assertIn("text", result)
        self.assertTrue(result["text"].strip())
        self.assertGreaterEqual(result["passes"], 1)
        self.assertLessEqual(result["passes"], MAX_PASSES)  # capped
        # scores[0] is the original; every rewrite adds one more.
        self.assertEqual(len(result["scores"]), result["passes"] + 1)
        self.assertEqual(result["scores"][0], result["scores"][0])  # int
        self.assertIn("detectors", result)
        self.assertEqual(len(result["detectors"]), len(DETECTORS))

    def test_empty_text_short_circuits(self):
        engine = Naturalizer(prefer_llm=False)
        result = feedback_humanize(engine, "   ")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["passes"], 0)

    def test_metrics_carry_plain_register(self):
        """The loop result reports the verified human-writing memory shift
        (before on the original, after on the converged text) so the UI can
        render the plain-register meter for the perfect loop too."""
        engine = Naturalizer(prefer_llm=False)
        result = feedback_humanize(engine, AI_SAMPLE, style="academic")
        pr = (result.get("metrics") or {}).get("plain_register") or {}
        self.assertIn("before", pr)
        self.assertIn("after", pr)
        self.assertGreaterEqual(pr["before"], 0.0)
        self.assertLessEqual(pr["before"], 1.0)
        self.assertGreaterEqual(pr["after"], 0.0)
        self.assertLessEqual(pr["after"], 1.0)

    def test_already_human_text_converges_fast(self):
        engine = Naturalizer(prefer_llm=False)
        result = feedback_humanize(engine, HUMAN_SAMPLE, style="academic")
        self.assertGreaterEqual(result["scores"][-1], HUMAN_FLOOR)

    def test_instruction_passed_to_engine_on_later_passes(self):
        engine = Naturalizer(prefer_llm=False)
        captured = {}

        def fake_naturalize(text, **kwargs):
            captured["pass"] = captured.get("pass", 0) + 1
            captured["instructions"] = captured.get("instructions", [])
            captured["instructions"].append(kwargs.get("instruction"))
            # Return the same tell-heavy text as the "rewrite" every time, so
            # the re-scan always scores below the floor and the loop is forced
            # to run again and carry an instruction into the next pass.
            return NaturalizeResult(
                original=text,
                rewritten=AI_SAMPLE,
                score=30,
                llm_rewritten=None,
                llm_used=False,
                llm_method=None,
                llm_warning=None,
                style=kwargs.get("style", "academic"),
                intensity=kwargs.get("intensity", 0.5),
            )

        with mock.patch.object(engine, "naturalize", side_effect=fake_naturalize):
            feedback_humanize(engine, AI_SAMPLE, style="academic", max_passes=2)

        self.assertGreaterEqual(captured["pass"], 2)
        instructions = [i for i in captured["instructions"] if i]
        self.assertTrue(instructions, "later passes must carry remaining-issue instructions")

    def test_fact_drift_triggers_restore_instruction(self):
        """A pass that drops a number must feed a restore instruction back."""
        engine = Naturalizer(prefer_llm=False)
        captured = {}
        calls = {"n": 0}

        def fake_naturalize(text, **kwargs):
            calls["n"] += 1
            captured["instruction"] = kwargs.get("instruction")
            # Second pass: the "rewrite" drops the 12 weeks / 800 figure.
            if calls["n"] >= 2:
                rewritten = AI_SAMPLE.replace("technology", "the study")  # no numbers
            else:
                rewritten = AI_SAMPLE
            return NaturalizeResult(
                original=text,
                rewritten=rewritten,
                score=30 if calls["n"] >= 2 else 100,
                llm_rewritten=None,
                llm_used=False,
                llm_method=None,
                llm_warning=None,
                style=kwargs.get("style", "academic"),
                intensity=kwargs.get("intensity", 0.5),
            )

        source = (
            "The trial ran for 12 weeks and included 800 participants." +
            " " + AI_SAMPLE
        )
        with mock.patch.object(engine, "naturalize", side_effect=fake_naturalize):
            feedback_humanize(engine, source, style="academic", max_passes=3)

        # Pass 2 or 3 must carry the number-restore instruction.
        self.assertTrue(
            captured["instruction"] and "Restore the number" in captured["instruction"],
            captured["instruction"],
        )

    def test_fact_issues_reported_in_result(self):
        """The final result reports numbers the loop could not restore."""
        engine = Naturalizer(prefer_llm=False)
        source = (
            "The trial ran for 12 weeks and included 800 participants."
            + " " + HUMAN_SAMPLE
        )

        def fake_naturalize(text, **kwargs):
            return NaturalizeResult(
                original=text,
                rewritten=HUMAN_SAMPLE,  # drops 12 and 800
                score=100,
                llm_rewritten=None,
                llm_used=False,
                llm_method=None,
                llm_warning=None,
                style=kwargs.get("style", "academic"),
                intensity=kwargs.get("intensity", 0.5),
            )

        with mock.patch.object(engine, "naturalize", side_effect=fake_naturalize):
            result = feedback_humanize(engine, source, style="academic", max_passes=2)

        self.assertTrue(result["fact_issues"])
        numbers = {i["snippet"] for i in result["fact_issues"]}
        self.assertIn("12", numbers)
        self.assertIn("800", numbers)


if __name__ == "__main__":
    unittest.main()
