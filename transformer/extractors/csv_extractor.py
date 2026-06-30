"""
csv_extractor.py — Reads the recruiter CSV export and returns raw candidate dicts.

Each row in the CSV maps to exactly one RawCandidate. No normalization happens
here — that is normalize.py's job. We just read, strip whitespace, and hand off.

Expected CSV columns (order doesn't matter, matched by header name):
    name, email, phone, current_company, title
"""

import csv
import logging
from pathlib import Path
from typing import List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RawCandidate:
    """
    Plain container for one row worth of data straight out of the CSV.
    All values are strings (or empty string when the cell was blank).
    Downstream stages will validate, normalize, and type-convert.
    """
    name: str = ""
    email: str = ""
    phone: str = ""
    current_company: str = ""
    title: str = ""
    # Tracks which file this row came from; set by the extractor automatically.
    source_label: str = "csv"


# The exact column names we expect in the CSV header (case-insensitive match).
EXPECTED_COLUMNS = {"name", "email", "phone", "current_company", "title"}


def extract_from_csv(csv_path: str | Path) -> List[RawCandidate]:
    """
    Parse the recruiter CSV file and return a list of RawCandidate objects.

    Robustness guarantees:
    - If the file does not exist or cannot be opened, logs a warning and returns [].
    - If a row is missing some columns, those fields default to "".
    - If the file has zero data rows, returns [] without error.
    - Completely blank rows (all fields empty) are silently skipped.

    Args:
        csv_path: Path to the CSV file (str or pathlib.Path).

    Returns:
        List of RawCandidate, one per non-blank CSV row.
    """
    csv_path = Path(csv_path)

    # Robustness: missing or unreadable file must not crash the pipeline.
    if not csv_path.exists():
        logger.warning("CSV file not found: %s — skipping CSV source.", csv_path)
        return []

    candidates: List[RawCandidate] = []

    try:
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)

            # Warn if any expected columns are absent so issues are surfaced early.
            if reader.fieldnames:
                # Normalise header names to lowercase for comparison.
                actual = {h.strip().lower() for h in reader.fieldnames}
                missing = EXPECTED_COLUMNS - actual
                if missing:
                    logger.warning(
                        "CSV is missing expected columns: %s. Those fields will be empty.",
                        missing,
                    )

            for row_num, row in enumerate(reader, start=2):  # start=2: row 1 is the header
                # Strip whitespace from every cell value.
                cleaned = {k.strip().lower(): v.strip() for k, v in row.items() if k}

                # Skip rows where every field is blank (e.g. trailing newlines).
                if not any(cleaned.values()):
                    logger.debug("Row %d is entirely blank — skipping.", row_num)
                    continue

                candidate = RawCandidate(
                    name=cleaned.get("name", ""),
                    email=cleaned.get("email", ""),
                    phone=cleaned.get("phone", ""),
                    current_company=cleaned.get("current_company", ""),
                    title=cleaned.get("title", ""),
                    source_label=f"csv:{csv_path.name}",
                )
                candidates.append(candidate)

    except Exception as exc:
        # Catch-all so a corrupt file never brings down the whole pipeline.
        logger.warning("Failed to read CSV file %s: %s — skipping CSV source.", csv_path, exc)
        return []

    logger.info("CSV extractor: read %d row(s) from %s.", len(candidates), csv_path.name)
    return candidates
