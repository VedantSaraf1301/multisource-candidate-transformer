"""
resume_extractor.py — Extract candidate fields from PDF and DOCX resume files.

Approach: pull raw text from the file, split it into named sections, then
run targeted regex patterns against each section.  This is intentionally
heuristic / regex-based — no NLP or ML models are used.

Libraries used:
  pdfplumber  — PDF text extraction
  python-docx — DOCX text extraction
"""

import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Raw data container
# ---------------------------------------------------------------------------

@dataclass
class RawResumeData:
    """
    All fields pulled from a single resume file, before any normalization.
    Values are raw strings exactly as found in the document.
    """
    name: str = ""
    email: str = ""
    phone: str = ""
    raw_location: str = ""      # e.g. "Bengaluru, Karnataka, India" — parsed in normalize.py
    headline: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    skills: List[str] = field(default_factory=list)
    # Each entry is a dict with keys: company, title, start, end, summary
    experience: List[Dict] = field(default_factory=list)
    # Each entry is a dict with keys: institution, degree, field, end_year
    education: List[Dict] = field(default_factory=list)
    years_experience: Optional[float] = None
    # e.g. "resume:resume1.pdf" — set by extract_from_resume()
    source_label: str = ""


# ---------------------------------------------------------------------------
# Compiled regex patterns (compiled once at import time for performance)
# ---------------------------------------------------------------------------

# Standard email pattern
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# Phone: starts with +, (, or a digit; contains digits/spaces/dashes/parens/dots
_PHONE_RE = re.compile(r"[\+\(]?[\d][\d\s\-\(\)\.]{6,}[\d]")

# Social links — capture just the URL path, not the surrounding prose
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w\-]+", re.IGNORECASE)
_GITHUB_RE   = re.compile(r"github\.com/[\w\-]+",   re.IGNORECASE)

# Generic URL for portfolio / other links
_URL_RE = re.compile(r"https?://[\w./\-\?\=\&\%\+\#]+", re.IGNORECASE)

# Section header lines — a line that is ONLY a header word (optionally followed by : or -)
# We use this to split the resume into labelled sections.
_SECTION_HEADER_RE = re.compile(
    r"^(skills?|technical skills?|core competencies|"
    r"work experience|professional experience|experience|"
    r"education|academic background|"
    r"summary|profile|objective|about me|"
    r"projects?|certifications?|achievements?|awards?)\s*[:\-]?\s*$",
    re.IGNORECASE,
)

# Date string patterns used inside the experience section.
# Matches: "Jan 2021", "January 2021", "2021-01", "2021"
_DATE_RE = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|\d{4}[-/]\d{2}"
    r"|\d{4}",
    re.IGNORECASE,
)

# Year range inside an education block: "2023 – 2027" or "2023 – Present".
# Group 1 = start year, Group 2 = end year or present-word.
# Used by _parse_education() to extract the GRADUATION year (group 2), not the
# admission year (group 1), which is what a plain re.search() for \d{4} would find.
_EDU_YEAR_RANGE_RE = re.compile(
    r"\b(\d{4})\s*[-–—]\s*(\d{4}|Present|Current|Now)\b",
    re.IGNORECASE,
)

