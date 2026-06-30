"""
routes.py — POST /transform endpoint.

Accepts multipart form-data:
  csv_file     (optional) — the recruiter CSV file
  resume_files (optional) — one or more PDF/DOCX resume files
  config       (optional) — runtime projection config as a JSON string;
                             falls back to configs/default_config.json if absent

Calls transformer.pipeline.run() directly — no pipeline logic lives here.
This file is purely HTTP plumbing: receive files, write them to a temp dir,
call the pipeline, return JSON, clean up.
"""

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from transformer.pipeline import run

logger = logging.getLogger(__name__)

router = APIRouter()

# Path to the default config shipped with the project
_DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "configs" / "default_config.json"


@router.post("/transform")
async def transform(
    csv_file: Optional[UploadFile] = File(default=None),
    resume_files: Optional[List[UploadFile]] = File(default=None),
    notes_files: Optional[List[UploadFile]] = File(default=None),
    config: Optional[str] = Form(default=None),
):
    """
    Run the candidate transformer pipeline and return the resulting profiles.

    All file handling uses a temporary directory that is deleted after the
    request completes — nothing is persisted on the server.
    """
    if not csv_file and not resume_files and not notes_files:
        raise HTTPException(
            status_code=422,
            detail="At least one of csv_file, resume_files, or notes_files must be provided.",
        )

    # Resolve the config: use the posted JSON string, or fall back to the
    # default config file on disk.
    if config:
        try:
            config_dict = json.loads(config)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid config JSON: {exc}")
    else:
        config_dict = json.loads(_DEFAULT_CONFIG.read_text(encoding="utf-8"))
        # Strip the _comment key so load_config doesn't see it
        config_dict.pop("_comment", None)

    # Write uploaded files to a temp directory so the pipeline can read them
    # by file path (same interface as the CLI).
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        csv_path = None
        if csv_file and csv_file.filename:
            csv_path = tmp_dir / csv_file.filename
            csv_path.write_bytes(await csv_file.read())

        resumes_dir = None
        if resume_files:
            valid_resumes = [f for f in resume_files if f and f.filename]
            if valid_resumes:
                resumes_dir = tmp_dir / "resumes"
                resumes_dir.mkdir()
                for rf in valid_resumes:
                    (resumes_dir / rf.filename).write_bytes(await rf.read())

        notes_dir = None
        if notes_files:
            valid_notes = [f for f in notes_files if f and f.filename]
            if valid_notes:
                notes_dir = tmp_dir / "notes"
                notes_dir.mkdir()
                for nf in valid_notes:
                    (notes_dir / nf.filename).write_bytes(await nf.read())

        # Run the pipeline — identical call to what cli.py makes
        results = run(
            csv_path=csv_path,
            resumes_dir=resumes_dir,
            notes_dir=notes_dir,
            config=config_dict,
        )

        return JSONResponse(content={"candidates": results, "count": len(results)})

    except Exception as exc:
        logger.exception("Pipeline error during /transform request")
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        # Always clean up temp files, even if the pipeline raised
        shutil.rmtree(tmp_dir, ignore_errors=True)
