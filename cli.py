import argparse
import json
import logging
import sys
from pathlib import Path

from transformer.pipeline import run


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description=(
            "Multi-source candidate data transformer.\n"
            "Ingests a recruiter CSV and/or a directory of resumes (PDF/DOCX),\n"
            "merges them into canonical candidate profiles, and emits JSON."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="Path to the recruiter CSV file (columns: name, email, phone, current_company, title).",
    )
    parser.add_argument(
        "--resumes",
        metavar="DIR",
        help="Path to the directory containing resume files (.pdf and/or .docx).",
    )
    parser.add_argument(
        "--notes",
        metavar="DIR",
        help="Path to directory containing recruiter notes files (.txt).",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "Path to a JSON projection config file. "
            "Omit to emit the full canonical schema. "
            "See configs/default_config.json for the format."
        ),
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="File path to write the JSON output to. If omitted, output is printed to stdout.",
    )
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: WARNING). Use INFO to see pipeline progress.",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args   = parser.parse_args()

    _configure_logging(args.log_level)
    logger = logging.getLogger(__name__)

    if not args.csv and not args.resumes and not args.notes:
        parser.error("At least one of --csv or --resumes must be provided.")

    if args.csv and not Path(args.csv).exists():
        logger.warning("CSV file not found: %s - it will be skipped by the pipeline.", args.csv)

    if args.resumes and not Path(args.resumes).is_dir():
        logger.warning(
            "Resumes path is not a directory: %s - it will be skipped by the pipeline.",
            args.resumes,
        )

    try:
        results = run(
            csv_path=args.csv,
            resumes_dir=args.resumes,
            notes_dir=args.notes,
            config=args.config,
        )
    except Exception as exc:
        logger.error("Pipeline failed with an unexpected error: %s", exc, exc_info=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    output_json = json.dumps(results, indent=2, ensure_ascii=False)

    if args.out:
        out_path = Path(args.out)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output_json, encoding="utf-8")
            print(f"Output written to {out_path}  ({len(results)} candidate(s))")
        except OSError as exc:
            print(f"ERROR: Could not write to {out_path}: {exc}", file=sys.stderr)
            return 1
    else:
        print(output_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
