"""Local entry point for the OTA Fleet Diagnostics Lab."""

from __future__ import annotations

import argparse
import os

import uvicorn

from ota_simulator.api import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deterministic OTA fleet diagnostics dashboard."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Interface to bind (default: 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    parser.add_argument(
        "--database",
        default=os.getenv("OTA_DATABASE_PATH", "data/ota-lab.db"),
        help="SQLite database path (default: data/ota-lab.db)",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return args


def main() -> None:
    args = parse_args()
    os.environ["OTA_DATABASE_PATH"] = args.database
    print(f"OTA Fleet Diagnostics Lab: http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
