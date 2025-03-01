# About MGIA

## One-line description

MGIA is a deterministic, dependency-free real-time safety pipeline that turns
perception events into stable, accessible, hands-free guidance.

## Longer description

The Mobility-Guided Intelligent Assistant (MGIA) explores how an assistive
system should decide **what to say, when to say it, and when to stay quiet**.
The new `mgia_realtime` package implements the real-time decision layer:
validated perception events, time-to-contact risk scoring, a hysteretic alarm
state machine, hazard- and context-aware safety tips, and a speech scheduler
with priority, deduplication, cooldowns, and preemption. It is standard-library
only and runs offline. The original multimodal prototype is preserved for
context.

## Topics and keywords

`assistive-technology` · `accessibility` · `real-time-systems` ·
`safety-critical` · `time-to-collision` · `risk-scoring` · `hysteresis` ·
`state-machine` · `speech-scheduling` · `priority-queue` · `preemption` ·
`deduplication` · `cooldown` · `hands-free` · `human-in-the-loop` ·
`edge-ai` · `deterministic-simulation` · `python` · `standard-library` ·
`branch-coverage` · `continuous-integration` · `mobility` ·
`pedestrian-safety` · `multimodal-ai` · `computer-vision`

## Technologies

- Python 3.10+ (runtime uses only the standard library)
- `dataclasses`, `enum`, `heapq`, `math`, `json`, `argparse`
- pytest + pytest-cov / coverage.py for branch coverage
- GitHub Actions for the Python 3.10–3.12 matrix
- Mermaid diagrams in documentation

## What is intentionally not included

- Camera, microphone, depth-sensor, or TTS integration in the tested core
- Network calls, model downloads, or LLM calls
- Claims of clinical validity, field performance, or hardware latency
- Fabricated metrics or benchmark numbers

## Author

**Kyaw Kyaw Oo**

- Email: [kyaw.tech@gmail.com](mailto:kyaw.tech@gmail.com)
- GitHub: [github.com/MrKyaw](https://github.com/MrKyaw)
- Project: [Mobility-Guided-Intelligent-Assistant-MGIA-for-Real-Time-Handsfree-Safety-Tips](https://github.com/MrKyaw/Mobility-Guided-Intelligent-Assistant-MGIA-for-Real-Time-Handsfree-Safety-Tips)

## License

MIT. See [LICENSE](LICENSE).
