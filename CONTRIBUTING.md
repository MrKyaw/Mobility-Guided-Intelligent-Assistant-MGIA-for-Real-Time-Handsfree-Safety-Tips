# Contributing

Thanks for considering a contribution to MGIA. This repository has two parts:

- `mgia_realtime/` — the new dependency-free real-time safety core. This is the
  actively tested surface.
- `src/`, `config/`, `data/`, `test/` — the preserved notebook-era prototype.
  Please do not delete or rewrite those files as part of unrelated changes.

## Development setup

```bash
python3 --version          # 3.10 or newer
python3 -m pip install -e ".[dev]"
```

No network, camera, microphone, model, or LLM access is required for the test
suite.

## Running checks

```bash
make test       # python3 -m pytest -q
make coverage   # 100% line + branch gate
make demo       # CLI smoke test
make check      # coverage + demo
```

Or directly:

```bash
python3 -m pytest --cov=mgia_realtime --cov-branch --cov-report=term-missing
```

The coverage gate is enforced by `pyproject.toml` (`fail_under = 100`). A pull
request that lowers coverage will fail CI.

## Test expectations

- Add tests for every behavior change, including both sides of new branches.
- Keep tests deterministic. Do not use `sleep`, real time, random data, network,
  camera, microphone, or LLM calls.
- Pass explicit session timestamps; the pipeline must remain replayable.
- Prefer exact boundary assertions (thresholds, cooldowns, dwell times) over
  broad snapshots.
- New modules under `mgia_realtime/` are automatically included in the coverage
  source and must reach 100%.

## Code style

- Python 3.10+ syntax with `from __future__ import annotations`.
- Type hints on public functions and dataclasses.
- Docstrings explain *why* a rule exists, not just what it does.
- Runtime code uses the standard library only. Do not add a runtime dependency
  without a design discussion.
- Keep the safety path free of I/O, blocking waits, and hidden global state.
- Raise package exceptions from `mgia_realtime/errors.py` for expected failures.

## Changing thresholds or templates

Risk thresholds, hysteresis values, TTLs, and cooldowns are safety-relevant.
When changing them:

1. Explain the user-facing consequence in the pull request.
2. Update `ARCHITECTURE.md` and `docs/DESIGN_DECISIONS.md` if the behavior or
   rationale changes.
3. Update boundary tests, not just happy-path tests.
4. Do not claim improved safety without measurement. This project does not
   fabricate metrics.

## Legacy prototype

The old prototype is preserved for context. Its `test/` files are empty
placeholders and are excluded from the default pytest run. If you revive them:

- put runnable tests under `test/` or migrate them to `tests/`;
- document any heavy dependencies;
- update `pyproject.toml` and `TESTING.md` honestly;
- do not make the dependency-free suite require those packages.

## Pull request checklist

- [ ] `make test` passes.
- [ ] `make coverage` reports 100% line and branch.
- [ ] `python3 -m mgia_realtime --demo` runs from the repository root.
- [ ] New public behavior is documented.
- [ ] No secrets, API keys, model weights, or large binaries are committed.
- [ ] Legacy files are preserved unless the change explicitly targets them.

## Reporting issues

Include the Python version, the command you ran, the full traceback, and a
minimal frame payload if the issue is input-related. For safety concerns, state
clearly whether the issue is a real-world hazard or a prototype limitation.

## License

By contributing, you agree that your contributions are licensed under the MIT
License in [LICENSE](LICENSE).
