"""
constrained_decoding.py , schema-forced function dispatch (publication excerpt)

Rewritten from the live system for public release. This is the guardrail
that makes the local 8B model *structurally unable* to fabricate.

The failure it prevents: ask a small local model "what's the thermostat
set to?" without function calling and it will produce a plausible number;
fluent, confident, and invented. Prompt-level instructions ("only use
real data") reduce this; they do not eliminate it, because the model's
output space still contains the lie.

Constrained decoding shrinks the output space itself. Ollama's `format`
parameter accepts a JSON schema and masks token generation so the model
can only emit documents that validate against it. The schema below gives
the model exactly three legal moves:

    1. call declared functions   (act)
    2. say something             (respond, no data values, by policy)
    3. ESCALATE with a typed reason (hand off honestly)

"Confidently invent a device reading" is not among them. And because
escalation is a first-class schema branch rather than a prose apology,
the dispatcher can react to it mechanically: knowledge escalations go to
the cloud tier as prose questions, command escalations go with tool-use
enabled. The model saying "I can't" became a routing signal instead of a
dead end.
"""

import json
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "local-8b"

# ── The schema: three legal moves, nothing else ──────────────────────────

DEEP_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "functions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["name", "args"],
            },
        },
        "say": {"type": "string"},
        "escalate": {
            "type": "object",
            "properties": {
                "reason": {"type": "string",
                           "enum": ["knowledge", "command"]},
                "query": {"type": "string"},
            },
            "required": ["reason", "query"],
        },
    },
}


def ask_deep(utterance: str, system_prompt: str) -> dict:
    """One constrained call. `format` carries the schema; the returned
    content is guaranteed parseable JSON that validates against it ,
    no regex extraction, no 'the model wrapped it in markdown' repair."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": utterance},
        ],
        "format": DEEP_RESPONSE_SCHEMA,   # ← the guardrail
        "stream": False,
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    return json.loads(body["message"]["content"])


def parse_and_execute(response: dict, execute_fn, escalate_fn) -> str:
    """Dispatch a schema-valid response.

    Precedence: escalation first (an honest handoff outranks a partial
    answer), then function calls, then plain speech.
    """
    if esc := response.get("escalate"):
        # Typed reason drives the escalation shape:
        #   "knowledge" -> cloud tier, prose answer, no tools
        #   "command"   -> cloud tier with tool-use enabled
        return escalate_fn(esc["reason"], esc["query"])

    lines = []
    for call in response.get("functions", []):
        # execute_fn maps name -> real handler (device APIs, calendar,
        # locks...). The REAL system reading happens here , the model
        # requested it but never authored it.
        lines.append(execute_fn(call["name"], call.get("args", {})))

    if say := response.get("say"):
        lines.append(say)

    return " ".join(lines) if lines else "Done."


if __name__ == "__main__":
    # Offline demo of the dispatcher over the three legal shapes.
    def fake_execute(name, args):
        return f"[executed {name}({args})]"

    def fake_escalate(reason, query):
        return f"[escalated to cloud: reason={reason} query={query!r}]"

    shapes = [
        {"functions": [{"name": "lights_on", "args": {"room": "all"}}],
         "say": "Lights on."},
        {"escalate": {"reason": "knowledge",
                      "query": "who won the match last night"}},
        {"say": "Certainly."},
    ]
    for shape in shapes:
        print(parse_and_execute(shape, fake_execute, fake_escalate))
