import json
import logging
from pathlib import Path
from typing import List, Optional, Union

from transformer.extractors.csv_extractor import extract_from_csv
from transformer.extractors.resume_extractor import extract_from_resume
from transformer.extractors.notes_extractor import extract_from_notes
from transformer.merge import merge_candidates
from transformer.project import project, load_config
from transformer.validate import validate, ValidationError

logger = logging.getLogger(__name__)

_RESUME_EXTENSIONS = {".pdf", ".docx"}
_NOTES_EXTENSIONS  = {".txt"}


def _collect_resume_files(resumes_dir: Path) -> List[Path]:
    if not resumes_dir.exists():
        logger.warning("Resumes directory does not exist: %s - skipping.", resumes_dir)
        return []
    if not resumes_dir.is_dir():
        logger.warning("%s is not a directory - skipping resumes.", resumes_dir)
        return []
    files = sorted(
        f for f in resumes_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _RESUME_EXTENSIONS
    )
    logger.info("Found %d resume file(s) in %s.", len(files), resumes_dir)
    return files


def run(
    csv_path: Optional[Union[str, Path]] = None,
    resumes_dir: Optional[Union[str, Path]] = None,
    notes_dir: Optional[Union[str, Path]] = None,
    config: Union[str, Path, dict, None] = None,
) -> List[dict]:
    """Run the full pipeline and return a list of output dicts, one per candidate."""

    csv_records = []
    if csv_path is not None:
        csv_records = extract_from_csv(csv_path)
        logger.info("CSV stage: %d record(s) extracted.", len(csv_records))
    else:
        logger.info("No CSV path provided - skipping CSV source.")

    resume_records = []
    if resumes_dir is not None:
        resume_files = _collect_resume_files(Path(resumes_dir))
        for resume_file in resume_files:
            result = extract_from_resume(resume_file)
            if result is not None:
                resume_records.append(result)
        logger.info("Resume stage: %d resume(s) parsed successfully.", len(resume_records))
    else:
        logger.info("No resumes directory provided - skipping resume source.")

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
            logger.warning("Notes directory not found: %s - skipping.", notes_dir)
    else:
        logger.info("No notes directory provided - skipping notes source.")

    if not csv_records and not resume_records:
        logger.warning("No records extracted from any source - returning empty output.")
        return []

    profiles = merge_candidates(csv_records, resume_records, notes_records or None)
    logger.info("Merge stage: %d unique candidate profile(s) produced.", len(profiles))

    cfg = load_config(config)

    results: List[dict] = []
    skipped = 0

    for profile in profiles:
        try:
            output = project(profile, cfg)
            validate(output, cfg)
            results.append(output)

        except ValidationError as exc:
            logger.warning(
                "Validation failed for candidate %r (%s) - skipping. Reason: %s",
                profile.full_name, profile.candidate_id, exc,
            )
            skipped += 1

        except Exception as exc:
            logger.warning(
                "Unexpected error processing candidate %r (%s) - skipping. Reason: %s",
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
    results = run(csv_path=csv_path, resumes_dir=resumes_dir, config=config)
    return json.dumps(results, indent=indent, ensure_ascii=False)
