"""Tests for the detector / naturalness scoring."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import (
    analyze,
    classify_sentences,
    sentence_distribution,
    split_sentences,
)

AI_HEAVY = (
    "In today's fast-paced world, it is important to note that technology "
    "plays a crucial role in our daily lives. Furthermore, the ever-evolving "
    "landscape of digital tools continues to transform the way we work. "
    "Moreover, it is essential to highlight that organizations must leverage "
    "cutting-edge solutions. Additionally, the realm of artificial "
    "intelligence offers a plethora of opportunities. In conclusion, it is "
    "important to remember that navigating the complexities of modern "
    "technology requires a robust and holistic approach. Overall, the "
    "journey toward digital transformation underscores the paramount "
    "importance of adaptability."
)

HUMAN_LIKE = (
    "I got the new coffee machine set up this morning, and it turns out the "
    "old one wasn't broken after all. The wiring had come loose behind the "
    "counter. A quick screwdriver job fixed it in ten minutes, and now the "
    "office smells like a café. Colleagues keep wandering over, cup in hand, "
    "hoping I'll brew another pot. I suppose I should have checked the "
    "obvious before ordering a replacement, but the new one is nice anyway."
)


class SplitSentencesTest(unittest.TestCase):
    def test_splits_on_periods(self):
        self.assertEqual(
            split_sentences("One. Two! Three?"),
            ["One.", "Two!", "Three?"],
        )

    def test_keeps_single_sentence(self):
        self.assertEqual(split_sentences("Just one sentence here."), ["Just one sentence here."])


class AnalyzeTest(unittest.TestCase):
    def test_ai_heavy_text_scores_low_and_flags_fillers(self):
        report = analyze(AI_HEAVY)
        self.assertLess(report.score, 60)
        kinds = {i.kind for i in report.issues}
        self.assertIn("filler", kinds)
        # A couple of specific phrases must be caught.
        messages = " ".join(i.message.lower() for i in report.issues)
        self.assertIn("fast-paced", messages)
        self.assertIn("landscape", messages)

    def test_human_like_text_scores_high(self):
        report = analyze(HUMAN_LIKE)
        self.assertGreater(report.score, 80)
        self.assertEqual(report.issues, [])

    def test_score_clamped_to_range(self):
        report = analyze(AI_HEAVY * 5)
        self.assertGreaterEqual(report.score, 0)
        self.assertLessEqual(report.score, 100)

    def test_allowlist_suppresses_matches(self):
        allowed = analyze(
            AI_HEAVY,
            allowlist={"in today's fast-paced world", "the ever-evolving landscape of"},
        )
        msgs = " ".join(i.message for i in allowed.issues)
        self.assertNotIn("fast-paced", msgs)
        self.assertNotIn("landscape", msgs)
        # Other tells are still detected.
        self.assertIn("filler", {i.kind for i in allowed.issues})

    def test_empty_text_does_not_crash(self):
        report = analyze("")
        self.assertIsInstance(report.score, int)

    def test_new_hedge_patterns_detected(self):
        text = (
            "It's worth mentioning that the plan changed. "
            "It is clear that the numbers improved. "
            "When it comes to cost, we are over budget."
        )
        report = analyze(text)
        kinds = {i.kind for i in report.issues}
        self.assertIn("hedge", kinds)
        msgs = " ".join(i.message for i in report.issues)
        self.assertIn("worth mentioning", msgs)
        self.assertIn("clear that", msgs)
        self.assertIn("when it comes to", msgs)

    def test_new_cliche_and_formulaic_patterns_detected(self):
        text = (
            "The policy is a double-edged sword. "
            "At the end of the day, we need a silver bullet. "
            "It is not only fast but also cheap, and it is one of the most "
            "talked-about releases this year."
        )
        report = analyze(text)
        kinds = {i.kind for i in report.issues}
        self.assertIn("cliche", kinds)
        self.assertIn("formulaic", kinds)
        msgs = " ".join(i.message for i in report.issues)
        self.assertIn("double-edged sword", msgs)
        self.assertIn("silver bullet", msgs)
        self.assertIn("not only", msgs)

    def test_second_expansion_patterns_detected(self):
        text = (
            "As we all know, the deadline moved. Due to the fact that demand "
            "rose, costs climbed in turn. In light of the numbers, the new "
            "hire hit the ground running, and the tool plays a key role in "
            "the workflow. Without a doubt, the synergy across teams is real, "
            "and moving forward we should foster collaboration."
        )
        report = analyze(text)
        msgs = " ".join(i.message for i in report.issues)
        for phrase in (
            "as we all know",
            "due to the fact that",
            "in turn",
            "in light of",
            "hit the ground running",
            "plays a key role in",
            "without a doubt",
            "synergy",
            "moving forward",
            "foster",
        ):
            self.assertIn(phrase, msgs)
        kinds = {i.kind for i in report.issues}
        self.assertTrue({"cliche", "hedge", "transition", "formulaic", "filler"} <= kinds)

    # -- signals ported from lynote-ai/ai-text-detector -----------------------

    def test_structured_answer_shape_detected(self):
        text = (
            "Here are the main reasons:\n"
            "- The tool saves time.\n"
            "- It reduces errors.\n"
            "- It is cheap to run.\n"
            "- The team adopted it quickly.\n"
            "Overall, the rollout should continue."
        )
        report = analyze(text)
        kinds = {i.kind for i in report.issues}
        self.assertIn("structure", kinds)
        issues = [i for i in report.issues if i.kind == "structure"]
        self.assertEqual(issues[0].severity, "medium")

    def test_single_bullet_is_not_flagged(self):
        # A couple of bullets plus a short label line is normal human writing.
        report = analyze("here is what we need:\n- milk\n- eggs")
        self.assertNotIn("structure", {i.kind for i in report.issues})

    def test_lexical_variety_is_length_gated(self):
        # Short text: low TTR carries no signal, so nothing must fire.
        short = analyze(AI_HEAVY)
        self.assertNotIn("lexical", {i.kind for i in short.issues})
        # Long repetitive text: fired as medium.
        long = "The team works hard on the plan and the plan works well for the team. " * 30
        report = analyze(long)
        lexical = [i for i in report.issues if i.kind == "lexical"]
        self.assertEqual(len(lexical), 1)
        self.assertEqual(lexical[0].severity, "medium")

    def test_compressibility_is_length_gated(self):
        short = analyze(AI_HEAVY)
        self.assertNotIn("compressibility", {i.kind for i in short.issues})
        long = "The team works hard on the plan and the plan works well for the team. " * 30
        report = analyze(long)
        comp = [i for i in report.issues if i.kind == "compressibility"]
        self.assertEqual(len(comp), 1)

    def test_short_sample_note_only_below_thirty_words(self):
        short = analyze("In today's world, the plan is simple and the team agrees.")
        kinds = {i.kind for i in short.issues}
        self.assertIn("short", kinds)
        self.assertNotIn("rhythm", kinds)
        self.assertNotIn("repetition", kinds)
        # 60+ words: no note, statistical checks run again.
        report = analyze(HUMAN_LIKE)
        self.assertNotIn("short", {i.kind for i in report.issues})

    # -- residual LLM-output tells (institutional/academic scaffolding) ----

    def test_institutional_tells_fire(self):
        text = (
            "Our analysis focused on three values. We first sought to define "
            "the concept. We were also asked to document our emotions. This "
            "assignment required us to prepare a report. The system was "
            "successfully implemented."
        )
        report = analyze(text)
        kinds = {i.kind for i in report.issues}
        self.assertIn("formulaic", kinds)
        messages = " ".join(i.message.lower() for i in report.issues)
        for phrase in ("our analysis focused on", "we first sought to",
                       "we were also asked to", "this assignment required us",
                       "successfully implemented"):
            self.assertIn(phrase, messages)

    def test_stock_modal_and_paired_antonym_fire(self):
        report = analyze(
            "Discerning users must also consider the intended and unintended "
            "consequences of the technology."
        )
        messages = " ".join(i.message.lower() for i in report.issues)
        self.assertIn("must also consider", messages)
        self.assertIn("intended and unintended", messages)

    def test_institutional_tells_do_not_fire_on_plain_prose(self):
        # Humans do say these words — just not stacked as a report scaffold.
        text = (
            "We considered three options and picked the second one. The fix "
            "went in without much trouble, and the team moved on to the next "
            "task. Nobody asked us to document anything."
        )
        report = analyze(text)
        messages = " ".join(i.message.lower() for i in report.issues)
        self.assertNotIn("must also consider", messages)
        self.assertNotIn("intended and unintended", messages)
        self.assertNotIn("we first sought to", messages)

    # -- sentence classification ------------------------------------------

    def test_classify_sentences_labels(self):
        text = (
            "In today's fast-paced world, it is important to note that tech "
            "matters. "
            "The wiring came loose behind the counter and we fixed it."
        )
        classified = classify_sentences(text)
        self.assertEqual(len(classified), 2)
        self.assertEqual(classified[0]["label"], "ai")
        self.assertEqual(classified[1]["label"], "human")
        self.assertIn("issues", classified[0])
        self.assertIsInstance(classified[0]["issues"], list)

    def test_sentence_distribution_sums(self):
        text = (
            "In today's fast-paced world, it is important to note that tech "
            "matters. "
            "The wiring came loose behind the counter and we fixed it. "
            "Furthermore, we must leverage cutting-edge tools."
        )
        dist = sentence_distribution(text)
        self.assertEqual(dist["ai"] + dist["mix"] + dist["human"], 100)
        self.assertEqual(len(dist["sentences"]), 3)
        self.assertIn("ai", dist)

    def test_classify_empty_text(self):
        self.assertEqual(classify_sentences(""), [])
        dist = sentence_distribution("")
        self.assertEqual(dist["sentences"], [])
        self.assertEqual(dist["ai"] + dist["mix"] + dist["human"], 0)

    # -- windowed segmentation (Turnitin-style passage scoring) ------------

    def test_window_pulls_clean_sentence_inside_ai_run_to_ai(self):
        # A sentence with no tells sandwiched between two tell-heavy
        # sentences: windowed scoring flags it as part of the AI passage.
        text = (
            "In today's fast-paced world, technology plays a crucial role. "
            "The wiring behind the counter held."
            " Furthermore, we must leverage cutting-edge tools to remain "
            "competitive."
        )
        classified = classify_sentences(text)
        labels = [c["label"] for c in classified]
        self.assertEqual(labels[0], "ai")
        self.assertIn(labels[1], ("ai", "mix"), "clean sentence inside an AI run")
        self.assertEqual(labels[2], "ai")

    def test_window_downgrades_isolated_ai_sentence(self):
        # One tell-heavy sentence surrounded by clean prose: windowed
        # scoring treats it as an outlier, not an AI passage.
        text = (
            "The wiring came loose behind the counter, so we shut the power off. "
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role. "
            "Once the new breaker was in, the lights came back on."
        )
        classified = classify_sentences(text)
        labels = [c["label"] for c in classified]
        self.assertEqual(labels[0], "human")
        self.assertNotEqual(labels[1], "ai", "isolated tell is an outlier, not a region")
        self.assertEqual(labels[2], "human")

    def test_region_detection_contiguous_ai_run(self):
        text = (
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role. "
            "Furthermore, we must leverage cutting-edge tools to remain "
            "competitive. "
            "Moreover, the ever-evolving landscape demands comprehensive "
            "solutions. "
            "The wiring came loose behind the counter and we fixed it."
        )
        dist = sentence_distribution(text)
        self.assertTrue(dist["regions"], "expected a contiguous AI region")
        region = dist["regions"][0]
        self.assertGreaterEqual(region["count"], 2)
        self.assertEqual(region["end"] - region["start"] + 1, region["count"])
        self.assertIn("text", region)

    def test_no_region_for_scattered_tells(self):
        # One tell per sentence, none adjacent -> no AI block.
        text = (
            "The wiring came loose behind the counter. "
            "In today's fast-paced world, we fixed it. "
            "Once the new breaker was in, the lights came back on."
        )
        dist = sentence_distribution(text)
        self.assertEqual(dist["regions"], [])

    # -- confidence / abstention -------------------------------------------

    def test_short_sample_abstains(self):
        from naturalizer.detectors import abstain_reasons, analyze

        report = analyze("In today's world, tech matters.")
        reasons = abstain_reasons("In today's world, tech matters.", report)
        self.assertTrue(reasons, "short sample should abstain")
        self.assertTrue(any("short" in r for r in reasons))

    def test_long_clean_sample_has_no_abstention(self):
        from naturalizer.detectors import abstain_reasons, analyze

        text = (
            "The wiring came loose behind the counter, so we shut the power off "
            "before touching anything. Once the new breaker was in, the lights "
            "came back on without a flicker. We left the panel open for an hour "
            "to make sure nothing else was drifting, and then closed it up and "
            "went home. The next morning everything still held, which was a "
            "relief after all the trouble we had gone through the night before."
        )
        report = analyze(text)
        self.assertEqual(abstain_reasons(text, report), [])

    # -- advanced metrics (detection signals panel) -----------------------

    def test_metrics_keys_present(self):
        report = analyze(AI_HEAVY)
        self.assertEqual(
            set(report.metrics),
            {"perplexity", "burstiness", "syntactic", "coherence", "word_choice"},
        )
        # Higher is always more human-like: all scores live in 0-100 or None.
        for v in report.metrics.values():
            self.assertTrue(v is None or 0.0 <= v <= 100.0)

    def test_syntactic_metric_improves_after_rewrite(self):
        # The deterministic rewrite removes formulaic patterns, so the
        # syntactic signal (freedom from filler/cliché/hedge shapes) must
        # rise after naturalizing an AI-heavy sample.
        from naturalizer.transforms import rewrite as det_rewrite
        import random

        before = analyze(AI_HEAVY).metrics["syntactic"]
        rewritten, _, _ = det_rewrite(AI_HEAVY, rng=random.Random(0))
        after = analyze(rewritten).metrics["syntactic"]
        self.assertGreaterEqual(after, before)

    def test_perplexity_length_gated(self):
        short = analyze(AI_HEAVY).metrics["perplexity"]
        self.assertIsNone(short)  # too short for zlib to expose repetition
        long = analyze(("The team works hard on the plan and the plan works well. " * 40)).metrics["perplexity"]
        self.assertIsNotNone(long)
        self.assertTrue(0.0 <= long <= 100.0)

    def test_burstiness_length_gated(self):
        one_sentence = analyze("The plan works well for the whole team.").metrics["burstiness"]
        self.assertIsNone(one_sentence)
        # A 4-sentence text is still too short for a meaningful variance
        # signal (3-4 sentence lengths are noise, like the other length
        # gates) — burstiness only appears from 5 sentences up.
        four_sentences = analyze(
            "The first sentence here is short. "
            "A second sentence that runs a little longer than the first. "
            "The third sentence sits in the middle of the range. "
            "And a fourth sentence to round the paragraph out."
        ).metrics["burstiness"]
        self.assertIsNone(four_sentences)
        varied = analyze(
            "Short. "
            "A medium-length sentence here for contrast. "
            "And then a considerably longer one that stretches across many "
            "words to give the paragraph some rhythm and life. "
            "Then a brief one again to break the pattern. "
            "Finally, a closing sentence that runs on a little to finish."
        ).metrics["burstiness"]
        self.assertIsNotNone(varied)

    def test_coherence_length_gated(self):
        one_sentence = analyze("The plan works well for the whole team.").metrics["coherence"]
        self.assertIsNone(one_sentence)

    def test_empty_text_has_no_short_note(self):
        report = analyze("")
        self.assertNotIn("short", {i.kind for i in report.issues})

    def test_chatgpt_self_reference_tells(self):
        text = (
            "As an AI language model, I cannot assist with that request. "
            "I'm sorry, but I can't help with that either. "
            "As a language model, I do not have access to the file."
        )
        report = analyze(text)
        msgs = " ".join(i.message for i in report.issues)
        self.assertIn("as an ai", msgs.lower())
        self.assertIn("i'm sorry", msgs.lower())
        self.assertIn("as a language model", msgs.lower())

    def test_human_self_reference_idioms_not_flagged(self):
        text = (
            "I cannot wait to see the results. I'm sorry I missed your call "
            "earlier — the meeting ran long. I cannot stress enough how much "
            "this matters to the team."
        )
        report = analyze(text)
        msgs = " ".join(i.message for i in report.issues)
        self.assertNotIn("self-reference", msgs)

    # -- Wikipedia "Signs of AI writing": rule of three / staccato / aphorism

    def test_rule_of_three_detected_low_severity(self):
        text = (
            "Our platform delivers innovation, inspiration, and insights "
            "to every customer."
        )
        report = analyze(text)
        triads = [i for i in report.issues if i.kind == "triad"]
        self.assertEqual(len(triads), 1)
        self.assertEqual(triads[0].severity, "low")
        self.assertIn("rule of three", triads[0].message.lower())

    def test_concrete_triad_not_flagged(self):
        # Humans list concrete items in threes all the time — never flag.
        text = "The kitchen had flour, sugar, and eggs on the counter."
        report = analyze(text)
        self.assertNotIn("triad", {i.kind for i in report.issues})
        text2 = "Red, white, and blue flags lined the street."
        report2 = analyze(text2)
        self.assertNotIn("triad", {i.kind for i in report2.issues})

    def test_staccato_drama_detected_low_severity(self):
        text = "It had no preference. No prior. No nostalgia."
        report = analyze(text)
        staccato = [i for i in report.issues if i.kind == "staccato"]
        self.assertEqual(len(staccato), 1)
        self.assertEqual(staccato[0].severity, "low")
        self.assertIn("staccato", staccato[0].message)

    def test_isolated_short_sentence_not_flagged(self):
        # One short sentence for emphasis is normal human writing.
        text = "They come out sweet. They need to be spread apart or they steam."
        report = analyze(text)
        self.assertNotIn("staccato", {i.kind for i in report.issues})

    def test_aphorism_formula_detected_low_severity(self):
        text = "Symmetry is the language of trust."
        report = analyze(text)
        aphs = [i for i in report.issues if "aphorism" in i.message]
        self.assertEqual(len(aphs), 1)
        self.assertEqual(aphs[0].severity, "low")
        text2 = "Patience is the key to success."
        report2 = analyze(text2)
        self.assertTrue(any("aphorism" in i.message for i in report2.issues))

    def test_plain_claim_not_aphorism(self):
        # "the key to the front door" is a literal object, not a motto.
        text = "The key to the front door was in my coat pocket."
        report = analyze(text)
        self.assertFalse(any("aphorism" in i.message for i in report.issues))

    # -- antithesis scaffold + em-dash density (the tells StealthWriter-
    #    class detectors weigh; both caught the smart-home sample ours
    #    previously scored 94/100 human)

    def test_antithesis_scaffold_detected_low_severity(self):
        text = (
            "These are not treated as aspirations but as qualities to be "
            "tested against experience. Privacy concerns are not acknowledged "
            "in passing and then set aside; they are treated as substantive "
            "issues the technology has not yet resolved."
        )
        report = analyze(text)
        anti = [i for i in report.issues if i.kind == "antithesis"]
        self.assertGreaterEqual(len(anti), 2)
        self.assertTrue(all(i.severity == "low" for i in anti))
        self.assertIn("antithesis", anti[0].message.lower())

    def test_plain_speech_not_antithesis(self):
        # Natural "not X but Y" in everyday prose must never fire.
        probes = [
            "She is not tall but she is quick.",
            "It was not the first time but the third that this happened.",
            "The rule is not simple but it works in practice.",
            "They are treated as equals in every department. It was not an easy call.",
        ]
        for text in probes:
            report = analyze(text)
            self.assertNotIn(
                "antithesis", {i.kind for i in report.issues}, msg=text
            )

    def test_emdash_density_detected(self):
        # 4 dashes in 223 words (17.9 per 1000) is the tell.
        text = (
            "The report's centre of gravity — a case study tracing one "
            "family's transition — sits at the heart of the analysis, and "
            "the findings — measured and affective alike — point one way: "
            "smart homes can enrich life — but only with care."
        )
        report = analyze(text)
        dash = [i for i in report.issues if i.kind == "punctuation"]
        self.assertEqual(len(dash), 1)
        self.assertIn("per 1000", dash[0].message)

    def test_emdash_sparse_not_detected(self):
        # 4 dashes across a 2,000-word report is normal, not a tell.
        filler = (
            "The audit covered every department in turn, from procurement "
            "through logistics to the front office, and the findings were "
            "consistent across all of them. "
        )
        text = (
            "The audit opened with a summary of the year's activity. "
            + filler * 18
            + "A single closing remark — worth keeping — ended the report. "
            + filler * 18
        )
        report = analyze(text)
        self.assertNotIn(
            "punctuation", {i.kind for i in report.issues}
        )


if __name__ == "__main__":
    unittest.main()
