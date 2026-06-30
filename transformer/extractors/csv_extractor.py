import csv
import logging
from pathlib import Path
from typing import List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RawCandidate:
    name: str = ""
    email: str = ""
    phone: str = ""
    current_company: str = ""
    title: str = ""
    source_label: str = "csv"


EXPECTED_COLUMNS = {"name", "email", "phone", "current_company", "title"}


def extract_from_csv(csv_path: str | Path) -> List[RawCandidate]:
    """Parse the recruiter CSV and return one RawCandidate per non-blank row."""
    csv_path = Path(csv_path)

    if not csv_path.exists():
        logger.warning("CSV file not found: %s - skipping CSV source.", csv_path)
        return []

    candidates: List[RawCandidate] = []

    try:
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)

            if reader.fieldnames:
                actual  = {h.strip().lower() for h in reader.fieldnames}
                missing = EXPECTED_COLUMNS - actual
                if missing:
                    logger.warning(
                        "CSV is missing expected columns: %s. Those fields will be empty.",
                        missing,
                    )

            for row_num, row in enumerate(reader, start=2):  # start=2: row 1 is the header
                cleaned = {k.strip().lower(): v.strip() for k, v in row.items() if k}

                if not any(cleaned.values()):
                    logger.debug("Row %d is entirely blank - skipping.", row_num)
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
        logger.warning("Failed to read CSV file %s: %s - skipping CSV source.", csv_path, exc)
        return []

    logger.info("CSV extractor: read %d row(s) from %s.", len(candidates), csv_path.name)
    return candidates
