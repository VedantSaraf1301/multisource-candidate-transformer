# Multi-Source Candidate Data Transformer

A Python pipeline that ingests recruiter data from three independent sources — a structured CSV, unstructured resume files (PDF/DOCX), and free-text recruiter notes (.txt) — merges them into canonical candidate profiles, and emits validated JSON.

Built as a take-home assignment for the Eightfold AI Engineering Intern role.

---

## Project Structure

```
eightfoldAI/
├── transformer/                # Core pipeline library
│   ├── extractors/
│   │   ├── csv_extractor.py    # Parse recruiter CSV → RawCandidate
│   │   ├── resume_extractor.py # Parse PDF/DOCX resumes → RawResumeData
│   │   └── notes_extractor.py  # Parse recruiter .txt notes → RawNotesData
│   ├── models.py               # Pydantic v2 canonical schema (CandidateProfile)
│   ├── normalize.py            # Phone, date, location, skill normalization
│   ├── merge.py                # Match-key deduplication + conflict resolution
│   ├── project.py              # Runtime output reshaping per config
│   ├── validate.py             # Post-projection field validation
│   └── pipeline.py             # Orchestrates all stages
├── backend/
│   └── api/
│       ├── main.py             # FastAPI app with CORS
│       └── routes.py           # POST /transform endpoint
├── frontend/
│   └── app/
│       ├── page.js             # Upload form + result viewer (Next.js)
│       └── layout.js           # Minimal layout
├── sample_inputs/
│   ├── candidates.csv          # 5 CSV rows (includes 1 duplicate, 1 no-email, 1 bad phone)
│   ├── resumes/                # 3 resume files (PDF + DOCX)
│   └── notes/                  # 3 recruiter notes (.txt)
├── configs/
│   ├── default_config.json     # Full schema output with confidence + provenance
│   └── example_custom_config.json  # Remapped/filtered output example
├── tests/
│   └── test_pipeline.py        # 38 pytest tests
├── cli.py                      # Command-line interface
├── requirements.txt
└── output.json                 # Sample output from a full 3-source run
```

---

## Setup

**Requirements:** Python 3.10+ and Node.js 18+ (for the web UI only).

### 1. Create and activate a virtual environment

```bash
# From the project root
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# macOS / Linux
source venv/bin/activate
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

---

## Running the Pipeline

### CLI

```bash
# Full run: CSV + resumes + recruiter notes, print to stdout
python cli.py --csv sample_inputs/candidates.csv \
              --resumes sample_inputs/resumes \
              --notes sample_inputs/notes

# Write output to a file instead of stdout
python cli.py --csv sample_inputs/candidates.csv \
              --resumes sample_inputs/resumes \
              --notes sample_inputs/notes \
              --out output.json

# Custom projection config (remap fields, filter, set on_missing)
python cli.py --csv sample_inputs/candidates.csv \
              --resumes sample_inputs/resumes \
              --config configs/example_custom_config.json \
              --out output.json

# CSV only (no resumes)
python cli.py --csv sample_inputs/candidates.csv

# Resumes only (no CSV)
python cli.py --resumes sample_inputs/resumes

# Verbose logging (shows per-stage progress)
python cli.py --csv sample_inputs/candidates.csv --log-level INFO
```

### Python API

```python
from transformer.pipeline import run

results = run(
    csv_path="sample_inputs/candidates.csv",
    resumes_dir="sample_inputs/resumes",
    notes_dir="sample_inputs/notes",
)
# results is a list of plain dicts, ready for json.dumps()
```

---

## Running the Web UI

The web UI has two parts: a FastAPI backend and a Next.js frontend. Start them in separate terminals.

### Terminal 1 — Backend (FastAPI)

```bash
# From the project root, with venv active
uvicorn backend.api.main:app --reload
# Listens on http://localhost:8000
```

### Terminal 2 — Frontend (Next.js)

```bash
cd frontend
npm install        # first time only
npm run dev
# Listens on http://localhost:3000
```

Open [http://localhost:3000](http://localhost:3000) in a browser. Upload a CSV, resume files (PDF/DOCX), and/or recruiter notes (.txt), edit the config JSON if desired, and click **Transform**.

---

## Running Tests

```bash
pytest tests/test_pipeline.py -v
# 38 tests — should all pass
```

---

## How the Pipeline Works

### Stage overview

```
CSV ──────────────┐
                  ├─► Extract ─► Normalize ─► Merge ─► Project ─► Validate ─► JSON
Resumes (PDF/DOCX)┤
                  │
