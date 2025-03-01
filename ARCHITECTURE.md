# Architecture

## Scope

`mgia_realtime` is the deterministic decision core of MGIA. It starts after
perception and ends before text-to-speech. The package intentionally excludes
camera capture, model inference, audio playback, and network I/O so that safety
behavior can be tested and reviewed without hardware or heavy dependencies.

```text
perception adapter ──► PerceptionEvent ──► RiskScorer ──► AlarmStateMachine
                                                              │
                                                              ▼
                                                        SafetyTipPolicy
                                                              │
                                                              ▼
                                                     SpeechScheduler
                                                              │
                                                              ▼
                                                    list[Guidance]
                                                              │
                                                              ▼
                                              SessionMetrics / CLI output
```

## Stage responsibilities

| Stage | Module | Input | Output | Key invariant |
| --- | --- | --- | --- | --- |
| Normalisation | `events.py` | JSON / typed objects | `PerceptionEvent` | Immutable, validated, fixed object order |
| Risk | `risk.py` | Event | `RiskAssessment` | Score in `[0, 1]`, deterministic ties by `object_id` |
| Alarm | `alarm.py` | Score + time | `AlarmTransition` or `None` | Escalation immediate; de-escalation hysteretic |
| Tips | `tips.py` | Assessment + level + context | `Tip` or `None` | Hazard-specific, profile-aware, anti-repetition |
| Speech | `speech.py` | `SpeechMessage` + clock | `SpokenMessage` | One start per tick, priority ordered, cooldown bounded |
| Metrics | `metrics.py` | Stage events | `MetricsSnapshot` | Counters and latency samples are exact |
| Orchestration | `pipeline.py` | Frames | `Guidance` | Non-decreasing timestamps, max one message per frame |

## Frame schema

A frame is a JSON object. `timestamp` is accepted as an alias for
`timestamp_s`; `id`/`distance`/`relative_speed`/`bearing` are accepted as aliases
inside objects.

```json
{
  "timestamp_s": 2.0,
  "frame_id": 2,
  "context": "walking",
  "objects": [
    {
      "object_id": "ped-1",
      "class": "pedestrian",
      "confidence": 0.92,
      "distance_m": 2.4,
      "relative_speed_mps": 1.8,
      "bearing_deg": -18.0,
      "position": "left"
    }
  ],
  "hazards": [
    {"kind": "pedestrian_path", "confidence": 0.9, "object_id": "ped-1"}
  ]
}
```

- `context`: `walking`, `transit`, or `crossing` (unknown values fall back to
  `walking`).
- `class`: pedestrian, vehicle, cyclist, obstacle, road_sign, kerb, door,
  unknown (unknown labels fall back to `unknown`).
- `position`: left, center, right, unknown.
- `hazard.kind`: pedestrian_path, vehicle_conflict, cyclist_path, obstacle,
  road_edge, door, signal, other.
- `distance_m` may be `null`/omitted when depth is unavailable.
- `relative_speed_mps` is positive when approaching.
- Hazard `object_id` must match an object in the same frame if supplied.
- Object IDs must be unique within a frame.

The top-level document may be a list or `{"frames": [...]}`.

## Risk scoring

For an object with known distance and positive relative speed:

```text
TTC        = distance / relative_speed
proximity  = clamp((horizon - TTC) / horizon, 0, 1)
score      = confidence
           × class_weight
           × (base + (1 − base) × proximity)
           × position_factor
           × approach_factor
score      = clamp(score + hazard_boost)
```

- Horizon defaults to 3 s, base to 0.10, class weights 0.4–1.0.
- Position factors: center 1.0, left/right 0.88, unknown 0.72.
- Approach factors: approaching 1.0, static 0.7, receding 0.45.
- With no positive approach speed, proximity falls back to absolute clearance
  against an 8 m static clearance distance.
- Missing distance uses a conservative proximity of 0.55 and marks
  `distance_known = false`.
- An annotated hazard adds `0.1 × confidence × hazard_weight`, capped at 1.0.
  A hazard with no tracked object produces a base score of
  `0.5 × confidence`.

The frame score is the maximum object score plus any hazard boost. Object risks
are sorted by `(-score, object_id)` so output is stable when scores tie.

## Alarm hysteresis

Default enter thresholds are 0.25 / 0.55 / 0.80. Default exit thresholds are
0.18 / 0.45 / 0.70. `min_dwell_s` defaults to 0.5 s.

- A higher target level is entered immediately.
- A lower target level is ignored while the score remains at or above the
  exit threshold for the current level.
- Even below the exit threshold, a downgrade waits until the dwell time has
  elapsed.
- Downgrades move exactly one level per update. This gives speech and the user
  time to process the change instead of dropping from CRITICAL to CLEAR in one
  frame.

`AlarmStateMachine` has no clock of its own: callers pass timestamps. This makes
tests exact and allows replay at arbitrary speed.

## Speech scheduling

`SpeechScheduler` uses a binary heap ordered by `(-priority, sequence)`, giving
FIFO behavior within a priority level.

1. **Profile gate** — concise admits HIGH/CRITICAL, standard admits NORMAL+,
   verbose admits LOW+.
2. **Expiry** — messages past `expires_at_s` are dropped.
3. **Active duplicate** — the same dedupe bucket already speaking is suppressed.
4. **Cooldown** — a bucket cannot speak again until its previous utterance has
   finished plus `cooldown_s`.
5. **Coalescing** — a queued message with the same bucket is merged; the newer
   message wins when priority is equal or higher.
6. **Queue pressure** — at `max_pending`, a lower-or-equal-priority arrival is
   dropped; a higher-priority arrival evicts the lowest queued message.
7. **Emission** — at most one message starts per `tick`. A higher-priority
   candidate preempts the active utterance if the speaking-rate spacing allows
   it, or unconditionally when CRITICAL.

Cooldown starts when the utterance ends, not when it begins, so long warnings
do not immediately repeat.

## Ordering and latency

- Pipeline timestamps must be non-decreasing. A late frame raises
  `OutOfOrderFrameError`; it is never silently reordered.
- Every emitted `Guidance` carries the frame ID that caused it.
- Latency is `emission_time − message_creation_time` (creation is the source
  frame timestamp).
- Session latency percentiles use linear interpolation over stored samples.
- The demo clock is synthetic. Zero-millisecond latency in the demo is not a
  hardware measurement.

## Concurrency model

The core is synchronous and single-threaded. That is a deliberate choice:

- deterministic tests and replay;
- no lock ordering or race conditions in the safety path;
- preemption is an explicit priority comparison, not thread interruption.

A production host can run the pipeline on a dedicated worker and feed frames
from a bounded queue. Backpressure policy belongs at the host boundary, not in
the deterministic decision core.

## Extension points

- Feed `PerceptionEvent` from a camera/depth adapter.
- Implement `render` around `Guidance` for a TTS engine or haptic output.
- Add profile-specific phrasing in `tips.py` (keep templates deterministic).
- Tune `RiskConfig` / `AlarmConfig` per device, then re-run the coverage suite.
- Export `MetricsSnapshot.to_dict()` to a telemetry sink.
