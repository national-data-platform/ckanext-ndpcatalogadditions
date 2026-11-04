"""Command line: python -m metadata_convert INPUT [INPUT ...] [--format F] [-o OUT] [--report]."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import metadata_convert
from .detect import FORMATS, FormatError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="metadata_convert",
        description="Convert DataCite, Dublin Core, schema.org, DCAT, ISO 19115 or STAC metadata to CKAN JSON.",
    )
    ap.add_argument("inputs", nargs="+", help="metadata file(s); '-' reads stdin")
    ap.add_argument("-f", "--format", default="auto", choices=("auto",) + FORMATS)
    ap.add_argument("-o", "--output", help="output file (one input) or directory (several inputs); default stdout")
    ap.add_argument("--report", action="store_true",
                    help="emit the full result (package, external_id, warnings, unmapped, errors) instead of just the package")
    ap.add_argument("--with-original", action="store_true", help="with --report, include the original source record")
    args = ap.parse_args(argv)

    multi = len(args.inputs) > 1
    out_dir = Path(args.output) if args.output and multi else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    status = 0
    written: set[str] = set()
    for inp in args.inputs:
        try:
            record = sys.stdin.buffer.read() if inp == "-" else Path(inp).read_bytes()
            result = metadata_convert(record, args.format)
        except (OSError, FormatError, ValueError) as e:
            print(f"{inp}: error: {e}", file=sys.stderr)
            status = 1
            continue
        payload = result.to_dict(include_original=args.with_original) if args.report else result.package
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if out_dir:
            fname = Path(inp).name + ".ckan.json"
            if fname in written:  # same file name from another directory
                fname = f"{Path(inp).parent.name}_{fname}"
            written.add(fname)
            (out_dir / fname).write_text(text + "\n", encoding="utf-8")
        elif args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        for w in result.warnings:
            print(f"{inp}: warning: {w}", file=sys.stderr)
        for e in result.errors:
            print(f"{inp}: invalid: {e}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
