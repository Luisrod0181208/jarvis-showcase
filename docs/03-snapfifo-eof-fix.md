# Design Deep-Dive: The FIFO That Died at First Silence

*A multi-room audio outage traced to POSIX pipe semantics & the three-line service that fixed it permanently.*

## The Problem

JARVIS distributes synthesized speech to multiple rooms through Snapcast: the TTS engine writes raw PCM into a named pipe (FIFO) on the primary machine, the Snapcast server reads from that pipe and streams to clients over the network, and each room's client plays the audio locally.

The symptom: multi-room audio worked flawlessly after every reboot, then at some unpredictable point later in the day it would die completely and stay dead until the Snapcast server was manually restarted. TTS kept writing, the server kept running, the clients stayed connected and still no audio.

## The Investigation

The trigger turned out to be the gap between speech events, not the speech itself. Named pipes follow classic POSIX semantics: a reader blocked on a FIFO receives end-of-file the moment the last writer closes its end. The TTS process opens the pipe, writes an utterance, and closes it, the correct behavior for a short-lived writer. The instant that close happens with no other writer holding the pipe open, the Snapcast server's reader sees EOF.

The server's pipe-stream implementation treats that EOF as the stream ending, reasonably, for its original use case of piped media players that run continuously. It transitions the stream to a dead state from which it never recovers. Every subsequent TTS write goes into a pipe nobody is reading in a way that will ever reach a speaker again. The system was architecturally sound and semantically doomed: any silence long enough for the writer count to hit zero was a permanent kill switch.

This explained the "works after reboot" pattern perfectly. Boot sequences produce a burst of startup speech; the failure waited for the first genuinely quiet stretch.

## The Decision

Keep a writer alive forever. A minimal systemd unit, the FIFO holder, opens the pipe with `O_RDWR` and simply holds the descriptor for the life of the system. With the holder's write end permanently referenced, the writer count never reaches zero, the server's reader never sees EOF, and the stream never dies. The TTS engine continues opening and closing the pipe per-utterance exactly as before; its closes no longer matter.

The `O_RDWR` detail is the quiet elegance here. Opening a FIFO write-only blocks until a reader exists, which would create a startup ordering dependency between the holder and the server. Opening read-write on a FIFO never blocks and keeps both ends referenced, so the holder starts cleanly regardless of ordering (though the restart procedure is still documented as holder-then-server) and the health-check registry verifies all three links (holder active, server active, client connected via the server's JSON-RPC interface) rather than trusting any single one.

## The Tradeoff

One more unit to supervise, one more line in the health check, a file descriptor held open forever, against an audio path that previously contained an unrecoverable failure state triggered by ordinary silence. The economics aren't close. The transferable lesson sits a level up: when a subsystem "works and then permanently stops," suspect a one-way state transition, and go read the semantics of the boundary primitive; the pipe, the socket, the session, before touching the application code on either side of it. The bug was never in anything I wrote; it lived in the space between two correct programs.
