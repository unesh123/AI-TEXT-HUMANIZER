"""AI-tell detection and naturalness scoring.

The detector scans prose for the linguistic fingerprints common in
machine-generated text and produces a 0-100 naturalness score plus a list
of concrete, actionable issues. Everything here is deterministic and pure,
so it is fully testable and runs with no external dependencies.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import Dict, List, Optional, Tuple

from .wordfreq import COMMON_5000, LOGP


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

#: Phrases and words that are over-represented in LLM output. Each entry maps
#: a regex pattern to a human-readable label, a suggested replacement, a
#: category (kind), and an optional severity override. When the severity is
#: ``None`` it is derived automatically: multi-word phrases are "high", single
#: words are "medium".
#:
#: Tuple shape: (regex, label, suggestion, kind, severity|None)
AI_FILLERS: List[Tuple[str, str, str, str, Optional[str]]] = [
    # --- filler: vague intensifiers and corporate/marketing buzzwords -------
    (r"\bin today'?s (fast-paced |ever-evolving )?world\b", "cliché opener", '"these days" or just drop it', "filler", None),
    (r"\bin today'?s digital age\b", "cliché opener", "drop or be specific", "filler", None),
    (r"\bdelve(s|d|ing)?( into)?\b", "AI buzzword", '"explores", "digs into", or "looks at"', "filler", None),
    (r"\bleverage\b", "corporate filler", '"use", "build on", or "put to work"', "filler", None),
    (r"\butilize(s|d)?\b", "corporate filler", '"use"', "filler", None),
    (r"\bunlock(s|ed|ing)?\b", "marketing buzzword", '"enable", "open up", or "tap into"', "filler", None),
    (r"\bseamless(ly)?\b", "marketing buzzword", '"smooth", "easy", or "without friction"', "filler", None),
    (r"\brobust\b", "vague adjective", '"solid", "reliable", or "strong"', "filler", None),
    (r"\bholistic\b", "vague adjective", "be specific about scope", "filler", None),
    (r"\bcomprehensive\b", "vague adjective", '"thorough", "wide-ranging", or name the scope', "filler", None),
    (r"\bcutting-edge\b", "marketing buzzword", '"current", "modern", or "recent"', "filler", None),
    (r"\bstate-of-the-art\b", "marketing buzzword", '"current", "modern", or "leading"', "filler", None),
    (r"\bplethora\b", "stilted word", '"range", "variety", or "many"', "filler", None),
    (r"\bmyriad\b", "stilted word", '"many", "countless", or "dozens of"', "filler", None),
    (r"\bmultifaceted\b", "vague adjective", "be specific about the sides/parts", "filler", None),
    (r"\belevate(s|d)?\b", "marketing buzzword", '"improve", "raise", or "boost"', "filler", None),
    (r"\bessentially\b", "filler word", '"basically", "in practice", or drop', "filler", None),
    (r"\bnotably\b", "filler word", "be specific or drop", "filler", None),
    (r"\bimportantly\b", "filler word", '"most of all" or drop', "filler", None),
    (r"\bin essence\b", "filler word", "drop", "filler", None),
    (r"\bsignificantly\b", "vague intensifier", '"markedly", "notably", or be specific', "filler", None),
    (r"\bcrucial(ly)?\b", "vague intensifier", '"key", "vital", or be specific', "filler", None),

    # --- cliché: dead metaphors and overused figurative language -------------
    (r"\bin the (ever-)?evolving landscape of\b", "cliché metaphor", '"in X" or drop', "cliche", None),
    (r"\blandscape\b", "cliché metaphor", "be specific: 'markets', 'field', or 'space'", "cliche", None),
    (r"\btapestry\b", "cliché metaphor", '"mix", "blend", or "range"', "cliche", None),
    (r"\btestament to\b", "cliché metaphor", '"shows", "reflects", or "is proof of"', "cliche", None),
    (r"\bunderscore(s|d)?\b", "cliché metaphor", '"highlights", "shows", or "stresses"', "cliche", None),
    (r"\bparamount\b", "cliché metaphor", '"essential", "central", or "critical"', "cliche", None),
    (r"\bpivotal\b", "cliché metaphor", '"key", "central", or "decisive"', "cliche", None),
    (r"\bgame-?changer\b", "cliché metaphor", '"major shift", "breakthrough", or be specific', "cliche", None),
    (r"\bnavigate(s|d)? the (complexities|challenges|intricacies|nuances|terrain|maze|waters|landscape|world) of\b", "cliché metaphor", '"handle", "work through", or "deal with"', "cliche", None),
    (r"\bjourney\b", "cliché metaphor", "be concrete about the process", "cliche", None),
    (r"\brealm of\b", "cliché metaphor", '"field of", "world of", or drop', "cliche", None),
    (r"\bever-?evolving\b", "cliché metaphor", '"changing", "shifting", or "in flux"', "cliche", None),
    (r"\bis a double-edged sword\b", "cliché metaphor", '"has both benefits and drawbacks"', "cliche", None),
    (r"\bdouble-edged sword\b", "cliché metaphor", '"has trade-offs" or be concrete', "cliche", None),
    (r"\btip of the iceberg\b", "cliché metaphor", '"the beginning" or be concrete', "cliche", None),
    (r"\belephant in the room\b", "cliché metaphor", '"the obvious issue"', "cliche", None),
    (r"\bsilver bullet\b", "cliché metaphor", '"a perfect solution"', "cliche", None),
    (r"\bpanacea\b", "cliché metaphor", '"a cure-all" or be concrete', "cliche", "medium"),
    (r"\blow-hanging fruit\b", "cliché metaphor", '"easy wins"', "cliche", None),
    (r"\bthink outside the box\b", "cliché metaphor", '"think creatively"', "cliche", None),
    (r"\bparadigm shift\b", "cliché metaphor", '"fundamental change"', "cliche", None),
    (r"\bat the end of the day\b", "cliché metaphor", '"in the end" or drop', "cliche", "medium"),
    (r"\bboil the ocean\b", "cliché metaphor", '"try to do everything at once"', "cliche", None),
    (r"\bslippery slope\b", "cliché metaphor", "name the actual risk instead", "cliche", "medium"),
    (r"\bband-aid\b", "cliché metaphor", '"stopgap" or "temporary fix"', "cliche", "medium"),
    (r"\bcornerstone\b", "cliché metaphor", '"foundation"', "cliche", "medium"),
    (r"\bbackbone\b", "cliché metaphor", '"core"', "cliche", "medium"),
    (r"\bmove the needle\b", "cliché metaphor", '"make a real difference"', "cliche", None),
    (r"\btransformative\b", "vague adjective", "be specific about the change", "cliche", "medium"),
    (r"\bgroundbreaking\b", "vague adjective", '"major", "important", or be specific', "cliche", "medium"),
    (r"\bunprecedented\b", "vague adjective", "be specific or drop", "cliche", "medium"),

    # --- hedge: empty throat-clearing that weakens the sentence -------------- 
    (r"\bit is (important|worth|essential|crucial) to (note|mention|remember|highlight) that\b",
     "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit'?s (important|worth|essential|crucial) to (note|mention|remember|highlight) that\b",
     "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit is (worth|important|essential|crucial) (mentioning|emphasizing) that\b",
     "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit'?s worth (mentioning|emphasizing|noting) that\b",
     "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit goes without saying that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit is no secret that\b", "hedging filler", "cut it — state the fact directly", "hedge", "medium"),
    (r"\bit cannot be overstated that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit should be noted that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit is safe to say that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit can be seen that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit is clear that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit is evident that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bthere is no doubt that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bneedless to say\b", "hedging filler", "cut it or say the thing", "hedge", "medium"),
    (r"\bto be honest\b", "conversational filler", "cut it or be specific", "hedge", "medium"),
    (r"\btruth be told\b", "conversational filler", "cut it or be specific", "hedge", "medium"),
    (r"\bwhen it comes to\b", "weak framing", '"regarding", "for", or restructure', "hedge", "medium"),
    (r"\bin terms of\b", "weak framing", "restructure to name the subject directly", "hedge", "medium"),

    # --- transition: formulaic connectors that machine text overuses ---------
    (r"\bin conclusion\b", "formulaic transition", '"taken together", "all told", or "in the end"', "transition", None),
    (r"\bto sum up\b", "formulaic transition", '"in short" or drop', "transition", None),
    (r"\bin summary\b", "formulaic transition", '"in short", "all told", or drop', "transition", None),
    (r"\bfurthermore\b", "formulaic transition", '"beyond that", "what is more", or "also"', "transition", None),
    (r"\bmoreover\b", "formulaic transition", '"besides", "on top of that", or "also"', "transition", None),
    (r"\badditionally\b", "formulaic transition", '"also", "on top of that", or "plus"', "transition", None),
    (r"\boverall\b", "formulaic transition", '"on the whole", "all told", or drop', "transition", None),
    (r"\bultimately\b", "formulaic transition", '"in the end" or drop', "transition", None),
    (r"\bthat being said\b", "formulaic transition", '"even so" or restructure', "transition", "medium"),
    (r"\bhaving said that\b", "formulaic transition", '"even so" or restructure', "transition", "medium"),
    (r"\bin other words\b", "formulaic transition", '"put differently" or drop', "transition", "medium"),
    (r"\bas a result\b", "formulaic transition", '"so", "that is why", or drop', "transition", "medium"),
    (r"\bconsequently\b", "formulaic transition", '"so", "as a result", or drop', "transition", "medium"),
    (r"\bin addition to\b", "formulaic transition", '"besides" or restructure', "transition", "medium"),
    (r"\bin recent years\b", "formulaic opener", '"recently" or name the timeframe', "transition", "medium"),
    (r"\bin the modern era\b", "formulaic opener", '"these days" or be specific', "transition", "medium"),
    (r"\bwith the advent of\b", "formulaic opener", '"since X arrived" or be specific', "transition", "medium"),

    # --- formulaic: structures and tics that scream template ------------------
    (r"\bnot only\b.{0,120}?\bbut also\b", "formulaic construction", "drop the scaffold; state the point plainly", "formulaic", "medium"),
    (r"\bone of the most\b", "formulaic opener", "name the thing directly", "formulaic", "medium"),
    (r"\bin the age of\b", "formulaic opener", '"in the era of" or be specific', "formulaic", "medium"),
    (r"\bas we move forward\b", "formulaic opener", "drop or be specific", "formulaic", "medium"),
    (r"\bdeep dive into\b", "corporate jargon", '"close look at"', "formulaic", "medium"),
    (r"\bcircle back\b", "corporate jargon", '"follow up" or "get back to"', "formulaic", "medium"),
    (r"\btouch base\b", "corporate jargon", '"check in"', "formulaic", "medium"),
    (r"\bdrill down\b", "corporate jargon", '"dig into" or "look closer at"', "formulaic", "medium"),
    (r"\bactionable\b", "corporate jargon", '"practical" or "ready to use"', "formulaic", "medium"),
    (r"\baforementioned\b", "stilted word", '"mentioned earlier" or drop', "formulaic", "medium"),
    (r"\ba wide range of\b", "vague quantifier", '"many" or be specific', "formulaic", "low"),
    (r"\ba variety of\b", "vague quantifier", '"many" or be specific', "formulaic", "low"),
    (r"\ba number of\b", "vague quantifier", '"several" or be specific', "formulaic", "low"),
    (r"\bnumerous\b", "vague quantifier", '"many"', "formulaic", "low"),
    (r"\bundoubtedly\b", "overclaiming adverb", '"clearly" or drop', "formulaic", "medium"),

    # --- second expansion: clichés / dead metaphors --------------------------
    (r"\bhit the ground running\b", "cliché metaphor", '"start strong" or "got going right away"', "cliche", "medium"),
    (r"\bshed(s|ding)? light on\b", "cliché metaphor", '"clarify" or "explain"', "cliche", "medium"),
    (r"\bpave the way for\b", "cliché metaphor", '"make possible"', "cliche", "medium"),
    (r"\bat the heart of\b", "cliché metaphor", '"central to"', "cliche", "medium"),
    (r"\braise the bar\b", "cliché metaphor", '"set a higher standard"', "cliche", "medium"),
    (r"\blevel the playing field\b", "cliché metaphor", '"make things fairer"', "cliche", "medium"),
    (r"\bsteep learning curve\b", "cliché metaphor", '"hard to get started with"', "cliche", "medium"),
    (r"\bin a nutshell\b", "cliché metaphor", '"in short"', "cliche", "medium"),
    (r"\bsilver lining\b", "cliché metaphor", '"the upside"', "cliche", "medium"),
    (r"\bholy grail\b", "cliché metaphor", '"the ideal solution"', "cliche", "medium"),
    (r"\bbread and butter\b", "cliché metaphor", '"mainstay"', "cliche", "medium"),
    (r"\bahead of the curve\b", "cliché metaphor", '"leading"', "cliche", "medium"),
    (r"\bin the pipeline\b", "cliché metaphor", '"on the way"', "cliche", "medium"),
    (r"\bstepping stone\b", "cliché metaphor", '"a first step"', "cliche", "medium"),
    (r"\bgame plan\b", "cliché metaphor", '"plan"', "cliche", "medium"),
    (r"\bon the same page\b", "cliché metaphor", '"in agreement"', "cliche", "medium"),
    (r"\bgame-changing\b", "cliché metaphor", '"major" or be specific', "cliche", "medium"),
    (r"\b(?:lies?|sits?|falls?)\s+(?:at|in)\s+the (?:intersection|crossroads) of\b",
     "cliché metaphor", '"combines", "brings together", or restructure', "cliche", "low"),

    # --- residual LLM tells: meta phrases, ritual verbs, stock personas -------
    (r"\bin the broad(?:est)? sense of the term\b", "meta phrase",
     "cut it — say what you actually mean", "hedge", "low"),
    (r"\bcritically analys(?:ed|e|es|ing)\b", "ritual academic phrase",
     '"looked closely at" or "examined carefully"', "formulaic", "low"),
    (r"\bdiscerning users\b", "stock persona",
     '"thoughtful users" or name the people', "formulaic", "low"),
    (r"\bour analysis focused on\b", "ritual academic opener",
     '"we focused on" — say who is doing the looking', "formulaic", "low"),
    (r"\bour analysis focuses on\b", "ritual academic opener",
     '"we focus on" — say who is doing the looking', "formulaic", "low"),
    (r"\bmust also consider\b", "stock modal construction",
     '"also need to think about" or restructure', "formulaic", "low"),
    (r"\bintended and unintended\b", "paired-antonym formula",
     "name the actual outcomes instead of the tidy pair", "formulaic", "low"),
    (r"\bwe first sought to\b", "rigid scaffold",
     '"we started by trying to" — break the step-by-step template', "formulaic", "low"),
    (r"\bwe were also asked to\b", "rigid scaffold",
     '"we also had to" — break the step-by-step template', "formulaic", "low"),
    (r"\bthis assignment required us to\b", "institutional voice",
     '"the assignment asked us to"', "formulaic", "low"),
    (r"\b(?:was|had been|have been|has been) successfully implemented\b",
     "passive success marker", '"put into practice" or name what actually happened', "formulaic", "low"),

    # --- second expansion: hedging constructions ------------------------------
    (r"\bit bears mentioning that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit is imperative to\b", "hedging filler", '"we must"', "hedge", None),
    (r"\bit should come as no surprise that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bas we all know\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bfor what it'?s worth\b", "hedging filler", "cut it", "hedge", "medium"),
    (r"\bwithout a doubt\b", "overclaiming adverb", '"clearly"', "hedge", "medium"),
    (r"\bone cannot (ignore|overlook|understate)\b", "hedging filler", "cut it or say the thing directly", "hedge", None),
    (r"\bit is impossible to (ignore|overlook)\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\blet'?s be honest\b", "conversational filler", "cut it", "hedge", "medium"),
    (r"\bfrankly speaking\b", "conversational filler", "cut it", "hedge", "medium"),
    (r"\bthe fact of the matter is that\b", "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bas a matter of fact\b", "hedging filler", '"in fact"', "hedge", "medium"),
    (r"\bat first glance\b", "hedging filler", '"on the surface"', "hedge", "medium"),
    (r"\bupon closer inspection\b", "hedging filler", "cut it", "hedge", "medium"),
    (r"\barguably\b", "hedging adverb", '"one could argue" or cut', "hedge", "medium"),
    (r"\bmore often than not\b", "hedging filler", '"usually"', "hedge", "medium"),
    (r"\bfor the most part\b", "hedging filler", '"mostly" or drop', "hedge", "low"),

    # --- second expansion: formulaic transitions ------------------------------
    (r"\bin turn\b", "formulaic transition", '"which then" or restructure', "transition", "medium"),
    (r"\bin doing so\b", "formulaic transition", '"by doing that"', "transition", "medium"),
    (r"\bas such\b", "formulaic transition", '"so" or "therefore"', "transition", "medium"),
    (r"\bin this regard\b", "formulaic transition", '"on that front"', "transition", "medium"),
    (r"\bto that end\b", "formulaic transition", '"for that purpose"', "transition", "medium"),
    (r"\bby the same token\b", "formulaic transition", '"likewise"', "transition", "medium"),
    (r"\bin light of\b", "formulaic transition", '"given"', "transition", "medium"),
    (r"\bin the face of\b", "formulaic transition", '"when faced with"', "transition", "medium"),
    (r"\bin the grand scheme of things\b", "formulaic transition", '"overall"', "transition", "medium"),
    (r"\bat its core\b", "formulaic transition", '"essentially"', "transition", "medium"),
    (r"\bow(?:ing) to\b", "formulaic transition", '"because of"', "transition", "medium"),
    (r"\bin the years to come\b", "formulaic transition", '"in the future"', "transition", "medium"),
    (r"\bin the not-too-distant future\b", "formulaic transition", '"soon"', "transition", "medium"),
    (r"\bin the coming years\b", "formulaic transition", '"soon" or name the timeframe', "transition", "medium"),

    # --- second expansion: wordy constructions and corporate tics -------------
    (r"\bplays? a (crucial|key|vital|significant|essential|important|pivotal|major) role in\b",
     "formulaic construction", '"is central to"', "formulaic", None),
    (r"\bin the world of\b", "cliché opener", '"in" or be specific', "formulaic", "medium"),
    (r"\bthe fast-paced world of\b", "cliché opener", "name the field directly", "formulaic", "medium"),
    (r"\bin an increasingly (digital|complex|connected|competitive|interconnected) world\b",
     "cliché opener", "be specific about the change", "formulaic", None),
    (r"\bdue to the fact that\b", "wordy construction", '"because"', "formulaic", "medium"),
    (r"\bdespite the fact that\b", "wordy construction", '"although"', "formulaic", "medium"),
    (r"\bin the event that\b", "wordy construction", '"if"', "formulaic", "medium"),
    (r"\bas to whether\b", "wordy construction", '"whether"', "formulaic", "medium"),
    (r"\bwith respect to\b", "wordy construction", '"about"', "formulaic", "medium"),
    (r"\bwith regard to\b", "wordy construction", '"about"', "formulaic", "medium"),
    (r"\bat this point in time\b", "wordy construction", '"now"', "formulaic", "medium"),
    (r"\bat the present time\b", "wordy construction", '"now"', "formulaic", "medium"),
    (r"\bmoving forward\b", "corporate tic", '"from here on"', "formulaic", "medium"),
    (r"\bkey takeaways?\b", "corporate jargon", '"main points"', "formulaic", "medium"),

    # --- second expansion: ChatGPT self-reference tells ----------------------
    (r"\bas an ai( (language )?model)?\b", "self-reference tell", "drop it — never announce being an AI", "formulaic", "high"),
    (r"\bas a language model\b", "self-reference tell", "drop it — never announce being an AI", "formulaic", "high"),
    (r"\bi'?m (sorry|afraid)[, ]*(but |that )?i (can'?t|cannot|do not|don'?t)\b",
     "self-reference tell", "drop the apology; answer directly", "formulaic", "high"),
    (r"\bi cannot (assist|help you|provide|process|access|generate|fulfill|complete|create|write|share|verify|confirm|recommend)\b",
     "self-reference tell", "rephrase as the fact itself or what you can do", "formulaic", "high"),

    # --- second expansion: filler buzzwords -----------------------------------
    (r"\bsynergy\b", "corporate filler", '"combined effort"', "filler", "high"),
    (r"\bempower(s|ed|ing)?\b", "marketing buzzword", '"enable", "help", or "give"', "filler", "medium"),
    (r"\bfoster(s|ed|ing)?\b", "vague verb", '"encourage" or "build"', "filler", "medium"),
    (r"\bcultivat(es|ed|ing|e)\b", "vague verb", '"build" or "develop"', "filler", "medium"),
    (r"\bfacilitat(es|ed|ing|e)\b", "vague verb", '"help" or "make easier"', "filler", "medium"),
    (r"\bstreamlin(es|ed|ing|e)\b", "vague verb", '"simplify"', "filler", "medium"),
    (r"\bharness(es|ed|ing)?\b", "vague verb", '"use" or "put to work"', "filler", "medium"),
    (r"\becosystem(s)?\b", "marketing buzzword", '"system" or "network"', "filler", "medium"),
    (r"\bparadigm\b", "stilted word", '"framework" or "model"', "filler", "medium"),
    (r"\bshowcas(es|ed|ing|e)\b", "vague verb", '"highlight" or "display"', "filler", "medium"),
    (r"\brevolutioniz(es|ed|ing|e)\b", "marketing buzzword", '"transform"', "filler", "medium"),
    (r"\bdisruptive\b", "marketing buzzword", "be specific about the change", "filler", "medium"),
    (r"\bbest-in-class\b", "marketing buzzword", '"top-tier"', "filler", "medium"),
    (r"\bworld-class\b", "marketing buzzword", '"top-quality"', "filler", "medium"),
    (r"\binnovative\b", "vague adjective", '"new" or "novel"', "filler", "medium"),
    (r"\bturnkey\b", "corporate jargon", '"ready to use"', "filler", "medium"),
    (r"\bmodern-day\b", "stilted word", '"modern"', "filler", "medium"),
    (r"\bthis day and age\b", "cliché opener", '"these days"', "filler", "medium"),
    (r"\bever-growing\b", "vague adjective", '"growing"', "filler", "medium"),
    (r"\bever-increasing\b", "vague adjective", '"growing"', "filler", "medium"),
    (r"\bever-changing\b", "vague adjective", '"changing"', "filler", "medium"),
    (r"\bever-present\b", "vague adjective", '"constant"', "filler", "medium"),
    (r"\bthought leadership\b", "corporate jargon", '"expert opinion"', "filler", "medium"),
    (r"\btouchpoints?\b", "corporate jargon", '"point of contact"', "filler", "medium"),
    (r"\bbandwidth (for|to)\b", "corporate jargon", '"capacity"', "filler", "medium"),

    # --- third expansion: modern LLM-era tells -------------------------------
    # These are the favorites of current models (2023-2026): the "unpack /
    # reimagine / nuanced" family, template openers and closers, and the
    # marketing register that leaked into everyday prose.
    (r"\bunpack(s|ing)?\b", "LLM favorite verb", '"break down", "examine", or "explain"', "filler", "medium"),
    (r"\breimagine(s|d|ing)?\b", "marketing buzzword", '"rethink"', "filler", "medium"),
    (r"\bredefine(s|d|ing)?\b", "vague verb", '"rethink"', "filler", "medium"),
    (r"\bunparalleled\b", "overclaiming adjective", "be specific or drop", "filler", "medium"),
    (r"\bsupercharge(s|d)?\b", "marketing buzzword", '"boost"', "filler", "medium"),
    (r"\bamplif(ies|ying|y)\b", "vague verb", '"strengthen" or be specific', "filler", "medium"),
    (r"\bdigital transformation\b", "corporate jargon", '"the move to digital tools"', "filler", "medium"),
    (r"\bdata-driven\b", "corporate jargon", '"based on the data"', "filler", "low"),
    (r"\bplaybook\b", "corporate jargon", '"approach" or be specific', "filler", "medium"),
    (r"\bnuanced\b", "LLM favorite adjective", "be specific about the shades", "formulaic", "low"),
    (r"\bbridge the gap\b", "cliché metaphor", '"close the gap"', "cliche", "medium"),
    (r"\ba wealth of\b", "cliché metaphor", '"a lot of"', "cliche", "medium"),
    (r"\ba treasure trove of\b", "cliché metaphor", '"a rich collection of"', "cliche", "medium"),
    (r"\bforefront of\b", "cliché metaphor", '"leading in" or be specific', "cliche", "medium"),
    (r"\brapidly evolving\b", "cliché metaphor", '"changing fast"', "cliche", "medium"),
    (r"\bnew normal\b", "cliché metaphor", '"the new situation"', "cliche", "medium"),
    (r"\bperfect storm\b", "cliché metaphor", '"a bad combination"', "cliche", "medium"),
    (r"\bcan of worms\b", "cliché metaphor", '"a messy situation"', "cliche", "medium"),
    (r"\bdigital landscape\b", "cliché metaphor", '"the online world"', "cliche", "medium"),
    (r"\bthe changing nature of\b", "cliché metaphor", "name the change directly", "cliche", "medium"),
    (r"\bin a world where\b", "cliché opener", "be specific or drop", "cliche", "medium"),
    (r"\bthe intricacies of\b", "stilted word", '"the details of"', "cliche", "medium"),
    (r"\bit is (important|essential|crucial|vital) to (understand|realize|recognize) that\b",
     "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bit'?s (important|essential|crucial|vital) to (understand|realize|recognize) that\b",
     "hedging filler", "cut it — state the fact directly", "hedge", None),
    (r"\bkeep in mind that\b", "hedging filler", "cut it — state the fact directly", "hedge", "medium"),
    (r"\bi hope this (email|message|note) finds you well\b", "template opener", "cut it — start with the actual news", "hedge", "medium"),
    (r"\bwhat'?s more\b", "formulaic transition", '"besides" or "also"', "transition", "medium"),
    (r"\bfirst and foremost\b", "formulaic opener", '"above all" or "first"', "transition", "medium"),
    (r"\blast but not least\b", "formulaic opener", '"finally"', "transition", "medium"),
    (r"\bto summarize\b", "formulaic transition", '"in short"', "transition", "medium"),
    (r"\bto conclude\b", "formulaic transition", '"in the end"', "transition", "medium"),
    (r"\ball things considered\b", "formulaic transition", '"on balance"', "transition", "medium"),
    (r"\bas we (explore|navigate|delve)\b", "formulaic opener", "cut it and start with the subject", "transition", "medium"),
    (r"\bwhen we look at\b", "formulaic opener", '"looking at" or cut', "transition", "medium"),
    (r"\bin today'?s society\b", "cliché opener", '"these days"', "transition", "medium"),
    (r"\bserves as a\b", "formulaic construction", '"acts as" or restructure', "formulaic", "medium"),
    (r"\b(do not|don'?t) hesitate to\b", "template closer", '"feel free to" or cut', "formulaic", "medium"),
    (r"\btransform(s)? the way we\b", "cliché metaphor", '"change how we"', "formulaic", "medium"),
    (r"\bharness the power of\b", "cliché metaphor", '"use"', "formulaic", "medium"),
    (r"\bunlock the (full )?potential of\b", "marketing buzzword", '"make the most of"', "formulaic", "medium"),

    # --- fourth expansion: Wikipedia "Signs of AI writing" patterns ---------
    # From the WikiProject AI Cleanup guide (blader/humanizer pattern set):
    # the constructions real editors flag when they review model-written text.
    (r"\bnot (just|merely) [^.!?]{3,60}? (but|it'?s|it is) ",
     "negative parallelism", "state the point directly instead of the 'not X, it's Y' scaffold", "formulaic", "medium"),
    (r"\blet'?s (dive|jump) in\b", "signposting announcement", "start with the content instead", "formulaic", "medium"),
    (r"\bhere'?s what you need to know\b", "signposting announcement", "start with the content instead", "formulaic", "medium"),
    (r"\bwithout further ado\b", "signposting announcement", "cut it — just start", "formulaic", "medium"),
    (r"\bbuckle up\b", "signposting announcement", "cut it — just start", "formulaic", "medium"),
    (r"\bgreat question!?\b", "sycophantic tone", "answer directly without the praise", "formulaic", "medium"),
    (r"\byou'?re absolutely right\b", "sycophantic tone", "agree directly without the praise", "formulaic", "medium"),
    (r"\bexcellent (point|question)!?\b", "sycophantic tone", "answer directly without the praise", "formulaic", "medium"),
    (r"\bnestled (in|within)\b", "promotional language", "say where it is plainly", "filler", "medium"),
    (r"\bbreathtaking\b", "promotional language", "be specific about what's impressive", "filler", "medium"),
    (r"\bdespite (the )?challenges?[^.!?]{0,60}? (thrives?|continues to (thrive|grow|succeed))\b",
     "formulaic challenge", "keep the facts; cut the 'despite X, still Y' boosterism", "formulaic", "medium"),
    (r"\bthe future looks bright\b", "generic conclusion", "name a specific plan or fact instead", "formulaic", "medium"),
    (r"\bthe sky'?s the limit\b", "generic conclusion", "be specific about what is possible", "formulaic", "medium"),
    (r"\bhonestly\?", "fake-candid opener", "cut the setup — just answer", "hedge", "low"),
    (r"\b(is|was) (not )?(just |merely )?(the |a |an )?"
     r"(language|key|heart|soul|essence|foundation|measure|currency|art|science|"
     r"lifeblood|cornerstone|bedrock|fabric|spirit|fuel|glue|backbone|secret|"
     r"true measure|very heart|real test|beginning|end|answer|problem|solution)( of| to)\b",
     "aphorism formula", "state the actual claim instead of the 'X is the Y of Z' motto", "formulaic", "low"),
]


#: Repetitive sentence openers worth varying when they dominate a paragraph.
COMMON_OPENERS = [
    "the", "it", "this", "these", "that", "however", "additionally",
    "furthermore", "moreover", "in", "as", "while", "although", "there",
]

#: Words that signal formulaic, machine-like lists when overused.
LIST_MARKERS = ["firstly", "secondly", "thirdly", "lastly", "finally"]


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    """A single detected problem in the source text."""

    kind: str            # machine-readable category, e.g. "filler"
    severity: str        # "high" | "medium" | "low"
    message: str         # human-readable summary
    snippet: str         # the offending substring (trimmed)
    suggestion: str      # concrete fix suggestion

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "snippet": self.snippet,
            "suggestion": self.suggestion,
        }


@dataclass
class NaturalnessReport:
    """Full analysis of a piece of text."""

    score: int
    issues: List[Issue] = field(default_factory=list)
    sentence_count: int = 0
    avg_sentence_len: float = 0.0
    sentence_len_cv: float = 0.0  # coefficient of variation (rhythm uniformity)
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)  # advanced signals

    def to_dict(self) -> Dict:
        return {
            "score": self.score,
            "issues": [i.to_dict() for i in self.issues],
            "sentence_count": self.sentence_count,
            "avg_sentence_len": round(self.avg_sentence_len, 1),
            "sentence_len_cv": round(self.sentence_len_cv, 3),
            "metrics": self.metrics,
        }


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

#: Common words excluded from repetition analysis.
_STOPWORDS = set(
    "a an the and or but if then else for nor so yet of in on at to from by "
    "with without about against between into through during before after above "
    "below up down out off over under again further once here there when where "
    "why how all any both each few more most other some such no nor not only "
    "own same than too very s t can will just don should now is are was were "
    "be been being have has had do does did having doing it its this that these "
    "those i you he she we they me him her us them my your his their our its "
    "as but which who whom whose what also would could may might shall must "
    "of at on".split()
)


def split_sentences(text: str) -> List[str]:
    """Split text into sentences, preserving the original strings (minus the
    trailing period). Sentences with no terminal punctuation are kept whole."""
    parts = _SENTENCE_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _trim_snippet(s: str, limit: int = 80) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _sentence_stats(sentences: List[str]) -> Tuple[float, float]:
    """Return (mean length, coefficient of variation) in words."""
    if not sentences:
        return 0.0, 0.0
    lengths = [len(s.split()) for s in sentences if s.strip()]
    if len(lengths) < 2:
        return (lengths[0] if lengths else 0.0), 0.0
    m = mean(lengths)
    sd = stdev(lengths)
    cv = (sd / m) if m else 0.0
    return m, cv


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _overlaps_allowlist(lowered: str, match: re.Match, allowlist: set) -> bool:
    """True when *match* sits inside an occurrence of an allowlisted phrase.

    This lets an allowlist entry like "the ever-evolving landscape of" also
    suppress the bare-word match for "landscape" at that position.
    """
    for entry in allowlist:
        if not entry:
            continue
        pos = lowered.find(entry)
        while pos != -1:
            if pos <= match.start() < pos + len(entry):
                return True
            pos = lowered.find(entry, pos + 1)
    return False


def _severity_for(word: str, override: Optional[str]) -> str:
    """Severity for a flagged phrase: explicit override or derived."""
    if override:
        return override
    return "high" if len(word.split()) > 1 else "medium"


def _check_fillers(text: str, allowlist: Optional[set] = None) -> List[Issue]:
    """Find AI-filler phrases and buzzwords."""
    issues: List[Issue] = []
    allowlist = allowlist or set()
    lowered = text.lower()
    for pattern, label, suggestion, kind, severity in AI_FILLERS:
        for match in re.finditer(pattern, lowered):
            word = match.group(0)
            if word in allowlist or _overlaps_allowlist(lowered, match, allowlist):
                continue
            issues.append(
                Issue(
                    kind=kind,
                    severity=_severity_for(word, severity),
                    message=f'"{word}" reads like {label}',
                    snippet=_trim_snippet(word),
                    suggestion=suggestion,
                )
            )
    return issues


def _check_openers(sentences: List[str]) -> List[Issue]:
    """Flag paragraphs whose sentences start the same way too often."""
    if len(sentences) < 4:
        return []
    starts: Dict[str, int] = {}
    for s in sentences:
        first = s.split()[0].lower().strip("“”\"'(),.!?;:") if s.split() else ""
        if first:
            starts[first] = starts.get(first, 0) + 1
    issues: List[Issue] = []
    for word, count in starts.items():
        share = count / len(sentences)
        # Flag only when a bare majority (or at least three sentences) share
        # an opener — two of five "The"s is normal human writing.
        if word in COMMON_OPENERS and (share >= 0.5 or count >= 3):
            issues.append(
                Issue(
                    kind="openers",
                    severity="high" if share >= 0.55 or count >= 4 else "medium",
                    message=(
                        f'{count} of {len(sentences)} sentences start with "{word.capitalize()}" '
                        f"({share:.0%}) — the rhythm feels mechanical"
                    ),
                    snippet=f'"{word.capitalize()} …"',
                    suggestion="vary the sentence openers",
                )
            )
    return issues


def _check_rhythm(sentences: List[str]) -> List[Issue]:
    """Flag unusually uniform sentence lengths (low coefficient of variation)."""
    if len(sentences) < 5:
        return []
    m, cv = _sentence_stats(sentences)
    if cv < 0.3 and m > 0:
        return [
            Issue(
                kind="rhythm",
                severity="medium",
                message=(
                    f"Sentences are nearly uniform in length (CV {cv:.2f}, avg {m:.0f} words) — "
                    "this flat rhythm is a machine tell"
                ),
                snippet=f"avg {m:.0f} words/sentence",
                suggestion="vary sentence length: split long ones, merge short ones",
            )
        ]
    return []


#: Abstract-noun suffixes — a triad of these reads as manufactured
#: parallelism ("innovation, inspiration, and insights").
_ABSTRACT_SUFFIX = re.compile(r"(?:ion|ity|ment|ness|tion|ence|ance|ism|ship|ure|age)$")

#: A three-item comma list, "X, Y, and Z", with single words.
_TRIAD_RE = re.compile(r"\b([A-Za-z]{4,}),\s+([A-Za-z]{4,}),\s+and\s+([A-Za-z]{4,})\b")


def _check_rule_of_three(text: str) -> List[Issue]:
    """Flag the manufactured "rule of three" — three parallel abstract nouns
    (Wikipedia pattern #10: "innovation, inspiration, and insights").

    Only fires when at least two of the three items carry an abstract suffix,
    so concrete triads ("flour, sugar, and eggs", "red, white, and blue")
    — which humans write all the time — are never flagged.
    """
    issues: List[Issue] = []
    for m in _TRIAD_RE.finditer(text):
        items = list(m.groups())
        abstract = sum(1 for w in items if _ABSTRACT_SUFFIX.search(w))
        if abstract >= 2:
            snippet = ", ".join(items)
            issues.append(
                Issue(
                    kind="triad",
                    severity="low",
                    message=(
                        f'Rule of three ("{snippet}") — three parallel abstract nouns '
                        "read like manufactured emphasis"
                    ),
                    snippet=snippet,
                    suggestion="use the natural number of items a person would pick",
                )
            )
    return issues


def _check_staccato(sentences: List[str]) -> List[Issue]:
    """Flag manufactured punchlines — a run of very short dramatic sentences
    (Wikipedia pattern #31: "It had no preference. No prior. No nostalgia.").

    Fires when three or more consecutive sentences are each five words or
    fewer; the run itself is the tell (humans drop one short sentence for
    emphasis, not a string of them)."""
    if len(sentences) < 3:
        return []
    best_run = cur = 0
    start = 0
    best_start = 0
    for i, s in enumerate(sentences):
        if len(s.split()) <= 5:
            if cur == 0:
                start = i
            cur += 1
            if cur > best_run:
                best_run = cur
                best_start = start
        else:
            cur = 0
    if best_run < 3:
        return []
    snippet = " ".join(sentences[best_start : best_start + best_run])
    snippet = _trim_snippet(snippet, limit=60)
    return [
        Issue(
            kind="staccato",
            severity="low",
            message=(
                f'{best_run} very short sentences in a row ("{snippet}") — '
                "staccato drama reads like a manufactured punchline"
            ),
            snippet=snippet,
            suggestion="vary the sentence lengths and keep the concrete claim",
        )
    ]


def _check_unicode_marks(text: str) -> List[Issue]:
    """Flag invisible Unicode carriers (zero-width, bidi controls, joiners).

    Borrowed from the watermarks-remover project's Layer-A hygiene: stealth
    humanizers and some generators inject invisible codepoints as a machine-
    readable fingerprint. Bidi controls are ``high`` (they can reorder the
    visible text and flip meaning); the rest are ``medium``.
    """
    from .unicode_marks import check_unicode_marks

    return check_unicode_marks(text)


def _check_emdash(text: str) -> List[Issue]:
    """Flag heavy em-dash use — measured by *density*, not raw count.

    LLM and humanized prose over-produce em dashes ("X — a case study — Y"),
    while human writing in the labeled corpus uses none: 4 dashes in a
    200-word paragraph (17.9 per 1000 words) is a tell; 4 dashes across a
    2,000-word report is not. Fires only when the density is genuinely
    elevated: 3+ dashes at 10+ per 1000 words."""
    count = text.count("—")
    words = len(text.split())
    if not words:
        return []
    per_1000 = count / words * 1000
    if count >= 3 and per_1000 >= 10:
        return [
            Issue(
                kind="punctuation",
                severity="medium" if per_1000 >= 15 else "low",
                message=(
                    f"{count} em dashes in {words} words ({per_1000:.1f} per 1000) — "
                    "heavy em-dash use is a common AI tell"
                ),
                snippet="—" * min(count, 8),
                suggestion='replace most with commas, parentheses, or restructure the sentence',
            )
        ]
    return []


#: Passive-voice verbs + prepositions that form the "not X; they are Y"
#: balanced-antithesis scaffold ("are not acknowledged in passing and then set
#: aside; they are treated as substantive concerns"). Only meaningful as a
#: tell when the sentence also carries the negating "not".
_ANTITHESIS_RE = re.compile(
    r"\b(?:they|these|it|this|such concerns|those)\s+(?:are|were|is|was)\s+"
    r"(?:not\s+)?(?:acknowledged|treated|seen|viewed|regarded|described|"
    r"presented|framed|defined|conceived|intended|meant|taken|held|cast|"
    r"portrayed|positioned|spoken|understood|deemed|considered|used|made|"
    r"shaped|built|grounded|based|set)\s+(?:as|in|on|at|to|with|for|"
    r"among|against|apart|aside|above|beyond)\b",
    re.IGNORECASE,
)


#: Balanced "not X but as Y" where both sides are parallel prepositional
#: phrases — the rhetorical scaffold LLMs lean on ("treated as aspirations
#: but as qualities"). The but-side must continue with "as" so natural
#: "not the first time but the third" phrasing never matches.
_PARALLEL_BUT_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|treated|seen|viewed|regarded|considered|"
    r"framed|positioned|presented|portrayed|cast|defined|conceived|intended|"
    r"designed|meant|used|chosen|made|shaped|built|acknowledged|spoken|"
    r"described|understood|deemed|held|taken|based|grounded)\s+not\s+"
    r"[a-z]{2,20}( [a-z]{2,20}){0,5}\s+but\s+as ",
    re.IGNORECASE,
)


def _check_antithesis(text: str) -> List[Issue]:
    """Flag the balanced "not X; they are Y" / "not X but Y" antithesis
    scaffold (Wikipedia pattern #11 "negative parallelism" in its common
    passive-voice form).

    Both patterns require a negating "not" in the same sentence, and the
    passive form only fires on passivized copular verbs — plain speech like
    "She is not tall but she is quick" never matches."""
    issues: List[Issue] = []
    for sent in split_sentences(text):
        if not re.search(r"\bnot\b", sent, re.I):
            continue
        if _ANTITHESIS_RE.search(sent) or _PARALLEL_BUT_RE.search(sent):
            snippet = _trim_snippet(sent, limit=70)
            issues.append(
                Issue(
                    kind="antithesis",
                    severity="low",
                    message=(
                        f'Balanced antithesis ("{snippet}") — the "not X but Y" '
                        "scaffold reads like polished AI prose"
                    ),
                    snippet=snippet,
                    suggestion="say it plainly: one clause, direct statement",
                )
            )
    return issues


def _check_repetition(sentences: List[str]) -> List[Issue]:
    """Flag unusual words repeated many times (a sign of formulaic writing)."""
    words: Dict[str, int] = {}
    for s in sentences:
        for w in re.findall(r"[a-zA-Z']{4,}", s.lower()):
            if w not in _STOPWORDS:
                words[w] = words.get(w, 0) + 1
    issues: List[Issue] = []
    for word, count in sorted(words.items(), key=lambda kv: -kv[1]):
        if count >= 4 and count >= len(sentences):
            issues.append(
                Issue(
                    kind="repetition",
                    severity="low",
                    message=f'"{word}" appears {count} times — repetitive wording',
                    snippet=word,
                    suggestion="vary the wording for at least some occurrences",
                )
            )
    return issues


def _check_list_markers(sentences: List[str]) -> List[Issue]:
    """Flag formulaic enumerations (firstly/secondly/finally)."""
    found = [m for m in LIST_MARKERS if any(s.lower().startswith(m) for s in sentences)]
    if found:
        return [
            Issue(
                kind="structure",
                severity="medium",
                message=f'Formulaic enumeration ("{", ".join(found)}") reads like a template',
                snippet=", ".join(found),
                suggestion="drop the markers and let the points flow naturally",
            )
        ]
    return []


def _check_structure_shape(text: str, keep_structure: bool = False) -> List[Issue]:
    """Flag list- and heading-heavy layouts (a structured-answer shape that
    LLMs produce far more often than people do). Adapted from the
    ``structured_answer_shape`` signal in lynote-ai/ai-text-detector.

    *keep_structure* suppresses the flag — Business mode keeps bulleted
    summaries as a legitimate register choice.
    """
    if keep_structure:
        return []
    listish = len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", text))
    headingish = len(re.findall(r"(?m)^\s{0,3}#{1,3}\s+|^\s{0,3}[A-Z][A-Za-z ]{3,}:\s*$", text))
    shape = listish + 2 * headingish
    # A couple of bullets is normal human writing; a fully list/heading-laid
    # out answer (4+ structured lines) is the machine tell.
    if shape < 4:
        return []
    return [
        Issue(
            kind="structure",
            severity="medium",
            message=(
                f"Text is laid out as {listish} list lines and {headingish} heading lines "
                "— a structured-answer shape common in AI output"
            ),
            snippet=f"{shape} structured lines",
            suggestion="blend the points into flowing paragraphs instead of bullets/headings",
        )
    ]


def _check_lexical_variety(text: str) -> List[Issue]:
    """Flag a low unique-token ratio on longer text (recycled vocabulary).

    Length-gated: on short passages the ratio is naturally high and carries no
    signal. Adapted from the ``lexical_smoothness`` signal in
    lynote-ai/ai-text-detector.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text.lower())
    if len(words) < 100:
        return []
    ttr = len(set(words)) / len(words)
    if ttr >= 0.56:
        return []
    return [
        Issue(
            kind="lexical",
            severity="medium" if ttr < 0.50 else "low",
            message=(
                f"Vocabulary is repetitive for its length (unique-token ratio {ttr:.2f}) "
                "— templated word choice"
            ),
            snippet=f"TTR {ttr:.2f}",
            suggestion="vary the vocabulary instead of recycling the same words",
        )
    ]


def _check_compressibility(text: str) -> List[Issue]:
    """Flag phrasing that compresses unusually well (repetitive templates).

    Length-gated: zlib only exposes repetition once the text is long enough.
    Adapted from the ``compressibility`` signal in lynote-ai/ai-text-detector.
    """
    norm = re.sub(r"\s+", " ", text).strip()
    raw = len(norm.encode("utf-8"))
    if raw < 800:
        return []
    ratio = len(zlib.compress(norm.encode("utf-8"))) / raw
    if ratio >= 0.48:
        return []
    return [
        Issue(
            kind="compressibility",
            severity="medium" if ratio < 0.40 else "low",
            message=(
                f"Text compresses unusually well (zlib ratio {ratio:.2f}) — "
                "repetitive phrasing"
            ),
            snippet=f"ratio {ratio:.2f}",
            suggestion="vary phrasing; highly compressible text often recycles templates",
        )
    ]


def _check_short_sample(word_count: int) -> List[Issue]:
    """Low-confidence note for very short samples (statistical signals are
    meaningless below ~30 words). Soft, never a refusal — the score still
    reflects the pattern checks that did run. Mirrors the short-text
    guardrail in lynote-ai/ai-text-detector."""
    if word_count <= 0 or word_count >= 30:
        return []
    return [
        Issue(
            kind="short",
            severity="low",
            message=(
                f"Sample is only {word_count} words — statistical signals are skipped "
                "and the score is low-confidence"
            ),
            snippet=f"{word_count} words",
            suggestion="analyze a longer passage (roughly 30+ words) for a steadier score",
        )
    ]


# ---------------------------------------------------------------------------
# Advanced metrics (the "detection signals" panel)
#
# Five 0-100 scores where *higher = more human-like*. These are the classic
# statistical tells detectors look at, computed honestly with pure stdlib:
#
#   perplexity   how random / unpredictable the text is (LLMs are *more*
#                predictable, so low randomness is a tell). Approximated with
#                zlib: repetitive, templated text compresses better.
#   burstiness   how much sentence length varies (LLM output is uniform;
#                humans mix long and short sentences). Uses the coefficient
#                of variation of sentence lengths.
#   syntactic    how free the text is of formulaic AI sentence shapes
#                (filler / cliché / hedge / transition patterns).
#   coherence    how well consecutive sentences hang together (lexical
#                cohesion — shared content words between neighbours).
#   word_choice  how predictably the text picks words (AI over-produces
#                mid-frequency formal vocabulary — "training echoes" — while
#                people lean on the common core). Uses rare-word density and
#                mean surprisal against real Google-Books frequencies.
#
# Length-gated signals return ``None`` on short text (no statistical signal
# exists there), which the UI renders as "—".
# ---------------------------------------------------------------------------


def _content_words(text: str) -> set:
    """Content words (stopwords removed), lowercased."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _perplexity_score(text: str) -> Optional[float]:
    """0-100 randomness score from compression ratio (higher = more random).

    zlib can only expose repetition once text is long enough, so short
    passages return ``None``."""
    norm = re.sub(r"\s+", " ", text).strip()
    raw = len(norm.encode("utf-8"))
    if raw < 800:
        return None
    ratio = len(zlib.compress(norm.encode("utf-8"))) / raw
    # Typical human prose sits around 0.45-0.60; templated text drops toward
    # 0.30. Map that band to a 0-100 scale and clamp.
    return round(max(0.0, min(100.0, (ratio - 0.30) / 0.30 * 100)), 1)


def _burstiness_score(sentences: List[str]) -> Optional[float]:
    """0-100 sentence-length variation score (higher = more varied).

    Length-gated like the other statistical signals: a variance measured on
    3-4 sentences is noise (a 3-sentence text with lengths 20/19/22 looks
    "flat" and one with 5/25/15 looks "varied" — neither is a real
    rhythm signal). Returns ``None`` below 5 sentences, which the UI
    renders as "—" and the human-band checks skip.
    """
    lengths = [len(s.split()) for s in sentences if s.strip()]
    if len(lengths) < 5:
        return None
    m = mean(lengths)
    if not m:
        return None
    cv = stdev(lengths) / m
    # Human CV typically 0.35-0.7; uniform AI prose drops to ~0.15-0.3.
    return round(max(0.0, min(100.0, cv / 0.6 * 100)), 1)


def _syntactic_score(text: str, allowlist: Optional[set] = None) -> float:
    """0-100 freedom from formulaic AI sentence shapes."""
    issues = _check_fillers(text, allowlist=allowlist)
    issues += _check_emdash(text)
    issues += _check_list_markers(split_sentences(text))
    score = 100.0 - sum(12 if i.severity == "high" else 6 for i in issues)
    return round(max(0.0, min(100.0, score)), 1)


def _word_choice_score(text: str) -> Optional[float]:
    """0-100 word-choice predictability score (higher = more human).

    Real detectors weigh vocabulary "training echoes": models over-produce
    mid-frequency formal words while people lean on the common core. This
    measures the rare-word density and mean surprisal (-log2 p) of content
    words against an embedded real-English frequency table (Google Books
    1-gram counts, see :mod:`naturalizer.wordfreq`).

    Calibrated on the labeled corpus: human prose lands around 70-85,
    realistic AI prose around 40-60. Length-gated like the other
    statistical signals (needs ~30 content words to mean anything).
    """
    tokens = re.findall(r"[a-z']+", text.lower())
    content = [w for w in tokens if w not in _STOPWORDS and len(w) > 1]
    if len(content) < 30:
        return None
    rare = sum(1 for w in content if w not in COMMON_5000) / len(content)
    surp = mean(-LOGP[w] if w in LOGP else 18.0 for w in content)
    # Linear maps calibrated on the labeled corpus + realistic AI prose
    # (content words only, embedded table): human corpus lands ~80, plain
    # business prose ~75-95, tell-stuffed AI corpus ~54, realistic
    # blog/academic AI prose ~50-65, concrete topic prose (e.g. a recipe)
    # can score low on rarity alone — which is why the benchmark only treats
    # word_choice as a tell when the prose is ALSO formulaic (see
    # tools/detector_bench.py: human_band).
    #   rare 0.24 -> 80, 0.34 -> 50   (rare-word density)
    #   surp 13.7 -> 80, 14.4 -> 55   (mean surprisal)
    rare_score = 152.0 - 300.0 * rare
    surp_score = 644.0 - 41.0 * surp
    score = 0.55 * rare_score + 0.45 * surp_score
    return round(max(0.0, min(100.0, score)), 1)


def _coherence_score(sentences: List[str]) -> Optional[float]:
    """0-100 lexical-cohesion score (higher = more connected).

    Average proportion of a sentence's content words that also appear in its
    neighbour. Fully disjointed text scores low; text that over-reuses words
    scores high but is separately caught by repetition checks."""
    sents = [s for s in sentences if s.strip()]
    if len(sents) < 2:
        return None
    total = 0.0
    pairs = 0
    for a, b in zip(sents, sents[1:]):
        wa, wb = _content_words(a), _content_words(b)
        if not wa or not wb:
            continue
        shared = len(wa & wb)
        total += shared / min(len(wa), len(wb))
        pairs += 1
    if not pairs:
        return None
    return round(max(0.0, min(100.0, total / pairs * 200)), 1)


def compute_metrics(text: str, allowlist: Optional[set] = None) -> Dict[str, Optional[float]]:
    """The four advanced detection signals for *text*.

    Keys: ``perplexity``, ``burstiness``, ``syntactic``, ``coherence``,
    ``word_choice``. Higher is always more human-like; ``None`` means the
    text is too short for that statistical signal to be meaningful.
    """
    sentences = split_sentences(text)
    return {
        "perplexity": _perplexity_score(text),
        "burstiness": _burstiness_score(sentences),
        "syntactic": _syntactic_score(text, allowlist=allowlist),
        "coherence": _coherence_score(sentences),
        "word_choice": _word_choice_score(text),
    }


# ---------------------------------------------------------------------------
# Windowed (context-aware) segmentation — the Turnitin-style passage layer
# ---------------------------------------------------------------------------
#
# Commercial detectors (Turnitin, GPTZero) do not label isolated sentences.
# They score *overlapping windows* of several sentences and aggregate, so a
# sentence's final label is influenced by its neighbours. Two consequences:
#
#   * a weak sentence sitting inside an AI-heavy run gets pulled up (a clean
#     sentence in an AI paragraph is still inside an AI passage);
#   * an isolated flagged sentence in otherwise clean prose gets pulled down
#     (one odd sentence is not an AI region).
#
# This is implemented as a cheap HMM-style smoothing over the raw per-
# sentence weights (ai=2, mix=1, human=0) with a [0.6 own, 0.2 left, 0.2
# right] prior — deterministic, dependency-free, and testable.

_LABEL_WEIGHT = {"human": 0, "mix": 1, "ai": 2}


def _smooth_labels(labels: List[str]) -> List[str]:
    """Neighbour-smoothed labels (windowed scoring)."""
    n = len(labels)
    if n < 2:
        return list(labels)
    weights = [_LABEL_WEIGHT.get(l, 0) for l in labels]
    out: List[str] = []
    for i, w in enumerate(weights):
        left = weights[i - 1] if i > 0 else w
        right = weights[i + 1] if i < n - 1 else w
        s = 0.6 * w + 0.2 * left + 0.2 * right
        out.append("ai" if s >= 1.4 else ("mix" if s >= 0.6 else "human"))
    return out


def _find_regions(classified: List[Dict]) -> List[Dict]:
    """Contiguous runs of 2+ ``ai`` sentences — passage-level evidence.

    Returns ``[{start, end, count, text}]`` where ``start``/``end`` are
    sentence indices and ``text`` is the joined run (for highlighting).
    """
    regions: List[Dict] = []
    run_start: Optional[int] = None
    for i, c in enumerate(classified):
        if c["label"] == "ai":
            if run_start is None:
                run_start = i
        elif run_start is not None:
            if i - run_start >= 2:  # 2+ consecutive ai sentences
                run = classified[run_start:i]
                regions.append(
                    {
                        "start": run_start,
                        "end": i - 1,
                        "count": len(run),
                        "text": " ".join(s["sentence"] for s in run),
                    }
                )
            run_start = None
    if run_start is not None and len(classified) - run_start >= 2:
        run = classified[run_start:]
        regions.append(
            {
                "start": run_start,
                "end": len(classified) - 1,
                "count": len(run),
                "text": " ".join(s["sentence"] for s in run),
            }
        )
    return regions


# ---------------------------------------------------------------------------
# Confidence / abstention — honest evidence reporting
# ---------------------------------------------------------------------------

def evidence_coverage(report: NaturalnessReport) -> float:
    """Fraction (0-1) of the statistical signals that actually measured
    something. A score on a 20-word sample has almost no statistical
    evidence behind it; a 400-word sample has all four signals live."""
    values = [
        report.metrics.get("perplexity"),
        report.metrics.get("burstiness"),
        report.metrics.get("coherence"),
        report.metrics.get("word_choice"),
    ]
    return sum(1 for v in values if v is not None) / max(len(values), 1)


def abstain_reasons(text: str, report: NaturalnessReport) -> List[str]:
    """Reasons the verdict should be treated as low-confidence evidence, not
    a definitive authorship claim (the abstention rules commercial detectors
    publish: too-short samples, list/heading-dominated text, no measurable
    statistical signal).
    """
    reasons: List[str] = []
    words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text.lower()))
    if words < 30:
        reasons.append("sample too short for reliable statistical signals")
    if any(i.kind == "structure" and "list" in i.message for i in report.issues):
        reasons.append("text is mostly lists/headings — prose signals are unreliable")
    if evidence_coverage(report) == 0 and words >= 30:
        reasons.append("no statistical signal could be measured")
    return reasons


def classify_sentences(
    text: str,
    allowlist: Optional[set] = None,
    keep_structure: bool = False,
    window: bool = True,
) -> List[Dict]:
    """Per-sentence labels for the detector view.

    Each sentence is checked for AI tells and tagged ``"ai"`` (high/medium
    tells present), ``"mix"`` (minor tells only), or ``"human"`` (clean).
    With *window* (default) the labels are then neighbour-smoothed like the
    windowed scoring real detectors use, so a clean sentence inside an AI
    passage is contextually flagged and an isolated odd sentence is not
    over-weighted. Returns ``[{sentence, label, issues}]`` in document order.
    """
    sentences = split_sentences(text)
    raw: List[Dict] = []
    for sent in sentences:
        if not sent.strip():
            continue
        report = analyze(sent, allowlist=allowlist, keep_structure=keep_structure)
        severity_budget = {"high": 2, "medium": 1, "low": 0}
        weight = sum(severity_budget.get(i.severity, 0) for i in report.issues)
        if weight >= 2:
            label = "ai"
        elif weight == 1:
            label = "mix"
        else:
            label = "human"
        raw.append(
            {
                "sentence": sent,
                "label": label,
                "issues": [i.kind for i in report.issues],
            }
        )
    if window:
        smoothed = _smooth_labels([c["label"] for c in raw])
        for c, label in zip(raw, smoothed):
            c["label"] = label
    return raw


def sentence_distribution(
    text: str,
    allowlist: Optional[set] = None,
    keep_structure: bool = False,
) -> Dict[str, float]:
    """Percentages of sentences tagged AI / mixed / human (0-100 each), plus
    the windowed ``regions`` — contiguous runs of AI-tagged sentences."""
    classified = classify_sentences(text, allowlist=allowlist, keep_structure=keep_structure)
    total = len(classified) or 1
    counts = {"ai": 0, "mix": 0, "human": 0}
    for c in classified:
        counts[c["label"]] += 1
    return {
        "ai": round(counts["ai"] / total * 100),
        "mix": round(counts["mix"] / total * 100),
        "human": round(counts["human"] / total * 100),
        "sentences": classified,
        "regions": _find_regions(classified),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(
    text: str,
    allowlist: Optional[set] = None,
    keep_structure: bool = False,
) -> NaturalnessReport:
    """Score *text* on a 0-100 naturalness scale and collect issues.

    An *allowlist* is a set of lowercase filler phrases to ignore (used by
    style profiles that legitimately keep certain vocabulary).
    ``keep_structure`` suppresses the structured-answer-shape flag (Business
    mode keeps bulleted summaries).
    """
    sentences = split_sentences(text)
    issues: List[Issue] = []
    # Invisible-Unicode watermark hygiene (watermarks-remover Layer A):
    # zero-width / bidi / joiner codepoints are invisible machine
    # fingerprints — they must drop the score before anything else runs.
    issues += _check_unicode_marks(text)
    issues += _check_fillers(text, allowlist=allowlist)
    issues += _check_rule_of_three(text)
    issues += _check_staccato(sentences)
    issues += _check_emdash(text)
    issues += _check_antithesis(text)
    issues += _check_list_markers(sentences)
    issues += _check_structure_shape(text, keep_structure=keep_structure)

    # Statistical signals (rhythm, repetition, lexical variety, compressibility)
    # are meaningless on tiny samples — skip them and say so honestly.
    word_count = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text.lower()))
    issues += _check_short_sample(word_count)
    if word_count >= 30:
        issues += _check_openers(sentences)
        issues += _check_rhythm(sentences)
        issues += _check_repetition(sentences)
        issues += _check_lexical_variety(text)
        issues += _check_compressibility(text)

    # Deduplicate identical messages (a filler regex can match multiple times
    # with the same word, and we only want one issue per distinct phrase).
    seen: set = set()
    unique: List[Issue] = []
    for issue in issues:
        key = (issue.kind, issue.snippet)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)

    # Score: start at 100, subtract per-issue weights (high=12, medium=6,
    # low=3), clamp at 0.
    weights = {"high": 12, "medium": 6, "low": 3}
    raw = 100 - sum(weights[i.severity] for i in unique)
    score = max(0, min(100, raw))

    avg_len, cv = _sentence_stats(sentences)
    return NaturalnessReport(
        score=score,
        issues=unique,
        sentence_count=len(sentences),
        avg_sentence_len=avg_len,
        sentence_len_cv=cv,
        metrics=compute_metrics(text, allowlist=allowlist),
    )
