"""
pipeline.py — Orchestrates the full transformation pipeline.

Stage order:
  1. Extract  — CSV extractor + resume extractor (each wrapped in try/except)
  2. Merge    — group by match key, resolve conflicts, score confidence
  3. Project  — reshape canonical record per runtime config
  4. Validate — check output shape and field constraints
  5. Emit     — return list of plain dicts ready for json.dumps()

Robustness contract:
  - A missing or corrupt CSV does not crash the pipeline; CSV source is skipped.
  - A missing or corrupt resume does not crash the pipeline; that file is skipped.
  - A projection/validation failure for one candidate logs a warning and skips
    that candidate; remaining candidates are still emitted.
  - Zero candidates in → empty list out (not an error).

Determinism:
  - Resume files are sorted alphabetically before processing so the output
    order is the same regardless of filesystem traversal order.
  - No timestamps or random values are introduced here.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from transformer.extractors.csv_extractor import extract_from_csv
from transformer.extractors.resume_extractor import extract_from_resume
from transformer.extractors.notes_extractor import extract_from_notes
from transformer.merge import merge_candidates
from transformer.project import project, load_config
from transformer.validate import validate, ValidationError

logger = logging.getLogger(__name__)

# File extensions we accept as resume inputs
_RESUME_EXTENSIONS = {".pdf", ".docx"}

# File extensions we accept as recruiter notes
_NOTES_EXTENSIONS = {".txt"}


# Internal helpers

def _collect_resume_files(resumes_dir: Path) -> List[Path]:
    """
    Return all PDF and DOCX files inside resumes_dir, sorted alphabetically
    so the pipeline is deterministic regardless of OS directory order.
    """
    if not resumes_dir.exists():
        logger.warning("Resumes directory does not exist: %s — skipping.", resumes_dir)
        return []
    if not resumes_dir.is_dir():
        logger.warning("%s is not a directory — skipping resumes.", resumes_dir)
        return []

    files = sorted(
        f for f in resumes_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _RESUME_EXTENSIONS
    )
    logger.info("Found %d resume file(s) in %s.", len(files), resumes_dir)
    return files


# Public API

def run(
    csv_path: Optional[Union[str, Path]] = None,
    resumes_dir: Optional[Union[str, Path]] = None,
    notes_dir: Optional[Union[str, Path]] = None,
    config: Union[str, Path, dict, None] = None,
) -> List[dict]:
    """
    Run the full pipeline and return a list of output dicts (one per candidate).

    Args:
        csv_path:    Path to the recruiter CSV file, or None to skip CSV source.
        resumes_dir: Path to the directory containing resume files (PDF/DOCX),
                     or None to skip resume source.
        config:      Runtime projection config — a file path, a raw JSON string,
                     a dict, or None (→ emit the full canonical schema).

    Returns:
        List of plain Python dicts, each representing one candidate's output.
        The list is empty when no valid candidates could be produced.
        Each dict is directly serializable with json.dumps().
    """

    #Stage 1a: Extracting data from CSV 
    csv_records = []
    if csv_path is not None:
        # extract_from_csv already handles missing/corrupt files internally
        # and returns [] on failure, so we don't need an extra try/except.
        csv_records = extract_from_csv(csv_path)
        logger.info("CSV stage: %d record(s) extracted.", len(csv_records))
    else:
        logger.info("No CSV path provided — skipping CSV source.")

    # Stage 1b: Extracting data from resumes 
    resume_records = []
    if resumes_dir is not None:
        resume_files = _collect_resume_files(Path(resumes_dir))
        for resume_file in resume_files:
            # extract_from_resume returns None on failure; we filter those out
            result = extract_from_resume(resume_file)
            if result is not None:
                resume_records.append(result)
        logger.info("Resume stage: %d resume(s) parsed successfully.", len(resume_records))
    else:
        logger.info("No resumes directory provided — skipping resume source.")

    # Stage 1c: Extracting from recruiter notes 
    notes_records = []
    if notes_dir is not None:
        notes_path = Path(notes_dir)
        if notes_path.exists() and notes_path.is_dir():
            note_files = sorted(
                f for f in notes_path.iterdir()
                if f.is_file() and f.suffix.lower() in _NOTES_EXTENSIONS
            )
            for nf in note_files:
                result = extract_from_notes(nf)
                if result is not None:
                    notes_records.append(result)
            logger.info("Notes stage: %d note(s) parsed successfully.", len(notes_records))
        else:
            logger.warning("Notes directory not found: %s — skipping.", notes_dir)
    else:
        logger.info("No notes directory provided — skipping notes source.")

    #  Additional check for nothing to process
    if not csv_records and not resume_records:
        logger.warning("No records extracted from any source — returning empty output.")
        return []

    #Stage 2: Merge 
    profiles = merge_candidates(csv_records, resume_records, notes_records or None)
    logger.info("Merge stage: %d unique candidate profile(s) produced.", len(profiles))

    # ── Stage 3: Load config (done once, shared across all profiles) ──────
    cfg = load_config(config)

    # ── Stages 4 + 5: Project → Validate → Collect ───────────────────────
    results: List[dict] = []
    skipped = 0

    for profile in profiles:
        try:
            # Project: reshape canonical record per config
            output = project(profile, cfg)

            # Validate: check shape and field constraints
            validate(output, cfg)

            results.append(output)

        except ValidationError as exc:
            logger.warning(
                "Validation failed for candidate %r (%s) — skipping. Reason: %s",
                profile.full_name, profile.candidate_id, exc,
            )
            skipped += 1

        except Exception as exc:
            # Catch-all for unexpected errors in project/validate so one
            # bad candidate never brings down the rest of the batch.
            logger.warning(
                "Unexpected error processing candidate %r (%s) — skipping. Reason: %s",
                profile.full_name, profile.candidate_id, exc,
            )
            skipped += 1

    logger.info(
        "Pipeline complete: %d candidate(s) emitted, %d skipped.",
        len(results), skipped,
    )
    return results


def run_to_json(
    csv_path: Optional[Union[str, Path]] = None,
    resumes_dir: Optional[Union[str, Path]] = None,
    config: Union[str, Path, dict, None] = None,
    indent: int = 2,
) -> str:
    """
    Convenience wrapper around run() that serializes the result to a JSON string.

    Args:
        csv_path, resumes_dir, config: same as run().
        indent: JSON indentation level (default 2).

    Returns:
        A JSON string containing a list of candidate output objects.
    """
    results = run(csv_path=csv_path, resumes_dir=resumes_dir, config=config)
    return json.dumps(results, indent=indent, ensure_ascii=False)
