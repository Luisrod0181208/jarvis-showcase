# Design Deep-Dive: Suppress-Not-Drop

*Two microphones, adjacent rooms, one wake word, and why verifying before suppressing beats trusting your own sensors.*

## The Problem

JARVIS listens in two rooms through independent voice nodes: a primary pipeline in the bedroom and a satellite in the living room that forwards captured audio to the primary machine for processing. Apartment walls are acoustically generous. A command spoken in the living room is frequently loud enough to trip the bedroom microphone as well.

Without coordination, every living-room command would be processed twice; two transcriptions, two dispatches, two spoken responses overlapping each other. The naive fix is suppression: use the millimeter-wave presence sensors to determine which room is occupied, and have the unoccupied room's pipeline discard what it heard.

The naive fix shipped, and commands started vanishing.

## The Investigation

The bedroom pipeline's suppression gate queried the presence sensors directly: living room occupied, bedroom vacant, therefore the satellite must have this one, discard. The assumption baked into that logic is the bug: occupancy tells you where the human is, not whether the satellite actually captured usable audio. The satellite's microphone session can be stale, its host can be mid-restart, the network POST can fail, any of which means the bedroom pipeline just discarded the only good copy of the command.

The failure was silent by construction. I spoke, both pipelines made locally reasonable decisions, and nothing happened. No error, no feedback, no log line that looked wrong in isolation. Debugging required correlating logs across two machines to notice that the bedroom said "suppressed, satellite has it" at timestamps where the satellite received nothing at all.

## The Decision

Two changes: one philosophical and one mechanical.

The philosophy became a named principle: **suppress-not-drop**. A duplicated command is a minor annoyance, the second dispatch is usually idempotent, and at worst the user hears two confirmations. A dropped command is a broken product. Every suppression decision must therefore be backed by *positive evidence* that the other pipeline has the audio, never by inference from occupancy alone. When evidence is absent, fall through and process, accept the duplicate.

The mechanism is a lightweight handshake through shared memory. The satellite receiver stamps a timestamp file the moment its utterance handler begins; before transcription, before anything that can fail slowly. The bedroom pipeline's suppression gate, instead of trusting the sensors, polls that stamp: three attempts, spaced under a second apart, requiring the stamp to be fresh within a twelve-second window. A fresh stamp is proof the satellite received the audio, and suppression proceeds. No fresh stamp after three attempts means the gate logs a suppress-skip and falls through to normal processing and finally, the command survives.

## The Tradeoff

The handshake buys correctness with a small latency tax on exactly one path: when the bedroom is about to suppress, it may wait up to roughly two and a half seconds confirming the satellite's receipt before discarding. The happy path is a fresh stamp on the first poll and costs almost nothing. Genuine room-transition overlaps, where both sensors read occupied, still produce occasional double-processing, and that is the accepted cost of the principle: the system is explicitly biased so that its residual failure mode is the harmless one. Distributed suppression, it turns out, is a harder problem than distributed detection because detection failures announce themselves and suppression failures are silence.
