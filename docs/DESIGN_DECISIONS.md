# Design Decisions

This document records the reasoning behind the real-time core. Each entry is a
lightweight architecture decision record: context, decision, alternatives, and
consequences.

---

## ADR-001: Build the decision core as a separate, dependency-free package

**Context.** The original MGIA prototype depends on notebook-era vision and LLM
tooling. That makes the safety logic hard to run, review, and test.

**Decision.** Add `mgia_realtime/` with zero third-party runtime dependencies
and leave the original `src/`, `config/`, `data/`, and `test/` trees intact.

**Alternatives.** Refactor the notebook modules in place; wrap the old code
behind optional imports; ship a Docker-only demo.

**Consequences.** Reviewers and CI can run the real logic with `python3`
alone. The new package does not exercise the old vision stack, so the
multimodal prototype is preserved as context rather than integrated. The
trade-off is deliberate: reproducibility and auditability over breadth.

---

## ADR-002: Normalise at the boundary with immutable dataclasses

**Context.** Perception payloads are noisy: missing depth, unknown labels,
duplicate IDs, inconsistent field names.

**Decision.** `PerceptionEvent`, `DetectedObject`, and `Hazard` are frozen
slotted dataclasses validated in `__post_init__`. A tolerant JSON parser accepts
`timestamp`/`id`/`distance` aliases and known enum spellings, with explicit
fallbacks for unknown labels.

**Alternatives.** Pass dictionaries through the pipeline; use Pydantic; validate
only in the CLI.

**Consequences.** Invalid frames fail early with `InvalidEventError`, and
downstream stages never branch on missing keys. Slots reduce per-frame
allocation. The parser is permissive about unknown classes but strict about
structural errors such as duplicate IDs and dangling hazard references.

---

## ADR-003: Use a TTC heuristic instead of a learned risk model

**Context.** A risk score must be fast, explainable, and usable without a GPU or
a model artifact. The system also has to behave when distance is missing.

**Decision.** Combine time-to-contact, absolute clearance, class weight,
confidence, position, and approach direction into a bounded 0–1 score. Missing
distance uses a conservative fixed proximity rather than a zero score.

**Alternatives.** Train a classifier; ask an LLM to rate danger; use a Kalman
filter and full trajectory prediction.

**Consequences.** Every score can be explained in one sentence, and tests can
assert exact boundaries. The heuristic is not a certified collision predictor and
assumes relative speed is accurate. A production system should calibrate it
against sensor characteristics and real trajectories.

---

## ADR-004: Hysteresis with one-step de-escalation

**Context.** Alert flapping interrupts speech and trains users to ignore alerts.
A single high-risk frame should not keep the system at CRITICAL forever, but an
instant drop to CLEAR is also disorienting.

**Decision.** Escalate immediately. De-escalate only after the score crosses a
lower exit threshold **and** the current state has lasted `min_dwell_s`, and move
one level at a time. Exit thresholds (0.18/0.45/0.70) sit below enter thresholds
(0.25/0.55/0.80).

**Alternatives.** Symmetric thresholds; a fixed cooldown per level; exponential
smoothing of the score.

**Consequences.** Urgent events are never delayed. Recovery takes at least two
frames from CRITICAL to CLEAR, which is intentional. The dwell timer resets on
every transition. Thresholds are configurable but validated to preserve ordering.

---

## ADR-005: At most one utterance start per frame tick

**Context.** Concurrent speech produces overlapping audio, nondeterministic
ordering, and misleading latency numbers.

**Decision.** `SpeechScheduler.tick(now)` returns zero or one `SpokenMessage`.
Within the queue, a heap orders by priority and then sequence number. The
pipeline calls `tick` once per processed frame.

**Alternatives.** A background TTS thread; a continuous scheduler loop; speak
every queued message immediately.

**Consequences.** Ordering and latency are deterministic and easy to test.
Throughput is bounded by frames and speech duration. The host may call `tick`
more often if it has a faster control loop, but the safety guarantees remain
local to each call.

---

## ADR-006: Preemption only for strictly higher priority, with a rate guard

**Context.** A CRITICAL stop instruction must not wait behind a low-priority
scanning note. But allowing every message to interrupt would create a stream of
truncated utterances.