# A date-range line like "Jan 2020 – Dec 2021" or "2019 - Present"
_DATE_RANGE_RE = re.compile(
    r"("
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|\d{4}[-/]\d{2}"
    r"|\d{4}"
    r")"
    r"\s*[-–—to]+\s*"
    r"(Present|Current|Now|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|\d{4}[-/]\d{2}"
    r"|\d{4}"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(path: Path) -> str:
    """Use pdfplumber to pull all text from every page of a PDF."""
    import pdfplumber  # imported here so import errors surface with a clear message
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
    return "\n".join(pages)


def _extract_text_from_docx(path: Path) -> str:
    """Use python-docx to pull text from every paragraph in a DOCX."""
    from docx import Document  # imported here for the same reason
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


# ---------------------------------------------------------------------------
# Section splitter
# ---------------------------------------------------------------------------

def _split_into_sections(text: str) -> Dict[str, str]:
    """
    Walk through the resume line by line. When a line matches a known section
    header, start collecting lines under that section name. Everything before
    the first recognised header goes into the "header" bucket (name, contact
    info, headline usually live there).

    Returns a dict of { section_name_lower: "body text" }.
    """
    sections: Dict[str, List[str]] = {"header": []}
    current = "header"

    for line in text.splitlines():
        stripped = line.strip()
        if _SECTION_HEADER_RE.match(stripped):
            # Normalise the header label so callers can use consistent keys
            current = stripped.lower().rstrip(":-").strip()
            # Map aliases to canonical names
            if "experience" in current:
                current = "experience"
            elif "skill" in current or "competenc" in current:
                current = "skills"
            elif "education" in current or "academic" in current:
                current = "education"
            elif current in ("summary", "profile", "objective", "about me"):
                current = "summary"
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(stripped)

    # Join each section's lines back into a single string
    return {k: "\n".join(v).strip() for k, v in sections.items()}


# ---------------------------------------------------------------------------
# Section-specific parsers
# ---------------------------------------------------------------------------

def _parse_header(header_text: str) -> Dict:
    """
    Extract name, email, phone, location, headline, and social links from
    the top block of the resume (everything before the first section header).

    Heuristic for name: the first non-empty line that contains no '@', no
    digit sequences, and no URL — this is almost always the candidate's name.
    """
    lines = [l.strip() for l in header_text.splitlines() if l.strip()]
    result = {
        "name": "", "email": "", "phone": "",
        "raw_location": "", "headline": "",
        "linkedin": "", "github": "", "portfolio": "",
    }

    # --- email ---
    email_match = _EMAIL_RE.search(header_text)
    if email_match:
        result["email"] = email_match.group()

    # --- phone ---
    phone_match = _PHONE_RE.search(header_text)
    if phone_match:
        result["phone"] = phone_match.group().strip()

    # --- social links ---
    li = _LINKEDIN_RE.search(header_text)
    if li:
        result["linkedin"] = "https://" + li.group()
    gh = _GITHUB_RE.search(header_text)
    if gh:
        result["github"] = "https://" + gh.group()

    # --- name (first clean line with ≥ 2 words, no special chars) ---
    for line in lines:
        # Skip lines that look like contact info
        if _EMAIL_RE.search(line):
            continue
        if _PHONE_RE.search(line):
            continue
        if _LINKEDIN_RE.search(line) or _GITHUB_RE.search(line):
            continue
        if re.search(r"\d{4}", line):   # skip lines containing years
            continue
        words = line.split()
        if 2 <= len(words) <= 5:        # names are usually 2–5 words
            result["name"] = line
            break

    # --- headline: first sentence-like line that isn't the name or contact ---
    name_found = False
    for line in lines:
        if not name_found and line == result["name"]:
            name_found = True
            continue
        if _EMAIL_RE.search(line) or _PHONE_RE.search(line):
            continue
        if _LINKEDIN_RE.search(line) or _GITHUB_RE.search(line):
            continue
        # A headline tends to contain job-title words and be a short phrase
        if len(line.split()) >= 3 and not re.search(r"\d{5,}", line):
            result["headline"] = line
            break

    # --- raw location: search each line individually for "City, Region" pattern ---
    # Using per-line search avoids the regex matching across newlines and grabbing
    # unrelated text from adjacent lines.
    loc_re = re.compile(
        r"^[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*"   # City (one or more title-case words)
        r",\s*[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*"  # Region
        r"(?:,\s*[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*)?$"  # optional Country
    )
    for line in lines:
        if loc_re.match(line):
            result["raw_location"] = line
            break

    return result


def _parse_skills(skills_text: str) -> List[str]:
    """
    Skills sections typically list items separated by commas, bullets, or newlines.
    Split on all of those and return a flat list of raw skill strings.

    We avoid splitting on hyphens that sit between word characters (e.g. "Scikit-learn")
    by only replacing bullet/pipe characters, then splitting on commas and newlines.

    Category-label prefixes like "Programming Languages: C, C++, ..." are stripped
    before splitting so the label never leaks into the first skill on that line.
    """
    # Replace bullet characters and pipes with commas; do NOT replace hyphens
    # because they appear legitimately inside skill names (e.g. "Scikit-learn").
    cleaned = re.sub(r"[•·▪▸\|]+", ",", skills_text)

    # Strip "Category Label:" prefix from each line before splitting on commas.
    # Lines like "Programming Languages: C, C++, Python" must not produce
    # "programming languages: c" as the first skill.
    lines = cleaned.split("\n")
    stripped_lines = []
    for line in lines:
        colon_pos = line.find(":")
        if colon_pos != -1:
            line = line[colon_pos + 1:]
        stripped_lines.append(line)
    cleaned = "\n".join(stripped_lines)

    parts = re.split(r"[,\n]+", cleaned)
    skills = [p.strip() for p in parts if p.strip() and len(p.strip()) > 1]
    return skills


def _parse_experience(exp_text: str) -> List[Dict]:
    """
    Parse work experience entries from the experience section text.

    Heuristic strategy — line-by-line scan (more robust than block splitting):
    1. Walk every line looking for a date range pattern ("Jan 2021 - Present").
    2. When a date range is found, the same line contains "Title | Company".
    3. Lines between two date-range headers are collected as the job summary.

    This approach works even when the source file has no blank lines between
    entries (common with DOCX files where every paragraph is one line).
    """
    entries = []
    lines = [l.strip() for l in exp_text.splitlines()]

    i = 0
    while i < len(lines):
        line = lines[i]
        date_match = _DATE_RANGE_RE.search(line)

        if not date_match:
            i += 1
            continue

        # Extract the date range
        start_raw = date_match.group(1).strip()
        end_raw   = date_match.group(2).strip()

        # Remove the date range from the line; what remains is "Title | Company"
        header_part = (line[: date_match.start()] + line[date_match.end() :]).strip()
        # Strip stray trailing delimiters left after removing the date
        header_part = header_part.rstrip("|–- ").strip()

        # Split on "|", "@", "–", or " at " to separate title and company
        parts = re.split(r"\s*[\|@–]\s*|\s+at\s+", header_part, maxsplit=1)
        if len(parts) == 2:
            title   = parts[0].strip()
            company = parts[1].strip().rstrip("|–- ").strip()
        else:
            title   = ""
            company = header_part

        # Collect the following lines as the summary until the next date-range line
        i += 1
        summary_lines = []
        while i < len(lines):
            if _DATE_RANGE_RE.search(lines[i]):
                break   # next job entry starts here
            cleaned = lines[i].lstrip("•·▪▸- ").strip()
            if cleaned:
                summary_lines.append(cleaned)
            i += 1

        summary = " ".join(summary_lines) if summary_lines else None

        entries.append({
            "company": company,
            "title":   title,
            "start":   start_raw,
            "end":     end_raw,
            "summary": summary,
        })

    return entries


def _parse_education(edu_text: str) -> List[Dict]:
    """
    Parse education entries. Each entry is expected to be a block with:
    - Institution name
    - Degree and/or field of study
    - A year range or single year (end year / graduation year)

    Year-range handling: "2023 – 2027" must produce end_year=2027 (graduation),
    not 2023 (admission).  The entire range substring is stripped from the block
    before extracting degree/field so it never leaks into those strings.
    """
    entries = []
    blocks = re.split(r"\n{2,}", edu_text.strip())

    degree_re = re.compile(
        r"(B\.?Tech|M\.?Tech|B\.?E|M\.?E|B\.?Sc|M\.?Sc|MBA|PhD|"
        r"Bachelor|Master|Doctor|Diploma|B\.?A|M\.?A)[^\n]*",
        re.IGNORECASE,
    )

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Detect a year range first so we can take the SECOND year (graduation)
        # and strip it out before extracting the field-of-study string.
        range_match = _EDU_YEAR_RANGE_RE.search(block)
        if range_match:
            end_str = range_match.group(2)
            end_year = None if end_str.lower() in ("present", "current", "now") else int(end_str)
            # Remove the matched range from the block so it doesn't pollute field text
            clean_block = (block[:range_match.start()] + block[range_match.end():]).strip()
        else:
            # No range: fall back to first standalone 4-digit year
            year_match = re.search(r"\b(19|20)\d{2}\b", block)
            end_year = int(year_match.group()) if year_match else None
            clean_block = block

        lines = [l.strip() for l in clean_block.splitlines() if l.strip()]
        if not lines:
            continue

        institution = lines[0]   # First line is almost always the institution
        degree = None
        field_of_study = None

        # Look for degree keywords in remaining lines
        for line in lines[1:]:
            dm = degree_re.search(line)
            if dm:
                degree_line = dm.group().strip()
                # Try to split "B.Tech in Computer Science" into degree + field
                in_split = re.split(r"\s+in\s+", degree_line, maxsplit=1, flags=re.IGNORECASE)
                degree = in_split[0].strip()
                if len(in_split) == 2:
                    field_of_study = in_split[1].strip()
                break

        entries.append({
            "institution": institution,
            "degree":      degree,
            "field":       field_of_study,
            "end_year":    end_year,
        })

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_from_resume(resume_path: str | Path) -> Optional[RawResumeData]:
    """
    Extract raw candidate data from a single PDF or DOCX resume file.

    Robustness guarantees:
    - Unsupported file extension → warning, returns None.
    - File not found → warning, returns None.
    - Any extraction / parsing error → warning, returns None.
      Callers should check for None and treat it as "no data from this source".

    Args:
        resume_path: Path to the resume file.

    Returns:
        RawResumeData on success, None on any failure.
    """
    path = Path(resume_path)

    if not path.exists():
        logger.warning("Resume file not found: %s — skipping.", path)
        return None

    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        logger.warning("Unsupported resume format '%s' for file %s — skipping.", suffix, path)
        return None

    try:
        # Step 1: get raw text from the file
        if suffix == ".pdf":
            text = _extract_text_from_pdf(path)
        else:
            text = _extract_text_from_docx(path)

        if not text.strip():
            logger.warning("Resume %s produced no extractable text — skipping.", path.name)
            return None

        # Step 2: split text into labelled sections
        sections = _split_into_sections(text)

        # Step 3: parse each section
        header_data = _parse_header(sections.get("header", ""))
        skills      = _parse_skills(sections.get("skills", ""))
        experience  = _parse_experience(sections.get("experience", ""))
        education   = _parse_education(sections.get("education", ""))

        # Step 4: assemble the raw result object
        data = RawResumeData(
            name=header_data["name"],
            email=header_data["email"],
            phone=header_data["phone"],
            raw_location=header_data["raw_location"],
            headline=header_data["headline"],
            linkedin=header_data["linkedin"],
            github=header_data["github"],
            portfolio=header_data["portfolio"],
            skills=skills,
            experience=experience,
            education=education,
            source_label=f"resume:{path.name}",
        )

        logger.info(
            "Resume extractor: parsed %s — name=%r, skills=%d, exp=%d, edu=%d",
            path.name, data.name, len(data.skills), len(data.experience), len(data.education),
        )
        return data

    except Exception as exc:
        logger.warning("Failed to parse resume %s: %s — skipping.", path.name, exc)
        return None
