"""Command line entry point: a few flags around `jev-preview`, then the TUI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from jev_preview import __version__
from jev_preview.config import (
    API_KEY_ENV,
    CONFIG_DIR_ENV,
    REQUESTS_DIR_ENV,
    Config,
    config_path,
    mask_key,
    requests_dir,
)

EPILOG = f"""\
environment:
  {API_KEY_ENV}     API key; overrides the one in the config file
  {CONFIG_DIR_ENV}        directory holding config.json
  {REQUESTS_DIR_ENV}      directory holding saved requests

Press ? inside the app for the key map.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jev-preview",
        description="A terminal sandbox for the TypeSafe (Jev) System One API.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"jev-preview {__version__}")
    parser.add_argument(
        "-d",
        "--requests-dir",
        type=Path,
        metavar="DIR",
        help="where saved requests live (default: %(default)s)",
        default=None,
    )
    parser.add_argument(
        "--set-key",
        metavar="KEY",
        help="store an API key and exit; pass '-' to read it from stdin",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="print where things are stored, then exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    directory = args.requests_dir or requests_dir()

    if args.set_key is not None:
        return _set_key(args.set_key)
    if args.show_config:
        return _show_config(directory)

    from jev_preview.app import JevApp  # imported late: starting the TUI is the slow path

    JevApp(config=Config.load(), directory=directory).run()
    return 0


def _set_key(value: str) -> int:
    key = sys.stdin.read().strip() if value == "-" else value.strip()
    if not key:
        print("no key given", file=sys.stderr)
        return 2
    config = Config.load().with_api_key(key)
    path = config.save()
    print(f"saved {mask_key(config.api_key)} to {path}")
    return 0


def _show_config(directory: Path) -> int:
    config = Config.load()
    source = "environment" if config.key_from_env else "config file" if config.has_api_key else "-"
    print(f"config file   {config_path()}")
    print(f"requests      {directory}")
    print(f"base url      {config.base_url}")
    print(f"models        {', '.join(config.models)}")
    print(f"api key       {mask_key(config.api_key) or 'not set'} ({source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
