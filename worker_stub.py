#!/usr/bin/env python3
"""AI Roto Bridge worker protocol example.

Run as:
    python worker.py /path/to/request.json

A real worker should create mask_*.png in the requested output directory and
exit with status 0.
"""

from pathlib import Path
import json
import sys


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: worker.py request.json")

    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    print("AI Roto Bridge request received")
    print(json.dumps(request, indent=2))

    output_dir = Path(request["output"]["directory"])
    output_dir.mkdir(parents=True, exist_ok=True)

    print("This is only the protocol stub; no masks were generated.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
