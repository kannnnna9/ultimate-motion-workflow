#!/usr/bin/env python3
"""Build a labeled contact sheet from an authorized local video (FFmpeg + FFprobe)."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ump_core.reference import make_contact_sheet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--at-ms', required=True, help='comma-separated timestamps, e.g. 0,1000,2000')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        times = [int(part) for part in args.at_ms.split(',')]
    except ValueError:
        parser.error('--at-ms requires comma-separated integer timestamps')
    try:
        make_contact_sheet(args.video, times, args.out)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(args.out / 'sheet.png')
    print(args.out / 'frames.json')


if __name__ == '__main__':
    main()
