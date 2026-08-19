"""Shared helpers for the hermetic test suite.

The suite must never touch the network or real credentials. When a
developer has LLM provider keys exported in their shell (or loaded from
``.env.local`` by an entry point), importing the engine would make the
LLM path *live* and the suite would start making real API calls — slow,
flaky, and dependent on whatever is configured.

``import tests.testutil`` scrubs the provider env vars at import time and
defaults the plan to ``pro`` so the server tests keep exercising the full
feature set (LLM/deep/batch). Free-plan gating is tested explicitly in
``test_plans.py`` with a patched ``NATURALIZER_PLAN=free`` + a temp state
dir. Tests that exercise the LLM / chain paths set their own (fake)
values via the ``_env`` context managers in their modules, so they are
unaffected.
"""

from __future__ import annotations

import os

_PREFIXES = (
    "HINAA_", "CX_GATEWAY_", "OPENAI_", "GEMINI_", "AGENT_ROUTER_",
    "HCNSEC_", "BEAUTIFUL_AI_",
    # third-party detector keys: GPTZero, ZeroGPT, Originality, Turnitin
    "GPTZERO_", "ZEROGPT_", "ORIGINALITY_", "TURNITIN_",
)


def clear_llm_env() -> None:
    """Remove LLM provider variables from the process environment."""
    for key in list(os.environ):
        if key.startswith(_PREFIXES):
            del os.environ[key]
    # Full feature set unless a test says otherwise (see test_plans.py).
    os.environ.setdefault("NATURALIZER_PLAN", "pro")


clear_llm_env()
