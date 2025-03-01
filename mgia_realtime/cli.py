"""Command-line entry point for offline demos and recorded frame playback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .demo import build_demo_events
from .errors import FrameSourceError, MgiaError
from .events import PerceptionEvent, parse_event_stream
from .pipeline import PipelineConfig, RealtimePipeline
from .speech import VerbosityProfile


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser (kept public for documentation tests)."""
    parser = argparse.ArgumentParser(
        prog="mgia_realtime",
        description=(
            "Offline MGIA real-time safety pipeline: risk scoring, alarm "
            "hysteresis, and hands-free speech scheduling."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--demo",
        action="store_true",
        help="run the built-in deterministic ten-second walking scenario",
    )
    source.add_argument(
        "--frames",
        type=Path,
        metavar="FILE",
        help="read perception frames from a JSON file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit guidance and session metrics as JSON",
    )
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in VerbosityProfile],
        default=VerbosityProfile.STANDARD.value,
        help="speech verbosity profile (default: standard)",
    )
    return parser


def load_events(path: Path) -> list[PerceptionEvent]:
    """Read and validate a JSON frame document.

    Accepts either a top-level list of frames or ``{"frames": [...]}``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FrameSourceError(f"cannot read {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FrameSourceError(f"{path} is not valid JSON: {exc.msg}") from exc
    return parse_event_stream(payload)


def _format_percentile(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 1000.0:.1f} ms"


def _run(pipeline: RealtimePipeline, events: list[PerceptionEvent], args: argparse.Namespace) -> int:
    guidance = pipeline.run(events)
    snapshot = pipeline.snapshot()
    if args.json:
        document = {
            "profile": args.profile,
            "frame_count": snapshot.frames_processed,
            "guidance": [item.to_dict() for item in guidance],
            "metrics": snapshot.to_dict(),
        }
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0

    for item in guidance:
        print(f"[{item.timestamp_s:6.2f}s] {item.level.name:<8} {item.text}")
    print(
        f"\n{snapshot.frames_processed} frames | {len(guidance)} guidance messages | "
        f"p50 {_format_percentile(snapshot.latency_p50_s)} | "
        f"p95 {_format_percentile(snapshot.latency_p95_s)} | "
        f"dropped {snapshot.messages_dropped} | coalesced {snapshot.messages_coalesced}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.demo and args.frames is None:
        parser.error("choose one of --demo or --frames FILE")
    profile = VerbosityProfile(args.profile)
    pipeline = RealtimePipeline(PipelineConfig(profile=profile))
    try:
        events = build_demo_events() if args.demo else load_events(args.frames)
        return _run(pipeline, list(events), args)
    except MgiaError as exc:
        print(f"mgia_realtime: error: {exc}", file=sys.stderr)
        return 1
