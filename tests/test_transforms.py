"""Tests for the deterministic rewriting transforms."""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.transforms import cut_filler, rewrite, soften_emdash, vary_openers


class CutFillerTest(unittest.TestCase):
    def test_removes_ai_phrases(self):
        text = (
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role. Furthermore, we must leverage "
            "cutting-edge solutions."
        )
        out, changed = cut_filler(text)
        self.assertTrue(changed)
        lowered = out.lower()
        self.assertNotIn("fast-paced", lowered)
        self.assertNotIn("important to note", lowered)
        self.assertNotIn("furthermore", lowered)
        self.assertNotIn("leverage", lowered)
        self.assertNotIn("cutting-edge", lowered)

    def test_meaning_survives(self):
        text = "It is important to note that the server was down for two hours."
        out, _ = cut_filler(text)
        self.assertIn("server was down", out)
        self.assertIn("two hours", out)

    def test_allowlist_preserves_terms(self):
        text = "A robust approach is crucial here."
        out, _ = cut_filler(text, allowlist={"robust", "crucial"})
        self.assertIn("robust", out)
        self.assertIn("crucial", out)

    def test_no_change_on_clean_text(self):
        clean = "The wiring came loose behind the counter, and I fixed it in ten minutes."
        out, changed = cut_filler(clean)
        self.assertFalse(changed)
        self.assertEqual(out, clean)

    def test_hedge_removal_fixes_capitalization(self):
        out, changed = cut_filler("It is clear that the plan worked.")
        self.assertTrue(changed)
        self.assertEqual(out, "The plan worked.")

    def test_institutional_scaffolding_swapped(self):
        out, changed = cut_filler(
            "Our analysis focused on three values. We first sought to define "
            "the concept. We were also asked to document our emotions. This "
            "assignment required us to prepare a report. The system had been "
            "successfully implemented."
        )
        self.assertTrue(changed)
        lowered = out.lower()
        self.assertIn("we focused on", lowered)
        self.assertIn("we started by trying to", lowered)
        self.assertIn("we also had to", lowered)
        self.assertIn("the assignment asked us to", lowered)
        self.assertIn("had been put into practice", lowered)
        self.assertNotIn("our analysis focused", lowered)
        self.assertNotIn("must also consider", lowered)

    def test_negative_parallelism_scaffold_flattened(self):
        from naturalizer.transforms import de_scaffold_negative_parallelism, split_sentences

        text = (
            "Adaptability, in this sense, is not merely a practical asset "
            "but a condition of meaningful participation in digital life."
        )
        out, did = de_scaffold_negative_parallelism(split_sentences(text))
        self.assertTrue(did)
        self.assertEqual(out[0], "Adaptability, in this sense, is both a practical asset and a condition of meaningful participation in digital life.")

    def test_negative_parallelism_but_also_collapsed(self):
        from naturalizer.transforms import de_scaffold_negative_parallelism, split_sentences

        out, did = de_scaffold_negative_parallelism(
            split_sentences("It is not just helpful but also essential.")
        )
        self.assertTrue(did)
        self.assertEqual(out[0], "It is both helpful and essential.")

    def test_negative_parallelism_capitalizes_sentence_start(self):
        from naturalizer.transforms import de_scaffold_negative_parallelism, split_sentences

        out, did = de_scaffold_negative_parallelism(
            split_sentences("Not merely a warning, the sign is a useful guide but not a rule.")
        )
        # No "not merely" scaffold with a trailing "but" side here, so the
        # plain sentence is left alone.
        self.assertFalse(did)
        self.assertEqual(out[0], "Not merely a warning, the sign is a useful guide but not a rule.")

    def test_negative_parallelism_never_fires_on_plain_speech(self):
        from naturalizer.transforms import de_scaffold_negative_parallelism, split_sentences

        plain = (
            "She is not tall but she is quick, and that is what matters. "
            "He did not call but he did send a note."
        )
        out, did = de_scaffold_negative_parallelism(split_sentences(plain))
        self.assertFalse(did)
        self.assertEqual(out, split_sentences(plain))

    def test_removal_keeps_structural_colon(self):
        out, changed = cut_filler(
            "Our analysis focused on three values in the broad sense of the "
            "term: convenience, security, and sustainability."
        )
        self.assertTrue(changed)
        self.assertIn("values: convenience", out)
        self.assertNotIn("broad sense", out)

    def test_removal_tidies_orphaned_parens(self):
        out, changed = cut_filler(
            "three values in a smart home (in the broad sense of the term) "
            "- convenience"
        )
        self.assertTrue(changed)
        self.assertIn("smart home - convenience", out)
        self.assertNotIn("(", out)
        self.assertNotIn("  ", out)
        # Non-empty parens are never touched.
        out2, _ = cut_filler("This (it should be noted) is fine.")
        self.assertIn("(it should be noted)", out2)

    def test_clause_fronting_restructures_but_keeps_meaning(self):
        from naturalizer.transforms import front_subordinate_clauses, split_sentences
        import random

        text = (
            "The system performs consistently well in production because it uses a "
            "simple architecture that anyone can maintain. Users strongly prefer "
            "the updated tool since it saves them several hours each week. The "
            "whole team adopted the workflow although it required a short learning "
            "period. Results improved when the team applied the new process "
            "consistently."
        )
        sentences = split_sentences(text)
        fired = False
        for seed in range(12):
            out, did = front_subordinate_clauses(sentences, random.Random(seed), intensity=1.0)
            if did:
                fired = True
                self.assertNotEqual(out[1], sentences[1])  # since-clause fronted
                self.assertIn("since it saves them", out[1].lower())
                self.assertTrue(out[1].startswith("Since"))
                break
        self.assertTrue(fired)

    def test_clause_fronting_low_intensity_is_silent(self):
        from naturalizer.transforms import front_subordinate_clauses, split_sentences
        import random

        text = (
            "The system performs consistently well in production because it uses a "
            "simple architecture. Users strongly prefer the updated tool since it "
            "saves them hours each week. The whole team adopted the workflow "
            "although it required a learning period. Results improved when the team "
            "applied the process consistently."
        )
        out, did = front_subordinate_clauses(split_sentences(text), random.Random(0), intensity=0.5)
        self.assertFalse(did)
        self.assertEqual(out, split_sentences(text))

    def test_clause_fronting_skips_fragments_and_short_heads(self):
        from naturalizer.transforms import front_subordinate_clauses, split_sentences
        import random

        # "because tired" is a fragment (no subject); short heads read awkward
        # when fronted — neither may fire.
        text = (
            "We left early because tired after the long drive. The bus was late "
            "because the snow came down hard overnight. The driver called ahead "
            "because the road was completely blocked. The station staff rerouted "
            "us because the platform had flooded."
        )
        out, did = front_subordinate_clauses(split_sentences(text), random.Random(2), intensity=1.0)
        # First sentence has a fragment tail (skipped); others may legitimately fire.
        self.assertFalse(out[0].startswith("Because tired"))

    def test_worth_mentioning_removed(self):
        out, changed = cut_filler(
            "It's worth mentioning that the server was down for two hours."
        )
        self.assertTrue(changed)
        self.assertEqual(out, "The server was down for two hours.")

    def test_as_a_result_versus_as_a_result_of(self):
        out, _ = cut_filler(
            "As a result of the storm, the power went out. "
            "As a result, the office closed."
        )
        self.assertIn("Because of the storm", out)
        self.assertIn("So, the office closed", out)
        self.assertNotIn("so of", out)

    def test_cliches_replaced(self):
        out, changed = cut_filler(
            "The tool is a double-edged sword, but the plan has low-hanging fruit."
        )
        self.assertTrue(changed)
        self.assertIn("has trade-offs", out)
        self.assertIn("easy wins", out)

    def test_at_end_of_day_replaced(self):
        out, _ = cut_filler("At the end of the day, we shipped.")
        self.assertIn("In the end, we shipped", out)

    def test_second_expansion_wordy_phrases(self):
        out, changed = cut_filler(
            "Due to the fact that demand rose, prices climbed. "
            "In the event that the trend holds, we will expand. "
            "As to whether the plan works, time will tell. "
            "With respect to the budget, we are fine. "
            "At this point in time, it looks good."
        )
        self.assertTrue(changed)
        self.assertIn("Because demand rose", out)
        self.assertIn("If the trend holds", out)
        self.assertIn("Whether the plan works", out)
        self.assertIn("About the budget", out)
        self.assertIn("Now, it looks good", out)

    def test_second_expansion_hedges_removed(self):
        out, changed = cut_filler(
            "As we all know, the deadline moved. "
            "It is imperative to review the contract now. "
            "It bears mentioning that the client has not signed off."
        )
        self.assertTrue(changed)
        self.assertNotIn("as we all know", out.lower())
        self.assertNotIn("imperative", out.lower())
        self.assertNotIn("bears mentioning", out.lower())
        self.assertIn("The deadline moved", out)
        self.assertIn("We must review the contract now", out)

    def test_second_expansion_cliches_and_swaps(self):
        out, changed = cut_filler(
            "The tool plays a key role in the workflow. "
            "In a nutshell, the modern-day platform fosters collaboration, "
            "and the team is finally on the same page."
        )
        self.assertTrue(changed)
        self.assertIn("is central to the workflow", out)
        self.assertIn("In short", out)
        self.assertIn("modern platform", out)
        self.assertIn("encourages collaboration", out)

    def test_third_expansion_cliches_swapped(self):
        out, changed = cut_filler(
            "The new hire hit the ground running and raised the bar for the "
            "unit. She paved the way for a much simpler process. The steep "
            "learning curve was a necessary evil, but at the heart of the "
            "rollout sat a solid plan. In a nutshell, the silver lining is "
            "real, and the team is finally on the same page."
        )
        self.assertTrue(changed)
        self.assertIn("set a higher standard", out)
        self.assertIn("helped create", out)
        self.assertIn("tough learning period", out)
        self.assertIn("at the core of", out)
        self.assertIn("the upside is real", out)
        # Ambiguous/human forms are left alone (detector-only).
        self.assertIn("hit the ground running", out)
        self.assertIn("necessary evil", out)
        self.assertIn("on the same page", out)

    def test_third_expansion_corporate_ticks_swapped(self):
        out, changed = cut_filler(
            "Let's circle back on the deliverables and touch base with the "
            "stakeholders before we drill down into the numbers. The synergy "
            "is actionable. Clearly, this approach will move the needle on "
            "engagement."
        )
        self.assertTrue(changed)
        self.assertIn("revisit the deliverables", out)
        self.assertIn("check in with the stakeholders", out)
        self.assertIn("dig into the numbers", out)
        self.assertIn("combined effort", out)
        self.assertIn("useful", out)
        self.assertIn("make a difference on engagement", out)

    def test_silver_lining_articles_are_grammatical(self):
        out, changed = cut_filler(
            "Every cloud has a silver lining. The silver lining is that it "
            "rained last night."
        )
        self.assertTrue(changed)
        self.assertIn("has an upside", out)
        self.assertIn("The upside is", out)
        self.assertNotIn("a upside", out)

    def test_necessary_evil_and_bandwidth_preserved(self):
        out, changed = cut_filler(
            "It felt like a necessary evil. The uplink needs more bandwidth "
            "for the nightly backup."
        )
        self.assertFalse(changed)
        self.assertIn("a necessary evil", out)
        self.assertIn("bandwidth for", out)

    def test_present_tense_hit_the_ground_running_preserved(self):
        out, changed = cut_filler(
            "Every January the new interns hit the ground running."
        )
        self.assertFalse(changed)
        self.assertIn("hit the ground running", out)

    def test_moving_forward_as_sentence_adverb_preserved(self):
        out, changed = cut_filler(
            "Moving forward, we should check the inventory weekly."
        )
        self.assertFalse(changed)
        self.assertIn("Moving forward,", out)

    def test_on_the_same_page_preserved(self):
        out, changed = cut_filler(
            "We got everyone on the same page about the launch date."
        )
        self.assertFalse(changed)
        self.assertIn("on the same page", out)

    def test_in_turn_swapped_only_as_discourse_marker(self):
        out, changed = cut_filler(
            "In turn, costs climbed. We each spoke in turn during the meeting."
        )
        self.assertTrue(changed)
        self.assertIn("Then, costs climbed", out)
        self.assertIn("spoke in turn", out)  # literal sense untouched

    def test_as_such_swapped_only_outside_parenthetical(self):
        out, changed = cut_filler(
            "Costs climbed, and as such the margins narrowed. "
            "The plan, as such, was approved as written."
        )
        self.assertTrue(changed)
        self.assertIn("and so the margins", out)
        self.assertIn("plan, as such,", out)  # formal sense untouched

    def test_to_that_end_and_moving_forward_swapped(self):
        out, changed = cut_filler(
            "To that end, the plan is to scale gradually. "
            "Moving forward is the only option."
        )
        self.assertTrue(changed)
        self.assertIn("For that reason, the plan", out)
        self.assertIn("Pushing ahead is the only option", out)

    def test_speech_hedges_swapped(self):
        out, changed = cut_filler(
            "Let's be honest: more often than not, these surprises are "
            "avoidable. For what it's worth, the delay gave us room to "
            "double-check. One cannot ignore the risk of scope creep."
        )
        self.assertTrue(changed)
        self.assertNotIn("let's be honest", out.lower())
        self.assertNotIn("more often than not", out.lower())
        self.assertNotIn("for what it's worth", out.lower())
        self.assertNotIn("one cannot", out.lower())
        self.assertIn("Most of the time", out)
        self.assertIn("We cannot ignore", out)
        self.assertNotIn(", ", out.lstrip()[:2])  # no leading comma seam


