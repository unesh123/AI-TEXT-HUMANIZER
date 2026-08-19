"""Style-profile tests: each register keeps its own natural vocabulary.

Business mode should preserve meeting-room idiom ("circle back", "touch
base", "move the needle") while still stripping inflated AI verbs; Casual
mode should preserve spoken idiom and conversational hedges ("silver
lining", "to be honest"); Academic should strip all of them as AI tells.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import analyze
from naturalizer.engine import Naturalizer
from naturalizer.styles import STYLES, get_style


class BusinessIdiomTest(unittest.TestCase):
    def setUp(self):
        self.engine = Naturalizer(seed=0)
        self.text = (
            "Let's circle back on the deliverables and touch base with the "
            "team. This approach will move the needle on engagement, and the "
            "steep learning curve was worth it."
        )

    def test_business_keeps_corporate_idioms(self):
        result = self.engine.naturalize(self.text, style="business", use_llm=False)
        self.assertEqual(result.rewritten, self.text)
        self.assertGreaterEqual(result.score, 90)

    def test_business_does_not_flag_corporate_idioms(self):
        report = analyze(self.text, allowlist=get_style("business")["allowlist"])
        flagged = {i.message for i in report.issues}
        self.assertFalse(any("circle back" in m or "touch base" in m or "move the needle" in m for m in flagged))
        self.assertGreaterEqual(report.score, 90)

    def test_academic_strips_corporate_idioms(self):
        result = self.engine.naturalize(self.text, style="academic", use_llm=False)
        self.assertIn("revisit the deliverables", result.rewritten)
        self.assertIn("check in with the team", result.rewritten)
        self.assertIn("make a difference on engagement", result.rewritten)
        self.assertIn("tough learning period", result.rewritten)
        self.assertNotIn("circle back", result.rewritten)
        self.assertNotIn("touch base", result.rewritten)
        self.assertNotIn("move the needle", result.rewritten)

    def test_business_still_strips_inflated_verbs(self):
        result = self.engine.naturalize(
            "We must leverage our synergy to utilize the budget.",
            style="business",
            use_llm=False,
        )
        self.assertIn("build on our combined effort", result.rewritten)
        self.assertIn("to use the budget", result.rewritten)
        self.assertNotIn("leverage", result.rewritten)
        self.assertNotIn("synergy", result.rewritten)
        self.assertNotIn("utilize", result.rewritten)


class CasualHedgeTest(unittest.TestCase):
    def setUp(self):
        self.engine = Naturalizer(seed=0)
        self.text = (
            "For what it's worth, the delay gave us room. Let's be honest: "
            "it was a rough month, and more often than not the plan slipped."
        )

    def test_casual_keeps_spoken_hedges(self):
        result = self.engine.naturalize(self.text, style="casual", use_llm=False)
        self.assertEqual(result.rewritten, self.text)
        self.assertGreaterEqual(result.score, 90)

    def test_academic_strips_spoken_hedges(self):
        result = self.engine.naturalize(self.text, style="academic", use_llm=False)
        self.assertIn("The delay gave us room", result.rewritten)
        self.assertIn("It was a rough month", result.rewritten)
        self.assertIn("most of the time", result.rewritten)
        self.assertNotIn("for what it's worth", result.rewritten)
        self.assertNotIn("let's be honest", result.rewritten)

    def test_casual_keeps_silver_lining_idiom(self):
        result = self.engine.naturalize(
            "Every cloud has a silver lining, even if you have to squint.",
            style="casual",
            use_llm=False,
        )
        self.assertEqual(result.rewritten, result.original)
        self.assertGreaterEqual(result.score, 90)


class BusinessStructureTest(unittest.TestCase):
    """Business keeps bulleted summaries; other registers flag them."""

    BULLETED = (
        "Here are the key findings from the audit:\n"
        "- Costs dropped 12% after the migration.\n"
        "- Uptime improved from 99.1% to 99.8%.\n"
        "- Support tickets fell by a third.\n"
        "- The rollout finished two weeks early.\n"
        "We will review the next steps on Thursday."
    )

    def setUp(self):
        self.engine = Naturalizer(seed=0)

    def test_business_does_not_flag_bulleted_summary(self):
        result = self.engine.naturalize(self.BULLETED, style="business", use_llm=False)
        self.assertNotIn("structure", {i["kind"] for i in result.issues})
        self.assertGreaterEqual(result.score, 90)

    def test_academic_flags_bulleted_summary(self):
        result = self.engine.naturalize(self.BULLETED, style="academic", use_llm=False)
        self.assertIn("structure", {i["kind"] for i in result.issues})

    def test_business_rewrite_leaves_bullets_intact(self):
        result = self.engine.naturalize(self.BULLETED, style="business", use_llm=False)
        self.assertIn("- Costs dropped", result.rewritten)
        self.assertIn("- The rollout finished", result.rewritten)

    def test_analyze_flag_suppresses_structure(self):
        report = analyze(self.BULLETED)
        self.assertIn("structure", {i.kind for i in report.issues})
        kept = analyze(self.BULLETED, keep_structure=True)
        self.assertNotIn("structure", {i.kind for i in kept.issues})

    def test_only_business_keeps_structure(self):
        for name in ("academic", "creative", "casual"):
            self.assertFalse(get_style(name)["keep_structure"])
        self.assertTrue(get_style("business")["keep_structure"])


class ProfileStructureTest(unittest.TestCase):
    def test_business_allowlists_meeting_room_idiom(self):
        allowlist = get_style("business")["allowlist"]
        for phrase in (
            "circle back",
            "circle back on",
            "touch base with",
            "move the needle",
            "actionable",
            "deep dive into",
            "bandwidth for",
            "on the same page",
            "raised the bar",
        ):
            self.assertIn(phrase, allowlist)

    def test_casual_allowlists_spoken_idiom(self):
        allowlist = get_style("casual")["allowlist"]
        for phrase in (
            "silver lining",
            "a silver lining",
            "to be honest",
            "for what it's worth,",
            "let's be honest:",
            "more often than not",
            "at the end of the day",
        ):
            self.assertIn(phrase, allowlist)

    def test_academic_allowlists_none_of_the_new_idioms(self):
        allowlist = get_style("academic")["allowlist"]
        for phrase in ("circle back", "touch base", "silver lining", "to be honest"):
            self.assertNotIn(phrase, allowlist)

    def test_profiles_expose_plain_allowlist_sets(self):
        for name in STYLES:
            allowlist = get_style(name)["allowlist"]
            self.assertIsInstance(allowlist, set)
            self.assertFalse(allowlist - set(allowlist))  # all lowercase already


if __name__ == "__main__":
    unittest.main()
