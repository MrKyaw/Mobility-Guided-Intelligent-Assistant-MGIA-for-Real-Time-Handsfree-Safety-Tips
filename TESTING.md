# Testing

## Commands

From the repository root:

```bash
# Fast default suite (new package only)
python3 -m pytest -q

# Required coverage gate: line + branch, package only
python3 -m pytest --cov=mgia_realtime --cov-branch --cov-report=term-missing
```

The second command fails if coverage drops below 100%, because
`pyproject.toml` sets:

```toml
[tool.coverage.run]
source = ["mgia_realtime"]
branch = true

[tool.coverage.report]
fail_under = 100
```

## Measured result

Verified locally on Python 3.12.8 with pytest 9.1.1, coverage 7.16.2:

```text
$ python3 -m pytest -q
110 passed

Name                        Stmts   Miss Branch BrPart   Cover
mgia_realtime/__init__.py      11      0      0      0 100.00%
mgia_realtime/__main__.py       2      0      0      0 100.00%
mgia_realtime/alarm.py         96      0     38      0 100.00%
mgia_realtime/cli.py           57      0      8      0 100.00%
mgia_realtime/demo.py           6      0      0      0 100.00%
mgia_realtime/errors.py         7      0      0      0 100.00%
mgia_realtime/events.py       168      0     64      0 100.00%
mgia_realtime/metrics.py       89      0     12      0 100.00%
mgia_realtime/pipeline.py      71      0     12      0 100.00%
mgia_realtime/risk.py         101      0     36      0 100.00%
mgia_realtime/speech.py       182      0     54      0 100.00%
mgia_realtime/tips.py          97      0     36      0 100.00%
TOTAL                         887      0    260      0 100.00%
```

CI runs the same suite and gate on Python 3.10, 3.11, and 3.12. Local timing
varies by machine; the pass count and coverage percentages are the stable
results.

## Test map

`tests/test_mgia_realtime.py` is organised by subsystem:

| Class | Focus |
| --- | --- |
| `TestEvents` | Dataclass validation, aliases, malformed JSON, duplicate IDs, enum fallbacks |
| `TestRisk` | TTC boundaries, static/receding/missing distance, class/position factors, hazard boost, deterministic sorting, config validation |
| `TestAlarm` | Exact enter/exit thresholds, hysteresis band, dwell blocking, one-step downgrades, invalid scores/timestamps |
| `TestSpeech` | Profiles, expiry, coalescing, cooldowns, active duplicates, queue overflow, speaking-rate cap, preemption, stale heap entries |
| `TestMetrics` | Percentile interpolation (including empty/single), time-in-state, counters, latency clamping, JSON export |
| `TestTips` | All levels and variants, direction/context phrasing, profiles, anti-repetition reset, hazard override, TTLs |
| `TestPipeline` | Demo end-to-end, out-of-order rejection, same-timestamp preemption ordering, stream/snapshot, metric injection |
| `TestCLI` | `--help`, source validation, human/JSON output, profiles, good and malformed frame files |
| `TestDemoAndPackage` | Determinism and public exports |

## Why branch coverage, not just line coverage

Most safety defects live at boundaries rather than on the happy path:

- score exactly `0.55` versus `0.549`;
- score inside the hysteresis band;
- dwell exactly at the minimum;
- a cooldown ending at the current timestamp;
- a queue at capacity with equal priority;
- a critical message arriving while lower-priority speech is active.

Branch mode forces both sides of each decision. There are no `# pragma: no
cover` escape hatches in the runtime package except the conventional
`__main__` guard.

## Legacy prototype tests

The original `test/` directory is preserved unchanged. Its files
(`test_apps.py`, `test_image2audio.py`, `test_image2text.py`,
`test_image2video.py`, `test_metrics.py`, `test_performance.py`,
`test_prompts.py`) are empty notebook-era placeholders. Running `pytest test`
currently reports:

```text
no tests ran
```

Because they are placeholders that depend conceptually on the notebook-era
environment, the default pytest configuration sets `testpaths = ["tests"]`.
This is an honest exclusion, not a claim that the legacy suite passes. The
legacy files were not deleted and remain available for future work.

## Adding a test

1. Put new runtime tests in `tests/`.
2. Prefer exact boundary assertions over broad snapshots.
3. Keep tests deterministic: no network, camera, microphone, sleep, or LLM.
4. Use the session clock explicitly (`timestamp_s`) rather than wall time.
5. Run the coverage command; the gate must remain at 100%.

## CI

`.github/workflows/ci.yml` installs `.[dev]` and runs the fast suite plus the
coverage gate on each Python version. It also runs
`python3 -m mgia_realtime --demo` as a CLI smoke test. The workflow has not been
executed in this environment; local results are reported above.
