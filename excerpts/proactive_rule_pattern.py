"""
proactive_rule_pattern.py , the proactive behavior engine (publication excerpt)

Rewritten from the live system for public release. The assistant initiates instead
of responding: arrival briefings, session-break nudges,
hydration reminders, lights-left-on alerts. Every proactive behavior is
one class registered into a single dispatcher, and this file shows the
whole pattern: the event dataclass, the rule contract, the registration
decorator, the dispatcher's gate ordering, and one representative rule.

The design goal is that adding a behavior costs exactly one class; no
scheduler edits, no dispatcher edits, and no wiring.
"""

import time
from dataclasses import dataclass, field


# ── The event a rule emits when it decides to act ────────────────────────

@dataclass
class ProactiveEvent:
    rule_id: str
    spoken: str | None = None          # TTS line, if any
    ntfy_title: str | None = None      # push-notification title
    ntfy_body: str | None = None       # push-notification body
    channels: tuple = ("speak", "ntfy")


# ── The rule contract ────────────────────────────────────────────────────

class ProactiveRule:
    """Base class. Subclasses declare their cadence and gating policy as
    class attributes and implement evaluate().

    rule_id         stable identifier (logs, cooldown persistence)
    interval_s      how often the dispatcher calls evaluate()
    cooldown_s      minimum silence after a fire (0 = rule manages its own)
    quiet_hours_ok  may this rule fire during configured quiet hours?
    respect_modes   is this rule silenced by wind-down / night modes?

    The two booleans are POLICY declarations consumed by the dispatcher,
    not behavior the rule implements. A rule that must keep updating
    internal state every tick (e.g. an activity tracker) declares itself
    exempt from the gates and applies suppression internally AFTER its
    state update , otherwise the gates would freeze its tracker all night.
    """

    rule_id: str = "base"
    interval_s: int = 60
    cooldown_s: int = 300
    quiet_hours_ok: bool = False
    respect_modes: bool = True

    def evaluate(self, snap: dict) -> ProactiveEvent | None:
        """Inspect the state snapshot; return an event to act, else None.

        `snap` is a read-only snapshot assembled once per dispatcher tick
        (occupancy, presence, lights, calendar, weather...). Rules never
        query devices directly , one snapshot per tick keeps every rule's
        view consistent and keeps sensor traffic constant no matter how
        many rules exist.
        """
        raise NotImplementedError


# ── Registration: one decorator, zero wiring ─────────────────────────────

_RULES: list[ProactiveRule] = []


def register(cls):
    """@register on a subclass instantiates it into the dispatch table."""
    _RULES.append(cls())
    return cls


# ── The dispatcher (sketch) ──────────────────────────────────────────────

def dispatch_tick(snap: dict, state: dict, now: float | None = None) -> None:
    """One scheduler tick. Gate order is deliberate and cheap-first:

        1. interval   , is this rule due?
        2. cooldown   , did it fire too recently?
        3. quiet hrs  , policy gate (unless rule opted out)
        4. modes      , wind-down / night silence (unless opted out)
        5. evaluate() , the rule's own logic, LAST and most expensive
    """
    now = now or time.time()
    for rule in _RULES:
        last_run = state.setdefault("last_run", {}).get(rule.rule_id, 0)
        if now - last_run < rule.interval_s:
            continue
        state["last_run"][rule.rule_id] = now

        last_fire = state.setdefault("last_fire", {}).get(rule.rule_id, 0)
        if rule.cooldown_s and now - last_fire < rule.cooldown_s:
            continue
        if not rule.quiet_hours_ok and snap.get("quiet_hours"):
            continue
        if rule.respect_modes and snap.get("restful_mode"):
            continue

        event = rule.evaluate(snap)
        if event:
            state["last_fire"][rule.rule_id] = now
            _deliver(event)  # TTS + push, per event.channels


def _deliver(event: ProactiveEvent) -> None:
    print(f"[{event.rule_id}] {event.spoken or event.ntfy_body}")


# ── One representative rule ──────────────────────────────────────────────

@register
class SessionBreakNudge(ProactiveRule):
    """Suggests a break after sustained desk activity.

    Two guards beyond the dispatcher gates, because a wellness nudge
    that misfires teaches the user to ignore every future nudge:

      * ACTIVE-NOW: fire only if activity was seen within the last few
        minutes , never nudge someone who already stopped on their own.
      * FIRE CAP: at the threshold and once more at double it, then
        silence. Nagging is a failure mode, not a feature.
    """

    rule_id = "session_break"
    interval_s = 60
    cooldown_s = 0          # session-scoped logic below; a cooldown would fight it
    quiet_hours_ok = False
    respect_modes = True

    SESSION_MIN = 90        # minutes before the first nudge
    ACTIVE_NOW_S = 180      # "still at it" freshness window
    MAX_FIRES = 2

    def __init__(self):
        self._fired = 0

    def evaluate(self, snap):
        start = snap.get("session_start")
        if not start or self._fired >= self.MAX_FIRES:
            return None

        minutes = (time.time() - start) / 60
        if minutes < self.SESSION_MIN * (self._fired + 1):
            return None
        if time.time() - snap.get("last_active", 0) > self.ACTIVE_NOW_S:
            return None  # they already stopped , stay silent

        self._fired += 1
        span = f"{minutes / 60:.1f} hours" if minutes >= 60 else f"{int(minutes)} minutes"
        return ProactiveEvent(
            self.rule_id,
            spoken=f"You've been at it for {span}. A short break might do you good.",
            ntfy_title="Assistant",
            ntfy_body=f"Active session: {span} , time to stretch",
        )


if __name__ == "__main__":
    state: dict = {}
    snap = {
        "quiet_hours": False,
        "restful_mode": False,
        "session_start": time.time() - 95 * 60,   # 95 minutes in
        "last_active": time.time() - 30,          # active seconds ago
    }
    dispatch_tick(snap, state)