**Decision.** A candidate preempts only when its priority is strictly greater.
Non-critical preemption also requires the minimum spacing between utterance
starts. CRITICAL bypasses spacing. Deduplication and cooldowns still apply.

**Alternatives.** Preempt on any new message; never preempt; weight by remaining
speech duration.

**Consequences.** "Stop now" wins, but ordinary warnings cannot machine-gun the
channel. Preempted messages are counted, not requeued automatically. A future
version could resume an interrupted low-priority message after the urgent one,
at the cost of more state.

---

## ADR-007: Cooldown starts when speech ends

**Context.** A cooldown measured from utterance start can expire while a long
warning is still being spoken, causing an immediate repeat.

**Decision.** Store `cooldown_until = estimated_speech_end + cooldown_s`. The
duration estimate is `max(0.7 s, words / 2.6)`, used only for occupancy.

**Alternatives.** Fixed post-message delay; timestamp of last start; no cooldown.

**Consequences.** Repetition is suppressed for the full utterance plus the
quiet window. The word-rate estimate is not a TTS latency claim; a real engine
should report actual completion.

---

## ADR-008: Profiles gate priority; phrasing changes separately

**Context.** Users have different tolerances for speech. Accessibility is not
one setting.

**Decision.** Concise admits HIGH/CRITICAL, standard admits NORMAL+, verbose
admits LOW+. The tip policy changes phrasing per profile: core instruction,
core plus context cue, or core plus cue plus measured detail.

**Alternatives.** One verbosity setting implemented by truncating text; a
separate profile per hazard type; user-defined templates at runtime.

**Consequences.** Concise mode suppresses scanning chatter while preserving
warnings. Because the dedupe key excludes profile, changing profiles does not
create duplicate cooldown buckets. Template coverage is finite and English-only.

---

## ADR-009: Deterministic in-memory metrics

**Context.** Latency percentiles and dropped-message counts are needed for
review and debugging, but telemetry must not block the safety path.

**Decision.** `SessionMetrics` stores integer counters and a latency sample list.
Percentiles use linear interpolation; an empty sample set returns `None`.
Snapshots are frozen dataclasses with a JSON-safe export.

**Alternatives.** Streaming approximations (t-digest); Prometheus client;
logging every event.

**Consequences.** Exact assertions in tests and a readable demo summary. Memory
grows with spoken messages; for long sessions, the host should snapshot and
reset. Returning `None` avoids the common but misleading "0 ms" for no data.

---

## ADR-010: Synthetic, deterministic demo instead of real footage

**Context.** A portfolio demo must run anywhere without downloading models or
media, and must not imply field validation.

**Decision.** `demo.py` builds a ten-frame walking scenario with an approaching
pedestrian, an annotated crossing hazard, a critical escalation, and recovery.

**Alternatives.** Bundle a video; call a hosted model; record a browser demo.

**Consequences.** Every run is identical and CI-safe. The demo exercises the
full path including preemption and de-escalation, but it is not evidence of
real-world accuracy. Wall-clock latency and sensor noise remain unmeasured.

---

## ADR-011: Single-file test suite with exact branch coverage

**Context.** Safety behavior is dominated by boundaries and state transitions.

**Decision.** Keep the runtime suite in `tests/test_mgia_realtime.py`, organized
by subsystem, and require 100% line and branch coverage for `mgia_realtime`.
Exclude empty legacy placeholders from the default collection path.

**Alternatives.** Many small files; line-only coverage; integration tests
against the notebook environment.

**Consequences.** The gate is strict and fast (under two seconds locally). A
single large file is a trade-off for reviewability; it is grouped by classes so
navigation remains straightforward. The legacy suite is explicitly not claimed
to pass.

---

## Rejected idea: LLM-generated safety phrasing in the hot path

An LLM could produce more natural tips, but it adds variable latency, network
dependence, nondeterminism, and a new failure mode in a safety loop. The
original prototype explores multimodal prompting; the real-time core keeps
phrasing deterministic. A future design could use an LLM offline to author
templates that are reviewed and tested, while the hot path stays rule-based.
