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

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "configs" / "default_config.json"


@router.post("/transform")
async def transform(
    csv_file: Optional[UploadFile] = File(default=None),
    resume_files: Optional[List[UploadFile]] = File(default=None),
    notes_files: Optional[List[UploadFile]] = File(default=None),
    config: Optional[str] = Form(default=None),
):
    if not csv_file and not resume_files and not notes_files:
        raise HTTPException(
            status_code=422,
            detail="At least one of csv_file, resume_files, or notes_files must be provided.",
        )

    if config:
        try:
            config_dict = json.loads(config)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid config JSON: {exc}")
    else:
        config_dict = json.loads(_DEFAULT_CONFIG.read_text(encoding="utf-8"))
        config_dict.pop("_comment", None)

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
        shutil.rmtree(tmp_dir, ignore_errors=True)
