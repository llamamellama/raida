"""Command-line entry point: ``raida serve | doctor | process | ask | export``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser
from pathlib import Path

from raida import __version__
from raida.config import Config, ConfigError, load_config
from raida.logging_setup import configure_logging
from raida.model_env import apply_model_env

log = logging.getLogger(__name__)


def _load(args: argparse.Namespace) -> Config:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    configure_logging(config.log_level)
    config.ensure_dirs()
    apply_model_env(config)
    return config


def cmd_doctor(args: argparse.Namespace) -> int:
    from raida.doctor import format_report, run_health

    config = _load(args)
    report = asyncio.run(run_health(config))
    print(format_report(report))
    return 0 if report.ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from raida.api.app import create_app
    from raida.doctor import format_report, hard_failures, run_health

    config = _load(args)
    report = asyncio.run(run_health(config))
    print(format_report(report), file=sys.stderr)
    failures = hard_failures(report)
    if failures:
        print("error: cannot start; fix these first:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    if not report.llm.ok:
        log.warning("llm_not_ready", detail=report.llm.detail)  # type: ignore[call-arg]
    host, port = config.server.host, args.port or config.server.port
    url = f"http://{host}:{port}/"
    app = create_app(config, initial_health=report)
    if config.server.open_browser and not args.no_open:
        webbrowser.open(url)
    print(f"raida {__version__} listening on {url}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    from raida.pipeline.headless import process_files

    config = _load(args)
    paths = [Path(p).expanduser() for p in args.files]
    return asyncio.run(process_files(config, paths, language=args.language))


def cmd_ask(args: argparse.Namespace) -> int:
    from raida.pipeline.headless import ask

    config = _load(args)
    return asyncio.run(ask(config, args.session_id, args.instruction))


def cmd_export(args: argparse.Namespace) -> int:
    from raida.pipeline.headless import export_message

    config = _load(args)
    return asyncio.run(export_message(config, args.message_id, args.format, args.output))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="raida", description="Offline source-processing harness")
    parser.add_argument(
        "--config", help="path to raida.toml (default: $RAIDA_CONFIG or ./raida.toml)"
    )
    parser.add_argument("--version", action="version", version=f"raida {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="start the local web app")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--no-open", action="store_true", help="do not open the browser")
    p_serve.set_defaults(func=cmd_serve)

    p_doctor = sub.add_parser("doctor", help="check models, tools and storage")
    p_doctor.set_defaults(func=cmd_doctor)

    p_process = sub.add_parser("process", help="ingest files headlessly and print the result")
    p_process.add_argument("files", nargs="+")
    p_process.add_argument("--language", default="auto")
    p_process.set_defaults(func=cmd_process)

    p_ask = sub.add_parser("ask", help="run an instruction against a session's sources")
    p_ask.add_argument("session_id")
    p_ask.add_argument("instruction")
    p_ask.set_defaults(func=cmd_ask)

    p_export = sub.add_parser("export", help="export an assistant message")
    p_export.add_argument("message_id")
    p_export.add_argument("--format", choices=["txt", "md", "pdf", "docx"], default="md")
    p_export.add_argument("--output", default=None)
    p_export.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