class VaryOpenersTest(unittest.TestCase):
    def test_repetitive_transitional_openers_are_varied(self):
        import random

        rng = random.Random(42)
        sentences = [
            "However, the results were mixed.",
            "However, the sample was small.",
            "Furthermore, the data was noisy.",
            "However, the trend was clear.",
        ]
        out, changed = vary_openers(sentences, rng)
        self.assertTrue(changed)
        starts = {s.split()[0].lower() for s in out}
        self.assertGreater(len(starts), 1)


class SoftenEmdashTest(unittest.TestCase):
    def test_replaces_emdashes(self):
        import random

        rng = random.Random(1)
        text = "The tool — a small utility — does one thing well."
        out, changed = soften_emdash(text, rng)
        self.assertTrue(changed)
        self.assertNotIn("—", out)
        self.assertIn("a small utility", out)


class RewriteTest(unittest.TestCase):
    def test_pipeline_runs_and_returns_text(self):
        text = (
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role. Furthermore, the ever-evolving "
            "landscape of digital tools transforms the way we work. Moreover, "
            "it is essential to highlight that organizations must leverage "
            "cutting-edge solutions. Additionally, the realm of artificial "
            "intelligence offers a plethora of opportunities. In conclusion, "
            "it is important to remember that navigating the complexities of "
            "modern technology requires a robust approach. Overall, the "
            "journey toward digital transformation underscores the paramount "
            "importance of adaptability."
        )
        out, sentences, changed = rewrite(text)
        self.assertTrue(changed)
        self.assertTrue(out)
        self.assertGreaterEqual(len(sentences), 1)
        # No empty sentences may survive.
        self.assertTrue(all(s.strip() for s in sentences))
        # Core content words still present.
        self.assertIn("technology", out)
        self.assertIn("adaptability", out)

    def test_clean_text_is_left_alone(self):
        clean = (
            "I got the new coffee machine set up this morning, and it turns "
            "out the old one wasn't broken after all. The wiring had come "
            "loose behind the counter. A quick screwdriver job fixed it in "
            "ten minutes, and now the office smells like a café. Colleagues "
            "keep wandering over, cup in hand, hoping I'll brew another pot."
        )
        out, _, changed = rewrite(clean)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), clean)

    def test_deterministic_for_same_seed(self):
        text = "Furthermore, the data was noisy. However, the trend was clear."
        a, _, _ = rewrite(text, rng=__import__("random").Random(7))
        b, _, _ = rewrite(text, rng=__import__("random").Random(7))
        self.assertEqual(a, b)

    def test_merge_never_leaves_period_comma_seam(self):
        out, _, changed = rewrite(
            "The data was noisy. To that end, we retried the experiment. "
            "The sample was small. The trend was clear. The room was quiet."
        )
        self.assertTrue(changed)
        self.assertNotIn("., and", out)
        self.assertNotIn(". ,", out)
        # The discourse-marker sentence survives as its own sentence.
        self.assertIn("For that reason,", out)

    def test_drops_ai_self_reference_opener_cleanly(self):
        out, _, changed = rewrite(
            "As an AI language model, I cannot access the file directly, but "
            "I can summarize it. As a language model, I do not have opinions."
        )
        self.assertTrue(changed)
        # No stray leading comma, no leftover announcement.
        self.assertNotIn(", I cannot", out)
        self.assertNotIn(", I do not", out)
        self.assertNotIn("as an ai", out.lower())
        self.assertNotIn("language model", out.lower())
        # The inability itself is humanized but kept.
        self.assertIn("I can't access", out)

    def test_drops_mid_sentence_ai_announcement(self):
        out, _, changed = rewrite(
            "The plan is solid, and as an AI I can tell you without a doubt "
            "that the numbers check out."
        )
        self.assertTrue(changed)
        self.assertNotIn("as an ai", out.lower())
        self.assertIn("I can tell you clearly", out)

    def test_humanizes_apology_inability(self):
        out, _, changed = rewrite(
            "I'm sorry, but I cannot share the full roadmap at this point in "
            "time."
        )
        self.assertTrue(changed)
        self.assertNotIn("I'm sorry", out)
        self.assertIn("I can't share", out)
        self.assertNotIn("at this point in time", out)

    def test_bare_cannot_verb_humanized(self):
        out, _, changed = rewrite("I cannot access the file from here.")
        self.assertTrue(changed)
        self.assertIn("I can't access", out)

    def test_human_cannot_idioms_untouched(self):
        out, _, changed = rewrite(
            "I cannot wait for the results, and I cannot stress how much this "
            "matters to the team."
        )
        self.assertFalse(changed)
        self.assertIn("I cannot wait", out)
        self.assertIn("I cannot stress", out)

    # -- intensity & variety -------------------------------------------

    def test_default_intensity_reproduces_legacy_behavior(self):
        text = (
            "It is important to note that the results were clear. "
            "Furthermore, we are planning to move quickly."
        )
        legacy, _, _ = rewrite(text, rng=random.Random(0))
        same, _, _ = rewrite(text, rng=random.Random(0), intensity=0.5)
        self.assertEqual(legacy, same)
        # At the default intensity the variety passes stay silent.
        self.assertNotIn("we're", same)

    def test_high_intensity_varies_synonyms_across_seeds(self):
        text = (
            "This is an important decision and a major step. The change is "
            "huge for the team, and the path forward is clear. It is easy to "
            "see why people care about the result."
        )
        # Scan a few seeds for one where a synonym actually swapped.
        swapped = False
        for seed in range(25):
            out, _, _ = rewrite(text, rng=random.Random(seed), intensity=1.0)
            if "important" not in out:
                swapped = True
                break
        self.assertTrue(swapped, "high intensity should vary 'important' for some seed")

    def test_different_seeds_produce_different_high_intensity_output(self):
        text = (
            "It is important to note that the results are clear and easy to "
            "read. Furthermore, the major change is important for the team."
        )
        outputs = {}
        for seed in range(6):
            out, _, _ = rewrite(text, rng=random.Random(seed), intensity=1.0)
            outputs[out] = True
        self.assertGreater(len(outputs), 1, "different seeds should vary the prose")

    def test_contractions_apply_only_when_enabled(self):
        text = "We are planning to move, and they are ready. It is a simple fix."
        # Disabled: never contracts, even at full intensity.
        out, _, _ = rewrite(text, rng=random.Random(2), intensity=1.0, contractions=False)
        self.assertNotIn("we're", out)
        self.assertNotIn("they're", out)
        self.assertNotIn("it's", out)
        # Enabled: at least one contraction appears somewhere.
        contracted = False
        for seed in range(25):
            out, _, _ = rewrite(text, rng=random.Random(seed), intensity=1.0, contractions=True)
            if any(w in out for w in ("we're", "they're", "it's")):
                contracted = True
                break
        self.assertTrue(contracted, "contractions should fire for some seed")


if __name__ == "__main__":
    unittest.main()
