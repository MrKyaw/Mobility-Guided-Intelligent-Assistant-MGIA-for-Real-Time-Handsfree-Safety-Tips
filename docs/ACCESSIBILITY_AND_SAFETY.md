# Accessibility and Safety

## Status

This repository is an **engineering prototype**. It is not a medical device,
not a certified assistive navigation system, and not a replacement for a cane,
guide dog, human assistant, or manufacturer-provided vehicle safety system. It
must not be used as the sole source of hazard information in real mobility.

## Accessibility intent

MGIA's speech layer is designed around a few practical accessibility principles:

- **Respect attention.** The system does not narrate every detection. Profiles
  let a user choose how much context they hear.
- **Never bury an urgent instruction.** CRITICAL guidance can preempt a
  lower-priority utterance, including bypassing the speaking-rate guard.
- **Do not repeat mechanically.** A dedupe key plus a cooldown prevents the same
  warning from looping while a hazard persists. The tip policy also alternates
  phrasing so a persistent hazard does not sound like a stuck recording.
- **Give recovery time.** De-escalation waits for a lower exit threshold and a
  minimum dwell time, and drops only one level at a time. This avoids abrupt
  "critical, clear, critical" swings.
- **Be predictable.** The pipeline is deterministic and single-threaded. Given
  the same frames, it produces the same guidance order.

### Verbosity profiles

| Profile | Minimum priority | Intended use |
| --- | --- | --- |
| Concise | HIGH | Users who want warnings and stop instructions only |
| Standard | NORMAL | Default: warnings plus low-level awareness |
| Verbose | LOW | Development, debugging, or users who want detail |

WATCH-level context is intentionally suppressed in concise mode. That is an
accessibility trade-off: less noise, but less situational awareness. It should
be a user choice, not a default imposed on everyone.

### Text guidance, not audio

The tested package emits text (`Guidance.text`). It does not synthesize audio.
A host integrating a TTS engine must consider:

- speech rate and language;
- overlap and ducking with other audio;
- announcement latency on the target device;
- interruption behavior when the user is speaking;
- haptic or visual fallback for users who cannot hear audio.

None of those are implemented or validated here.

## Safety limitations

### Not validated with users or hardware

There is no user study, field trial, clinical evaluation, or hardware-in-the-loop
test. The demo is a synthetic ten-frame scenario. Claiming real-world
effectiveness from it would be false.

### Perception assumptions

The risk score assumes that:

- object class and confidence are meaningful;
- relative speed is accurate and signed correctly;
- distance, when present, is in metres and reasonably calibrated;
- the frame timestamp reflects perception time.

Real depth sensors are noisy, occluded, and biased. A pedestrian at 2 m with a
bad depth estimate could be scored as far away. Missing distance is handled
conservatively, but wrong distance is not detectable by this layer.

### TTC is a heuristic

Time-to-contact is computed as `distance / relative_speed`. It assumes constant
relative velocity, a single tracked object, and no acceleration. It is not a
certified collision predictor and does not model vehicle dynamics, road geometry,
or multiple interacting agents.

### Rule-based phrasing

Templates are English-only, hand-written, and not localized. They do not adapt
to dialect, hearing ability, cognitive load, or cultural context. "Left" and
"right" are relative to the perception frame, not necessarily the user's body.
The demo assumes the frame is forward-facing.

### Logical clock, not wall-clock performance

The demo and tests use timestamps supplied by frames. Frame creation and
emission often share a timestamp, so reported latency can be exactly `0.0 ms`.
That is an ordering check, not evidence of device latency. No real-time
deadline, jitter, or worst-case execution time is claimed. Real deployments
must measure end-to-end latency on the target hardware, including perception,
scheduling, and TTS.

### Failure modes

The core raises explicit errors for malformed frames and out-of-order input. It
does not silently drop a late frame, because that could hide a hazard. A host
must decide how to fail safe: stop, fall back to a simpler alert, or hand control
to the user. The package does not implement a watchdog, health check, or
degraded-mode policy.

## Privacy and data handling

The tested core processes structured events only. It does not capture images,
audio, or location. The CLI reads a local JSON file and writes text/JSON to
stdout. The original prototype's `.env` configuration is preserved but is not
used by `mgia_realtime`; no network calls are made.

A production deployment should still consider:

- whether frames contain identifiable people;
- on-device retention and deletion;
- consent for camera/microphone use;
- secure storage of any credentials in the legacy prototype.

## Ethical and usability notes

- Alert fatigue is a safety risk. More alerts are not automatically better.
- A false negative can be more dangerous than a false positive, but excessive
  false positives cause users to disable the system.
- Urgent phrasing ("Stop now") is intentionally direct. It should be reviewed
  with assistive-technology users and orientation-and-mobility specialists.
- The system should communicate uncertainty rather than pretending precision.
  The verbose profile surfaces approximate distance and contact time; concise
  mode deliberately omits that detail.

## Recommended next steps before any field use

1. Integrate real depth/perception and validate distance calibration.
2. Measure end-to-end latency and jitter on target hardware.
3. Run a supervised user study with assistive-technology participants.
4. Add watchdog and degraded-mode behavior.
5. Localize phrasing and test with screen readers / TTS engines.
6. Add conservative fail-safe defaults and a physical emergency stop path.
7. Perform a formal hazard analysis (for example, ISO 14971-style) if the
   product is intended for medical or safety-critical use.

Until then, treat this as a well-tested decision-core prototype and a transparent
record of engineering trade-offs.
