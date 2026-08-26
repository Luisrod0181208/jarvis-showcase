"""
router_cascade.py , the three-tier LLM routing cascade (publication excerpt)

Rewritten from the live system for public release. Signal lists are
representative samples, not the production sets; the structure and the
ordering logic are faithful.

The core idea: the "router inversion" (see docs/01-router-inversion.md):
anything that is not *provably conversational* routes to the tier that can
call functions and can structurally escalate. The old design (route to the
function tier only on keyword match) let unlisted phrasings fall through to
a small conversational model, which would fabricate confirmations for
actions it never performed. The inversion makes the default failure mode
"slightly slower" instead of "confidently wrong."

Cascade order matters and is the whole design:

    1. small talk        -> FAST   (checked FIRST, closes the cost leak)
    2. recency signals   -> CLOUD
    3. depth signals     -> CLOUD
    4. advice shapes     -> CLOUD
    5. everything else   -> DEEP   (the inverted default)
"""

from enum import Enum


class Tier(Enum):
    FAST = "fast"    # local 3B , conversation only; CANNOT call functions
    DEEP = "deep"    # local 8B , constrained-JSON function dispatch
    CLOUD = "cloud"  # hosted frontier model , web search, tool use, reasoning


# ── Signal vocabularies (representative samples) ─────────────────────────
#
# Design note: maintaining a CONVERSATION allowlist is tractable because
# the conversational domain is small and stable. Maintaining a COMMAND
# allowlist is a treadmill because the command domain is unbounded , that
# asymmetry is what motivated the inversion.

CONVERSATIONAL_OPENERS = (
    "how are you", "good morning", "good night", "thank you", "thanks",
    "hello", "hey there", "what's up", "never mind", "you're welcome",
)

RECENCY_SIGNALS = (
    "news", "latest", "current events", "what happened", "score",
    "stock", "headline",
)

DEPTH_SIGNALS = (
    "explain why", "compare", "walk me through", "pros and cons",
    "what would happen if", "help me understand",
)

ADVICE_SHAPES = (
    "should i", "what do you think about", "is it worth", "recommend",
)


def is_small_talk(text: str) -> bool:
    """True when the utterance is phatic conversation, not a request.

    Checked BEFORE any recency/depth signal. Without this ordering,
    "how are you doing today" matches a recency signal ("today") and
    a greeting gets billed as a paid web-search call , an actual bug
    from the production audit, not a hypothetical.
    """
    lowered = text.lower().strip()
    return any(lowered.startswith(op) or op in lowered
               for op in CONVERSATIONAL_OPENERS)


def pick_model(text: str) -> Tier:
    """Route one utterance to its tier. Order encodes the design.

    Note what is ABSENT: no command-keyword matching. Recognized
    commands never reach this function , a deterministic pre-router
    intercepts them upstream and dispatches directly to handler
    functions in under 50ms with zero inference. pick_model() only
    sees what the pre-router declined to claim.
    """
    lowered = text.lower()

    # 1. Conversational short-circuit , cheapest tier, checked first.
    if is_small_talk(lowered):
        return Tier.FAST

    # 2. Recency: the local models' knowledge is frozen at training
    #    time; anything time-sensitive needs live search.
    if any(sig in lowered for sig in RECENCY_SIGNALS):
        return Tier.CLOUD

    # 3. Depth: multi-step reasoning shapes outrun an 8B's ceiling.
    if any(sig in lowered for sig in DEPTH_SIGNALS):
        return Tier.CLOUD

    # 4. Advice-shaped questions: judgment calls route to the tier
    #    best equipped to reason about tradeoffs.
    if any(lowered.startswith(sig) or f" {sig} " in lowered
           for sig in ADVICE_SHAPES):
        return Tier.CLOUD

    # 5. The inverted default. An unrecognized phrasing lands on the
    #    model that can ACT , and that can emit a typed ESCALATE
    #    sentinel when it knows it can't (see constrained_decoding.py),
    #    which the dispatcher converts into an automatic cloud
    #    escalation. Fabrication is no longer a reachable state.
    return Tier.DEEP


if __name__ == "__main__":
    # A slice of the regression matrix that gates every routing change.
    cases = [
        ("how are you doing today",            Tier.FAST),   # the cost-leak case
        ("what's the latest news",             Tier.CLOUD),
        ("explain why the sky is blue",        Tier.CLOUD),
        ("should i take the highway tonight",  Tier.CLOUD),
        ("add a test event to my calendar",    Tier.DEEP),   # the fabrication case
        ("make it feel cozier in here",        Tier.DEEP),   # no keyword , still lands safe
    ]
    for utterance, expected in cases:
        got = pick_model(utterance)
        status = "PASS" if got is expected else "FAIL"
        print(f"[{status}] {utterance!r:44s} -> {got.value}")