Notes (.txt) ─────┘
```

### 1. Extract

Each source has a dedicated extractor that returns a typed dataclass and never raises — a missing or corrupt file logs a warning and returns `None` / `[]`.

| Source | Extractor | Output type |
|--------|-----------|-------------|
| CSV | `csv_extractor.py` | `RawCandidate` |
| PDF / DOCX | `resume_extractor.py` | `RawResumeData` |
| .txt notes | `notes_extractor.py` | `RawNotesData` |

### 2. Normalize

Before merging, every field is normalized:

- **Phone** → E.164 format (`+91...`). Default region: India. Strings containing letters are rejected before parsing (avoids parsing `1800-CALL-NOW` as a US number).
- **Date** → `YYYY-MM`. Handles `"Jan 2021"`, `"2021"` (→ `"2021-01"`), and `"Present"` (→ `None`).
- **Skills** → canonical lowercase names via alias table (`ReactJS → react`, `PostgreSQL → postgres`, `JS → javascript`, etc.).
- **Location** → `{city, region, country}` struct; country names mapped to ISO-3166 alpha-2.

### 3. Merge

**Match key** (priority order):
1. Normalized email
2. E.164 phone
3. Synthetic name key (last resort — first initial + last name)

Candidates sharing the same match key are grouped and merged. Duplicate CSV rows for the same email collapse into one profile.

**Trust ranking** (higher = wins on conflict):

| Source | Wins for |
|--------|----------|
| CSV | `current_company`, `title`, `name`, `email`, `phone` |
| Notes | `current_company`, `title`, `skills`, `years_experience` (trust between CSV and resume) |
| Resume | `headline`, `skills`, `education`, `location`, `links` |

**Conflict resolution:**
- Single source or sources agree → confidence `1.0`
- Conflict resolved by trust → confidence `0.6`
- Value derived from date arithmetic (years_experience) → confidence `0.3`

**Candidate ID:** SHA-1 of the match key, truncated to 16 hex characters. Deterministic — same input always produces the same ID.

### 4. Project

A runtime config (JSON) controls the output shape:

```json
{
  "fields": [
    { "path": "full_name",     "type": "string",   "required": true },
    { "path": "primary_email", "from": "emails[0]","type": "string" },
    { "path": "skill_names",   "from": "skills[].name", "type": "string[]" }
  ],
  "on_missing": "null",
  "include_confidence": true,
  "include_provenance": false
}
```

- `"path"` is the output key; `"from"` is the source path in the canonical schema.
- Dot notation (`location.city`), index access (`emails[0]`), and array flattening (`skills[].name`) are supported.
- `on_missing`: `"null"` (emit key with `null`), `"omit"` (drop key), `"error"` (skip candidate).
- Omit the `"fields"` key entirely to emit the full canonical schema unchanged.

### 5. Validate

Post-projection checks on the output dict:
- Phones match `^\+\d{7,15}$` (E.164)
- Experience dates match `YYYY-MM`
- `overall_confidence` in `[0, 1]`

A validation failure skips that one candidate and logs a warning; the rest of the batch is unaffected.

---

## Canonical Schema

```json
{
  "candidate_id": "3f8a1b2c4d5e6f7a",
  "full_name": "Priya Sharma",
  "emails": ["priya.sharma@email.com"],
  "phones": ["+919876543210"],
  "location": { "city": "Bengaluru", "region": "Karnataka", "country": "IN" },
  "headline": "Software Engineer | Full-Stack Developer",
  "links": { "linkedin": "...", "github": "...", "portfolio": "..." },
  "skills": [{ "name": "python", "confidence": 1.0 }],
  "experience": [{
    "company": "TechCorp", "title": "Software Engineer",
    "start": "2021-01", "end": null, "summary": null
  }],
  "education": [{
    "institution": "Indian Institute of Technology",
    "degree": "B.Tech", "field": "Computer Science",
    "start": null, "end": "2019-05"
  }],
  "years_experience": 1.5,
  "overall_confidence": 0.89,
  "provenance": [{
    "field": "headline",
    "sources": ["csv:candidates.csv", "resume:resume1.docx"],
    "method": "trust-ranking-conflict: resume won over csv",
    "confidence": 0.6
  }]
}
```

---

## Sample Data

The `sample_inputs/` directory contains data designed to exercise every pipeline path:

| Candidate | Sources | Tests |
|-----------|---------|-------|
| Priya Sharma | CSV (×2 duplicate) + resume1.docx | Duplicate collapse, headline conflict, skill aliases |
| Arjun Mehta | CSV + resume3.docx + notes_arjun.txt | 3-source merge, phone without country code |
| Ravi Kumar | CSV + notes_ravi.txt | No-email candidate (phone match key) |
| Sneha Patel | CSV + notes_sneha.txt | Malformed phone dropped |
| Kiran Rao | resume2.pdf only | Resume-only candidate, no CSV match |

---

## Assumptions and Design Decisions

- **Default phone region is India** (`IN`). Numbers without a `+` country code are assumed to be Indian mobile numbers (10 digits).
- **Resume parsing is heuristic, not ML**. Section headers (`EXPERIENCE`, `SKILLS`, etc.) are detected by keyword matching. Dates are detected by regex. This is intentional — no external NLP dependencies.
- **Experience years use only closed roles**. An ongoing role (end = `null`) is excluded from `years_experience` calculation for determinism — the current date would make the output non-reproducible.
- **Match key falls back to a name key** as a last resort when neither email nor phone is available. This is low-precision and marked explicitly in logs.
- **Notes trust position sits between CSV and resume** for company/title (confirmed by a recruiter but informal), and below resume for skills (recruiter-observed vs. resume-stated).
- **Recruiter notes are entirely free-text** — no fixed structure is assumed. All patterns are applied to the full document simultaneously.

## Descoped Items

The following were considered but excluded from this implementation:

- **NLP / entity recognition** for resume parsing. The heuristic approach handles the provided samples accurately without adding model dependencies.
- **Persistent storage / database**. The pipeline is stateless: input files in, JSON out. Adding a database layer would be a straightforward extension.
- **Authentication on the API**. The backend is intended for local use; the CORS policy restricts calls to `localhost:3000`.
- **Deduplication across notes files**. If two notes files refer to the same candidate with different emails, they produce separate profiles. Resolving this would require fuzzy name matching, which is outside the stated scope.
- **Confidence calibration**. The `1.0 / 0.6 / 0.3` values are heuristic. A production system would calibrate these against ground-truth merged records.
