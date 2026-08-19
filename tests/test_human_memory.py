"""Tests for the verified human-writing memory and the plain-language pass."""

import re
import sys
import unittest

sys.path.insert(0, ".")

from naturalizer import human_memory as hm
from naturalizer import transforms


class HumanMemoryTest(unittest.TestCase):
    def test_corpus_loads_verified_human_paragraphs(self):
        paras = hm.load_corpus()
        self.assertGreaterEqual(len(paras), 15)
        for p in paras:
            self.assertGreater(len(p.split()), 15)  # real paragraphs, not lines

    def test_everyday_vocabulary_built_from_corpus(self):
        vocab, freq = hm.everyday_vocabulary()
        self.assertGreaterEqual(len(vocab), 300)
        self.assertIn("the", vocab)
        self.assertGreater(freq["the"], 10)

    def test_no_swap_source_appears_in_human_corpus(self):
        # The critical invariant: a demotion must never be able to touch
        # natural human prose. If a swap source appears in the verified
        # corpus, it is human by evidence and must be removed from the table.
        hits = hm.verify_against_corpus()
        self.assertEqual(hits, [])

    def test_swap_table_is_clean(self):
        keys = list(hm.PLAIN_SWAPS)
        self.assertGreaterEqual(len(keys), 200)
        self.assertEqual(len(set(keys)), len(keys))  # no duplicate keys
        for source, plain in hm.PLAIN_SWAPS.items():
            self.assertTrue(source and plain)
            self.assertNotEqual(source, plain)

    def test_plain_register_score_high_on_corpus_low_on_stiff(self):
        corpus = " ".join(hm.load_corpus())
        self.assertGreater(hm.plain_register_score(corpus), 0.95)
        stiff = (
            "The utilization of this framework will subsequently facilitate "
            "the commencement of our endeavor."
        )
        # Stiff Latinate prose must score well below everyday prose, and
        # far below the corpus itself (which is the floor for plainness).
        self.assertLess(hm.plain_register_score(stiff), 0.7)
        self.assertLess(
            hm.plain_register_score(stiff),
            hm.plain_register_score(corpus) - 0.2,
        )

    def test_plain_register_guidance_names_draft_words(self):
        stiff = (
            "The utilization of this approach will subsequently facilitate "
            "the commencement of our work."
        )
        guidance = hm.plain_register_guidance(stiff)
        self.assertIsNotNone(guidance)
        self.assertIn("utilization", guidance)
        self.assertIn("-> use", guidance)
        self.assertIn("subsequently", guidance)
        # A plain draft gets no guidance noise.
        self.assertIsNone(
            hm.plain_register_guidance(
                "I sorted the garage on Saturday and found three boxes of old cables."
            )
        )


class PlainRegisterPassTest(unittest.TestCase):
    def test_demotes_stiff_words(self):
        out, changed = transforms.simplify_register(
            "The utilization of this framework will subsequently facilitate "
            "the commencement of our work."
        )
        self.assertTrue(changed)
        self.assertIn("use", out)
        self.assertNotIn("utilization", out)
        self.assertIn("then", out)
        self.assertNotIn("subsequently", out)
        self.assertIn("help", out)
        self.assertIn("start", out)
        self.assertNotIn("commencement", out)

    def test_never_fires_on_human_corpus(self):
        hum = open("tests/corpus/human_samples.txt", encoding="utf-8").read()
        paras = [p.strip() for p in re.split(r"\n\s*\n", hum) if p.strip()]
        fires = 0
        for p in paras:
            _, did = transforms.simplify_register(p)
            fires += int(did)
        self.assertEqual(fires, 0)

    def test_preserves_capitalization(self):
        out, changed = transforms.simplify_register(
            "Utilize the tools. Commence the review."
        )
        self.assertTrue(changed)
        self.assertIn("Use the tools", out)
        self.assertIn("Start the review", out)

    def test_plain_words_untouched(self):
        out, changed = transforms.simplify_register(
            "I use simple words and start things on time. Help is welcome."
        )
        self.assertFalse(changed)
        self.assertEqual(
            out, "I use simple words and start things on time. Help is welcome."
        )

    def test_allowlist_protects_register_idiom(self):
        # Business style's allowlist protects "key takeaways" even though
        # the plain-word swap would otherwise demote "takeaways".
        out, changed = transforms.simplify_register(
            "The key takeaways from the call are on the shared drive.",
            allowlist={"key takeaways", "key takeaway"},
        )
        self.assertFalse(changed)
        self.assertIn("key takeaways", out)
        # Without the allowlist the swap fires.
        out2, changed2 = transforms.simplify_register(
            "The key takeaways from the call are on the shared drive."
        )
        self.assertTrue(changed2)

    def test_merge_preserves_capital_i(self):
        # A sentence merge must never corrupt "I" -> "i".
        self.assertEqual(transforms._merge_cap("I flip them once"), "I flip them once")
        self.assertEqual(transforms._merge_cap("They steam instead"), "they steam instead")

    def test_merge_never_swallows_a_question(self):
        # A question can't be grafted onto a declarative, and a declarative
        # can't be grafted onto a question.
        self.assertFalse(
            transforms._mergeable_pair("I booked Wednesday at two.", "Want to join?")
        )
        self.assertFalse(
            transforms._mergeable_pair("Can you check with the landlord?", "Remind me to grab")
        )
        self.assertTrue(
            transforms._mergeable_pair("They came out sweet.", "I flipped them once")
        )


if __name__ == "__main__":
    unittest.main()
