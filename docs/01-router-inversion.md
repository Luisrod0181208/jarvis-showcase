# Design Deep-Dive: The Router Inversion

*How a silent fabrication bug forced a rethink of the default routing path, and why "route to the capable model unless proven otherwise" is better than "route to the capable model when a keyword matches."*

## The Problem

JARVIS routes each utterance to one of three AI tiers: a local 3B model (FAST) for conversation, a local 8B model (DEEP) for function-calling device commands, and a hosted frontier model (CLOUD) for web search and complex reasoning. The original router worked by keyword recognition: if the utterance matched a known command vocabulary e.g. "lights," "thermostat," "timer", it would route to DEEP, which can call functions. Everything else fell through to FAST.

The failure appeared quietly. A request like "add a test event to my calendar", phrased without any listed keyword, fell through to FAST. FAST cannot call functions. It also, being a small conversational model, has no instinct to say so. It responded with a cheerful, entirely fabricated confirmation: "Done, sir. I've added that to your calendar." Nothing was added, no error was surfaced and I would walk away believing a calendar entry existed.

This is the worst class of failure a voice assistant can have: not a refusal, not a crash, but a confident lie about a completed action.

## The Investigation

Auditing the routing logs revealed the keyword list could never win this game. Natural language generates unbounded phrasings for the same intent; every unlisted phrasing was a fabrication window. Patching the list after each miss was a treadmill, and each miss was invisible until the user noticed a light still on or an event that never existed.

A second, opposite failure surfaced in the same audit: casual conversation leaking to the paid tier. "How are you doing today" contains "today," which matched a recency signal intended to catch "what happened in the news today" which then caused small talk to be intermittently billed as a cloud call.

Both bugs shared a root cause: the router made its most consequential decision (can this request touch the real world?) on the weakest evidence (substring presence).

## The Decision

Invert the default. The cascade now runs in this order:

1. **Conversational short-circuit.** A dedicated small-talk detector (greetings, phatic phrases, acknowledgments) routes to FAST first, checked before any recency or depth signal, closing the cost leak.
2. **Genuine recency and depth signals** (news, current events, multi-step reasoning shapes, advice-seeking phrasings) route to CLOUD.
3. **Everything else routes to DEEP.**

The inversion means an unrecognized phrasing now lands on the model that can act and can structurally escalate, rather than the model that can only improvise. DEEP's constrained JSON output (see `excerpts/constrained_decoding.py`) gives it a typed way to say "I can't handle this", an `ESCALATE` sentinel with a reason which the dispatcher then converts into an automatic cloud escalation. The path for a missed phrasing changed from *fabricated confirmation* to *correct execution or honest escalation*.

A 24-case regression suite became the deploy gate: every routing change must pass the full matrix of known-tricky utterances before it ships, and every new intercept ships with new cases.

## The Tradeoff

DEEP is slower than FAST, so ambiguous chit-chat that escapes the conversational detector now pays 8B latency instead of 3B latency. The conversational signal list requires maintenance, though maintaining a conversation allowlist is a far better position than maintaining a command allowlist, because the conversational domain is small and stable while the command domain is unbounded. The deeper lesson: when one failure mode is mild latency and the other is a confident fabrication, the default should always absorb the latency.
