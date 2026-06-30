import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)


@dataclass
class RawResumeData:
    name: str = ""
    email: str = ""
    phone: str = ""
    raw_location: str = ""
    headline: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    skills: List[str] = field(default_factory=list)
    experience: List[Dict] = field(default_factory=list)
    education: List[Dict] = field(default_factory=list)
    years_experience: Optional[float] = None
    source_label: str = ""


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"[\+\(]?[\d][\d\s\-\(\)\.]{6,}[\d]")

_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w\-]+", re.IGNORECASE)
_GITHUB_RE   = re.compile(r"github\.com/[\w\-]+",   re.IGNORECASE)
_URL_RE      = re.compile(r"https?://[\w./\-\?\=\&\%\+\#]+", re.IGNORECASE)

_SECTION_HEADER_RE = re.compile(
    r"^(skills?|technical skills?|core competencies|"
    r"work experience|professional experience|experience|"
    r"education|academic background|"
    r"summary|profile|objective|about me|"
    r"projects?|certifications?|achievements?|awards?)\s*[:\-]?\s*$",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|\d{4}[-/]\d{2}"
    r"|\d{4}",
    re.IGNORECASE,
)

# Captures a year range like "2023 - 2027" or "2023 - Present".
# Group 1 = start year, Group 2 = end year or present-word.
# Used to extract the GRADUATION year (group 2), not admission year (group 1).
_EDU_YEAR_RANGE_RE = re.compile(
    r"\b(\d{4})\s*[-–-]\s*(\d{4}|Present|Current|Now)\b",
    re.IGNORECASE,
)

_DATE_RANGE_RE = re.compile(
    r"("
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|\d{4}[-/]\d{2}"
    r"|\d{4}"
    r")"
    r"\s*[-–to]+\s*"
    r"(Present|Current|Now|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|\d{4}[-/]\d{2}"
    r"|\d{4}"
    r")",
    re.IGNORECASE,
)


def _extract_text_from_pdf(path: Path) -> str:
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
    return "\n".join(pages)


def _extract_text_from_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _split_into_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {"header": []}
    current = "header"

    for line in text.splitlines():
        stripped = line.strip()
        if _SECTION_HEADER_RE.match(stripped):
            current = stripped.lower().rstrip(":-").strip()
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

    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _parse_header(header_text: str) -> Dict:
    lines = [l.strip() for l in header_text.splitlines() if l.strip()]
    result = {
        "name": "", "email": "", "phone": "",
        "raw_location": "", "headline": "",
        "linkedin": "", "github": "", "portfolio": "",
    }

    email_match = _EMAIL_RE.search(header_text)
    if email_match:
        result["email"] = email_match.group()

    phone_match = _PHONE_RE.search(header_text)
    if phone_match:
        result["phone"] = phone_match.group().strip()

    li = _LINKEDIN_RE.search(header_text)
    if li:
        result["linkedin"] = "https://" + li.group()
    gh = _GITHUB_RE.search(header_text)
    if gh:
        result["github"] = "https://" + gh.group()

    for line in lines:
        if _EMAIL_RE.search(line):
            continue
        if _PHONE_RE.search(line):
            continue
        if _LINKEDIN_RE.search(line) or _GITHUB_RE.search(line):
            continue
        if re.search(r"\d{4}", line):
            continue
        words = line.split()
        if 2 <= len(words) <= 5:
            result["name"] = line
            break

    name_found = False
    for line in lines:
        if not name_found and line == result["name"]:
            name_found = True
            continue
        if _EMAIL_RE.search(line) or _PHONE_RE.search(line):
            continue
        if _LINKEDIN_RE.search(line) or _GITHUB_RE.search(line):
            continue
        if len(line.split()) >= 3 and not re.search(r"\d{5,}", line):
            result["headline"] = line
            break

    # Per-line search avoids the regex matching across newlines into unrelated text.
    loc_re = re.compile(
        r"^[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*"
        r",\s*[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*"
        r"(?:,\s*[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*)?$"
    )
    for line in lines:
        if loc_re.match(line):
            result["raw_location"] = line
            break

    return result


def _parse_skills(skills_text: str) -> List[str]:
    cleaned = re.sub(r"[•·▪▸\|]+", ",", skills_text)

    # Strip "Category Label:" prefix per line so it doesn't leak into skill names.
    lines = cleaned.split("\n")
    stripped_lines = []
    for line in lines:
        colon_pos = line.find(":")
        if colon_pos != -1:
            line = line[colon_pos + 1:]
        stripped_lines.append(line)
    cleaned = "\n".join(stripped_lines)

    parts  = re.split(r"[,\n]+", cleaned)
    skills = [p.strip() for p in parts if p.strip() and len(p.strip()) > 1]
    return skills


def _parse_experience(exp_text: str) -> List[Dict]:
    entries = []
    lines   = [l.strip() for l in exp_text.splitlines()]

    i = 0
    while i < len(lines):
        line       = lines[i]
        date_match = _DATE_RANGE_RE.search(line)

        if not date_match:
            i += 1
            continue

        start_raw = date_match.group(1).strip()
        end_raw   = date_match.group(2).strip()

        header_part = (line[: date_match.start()] + line[date_match.end():]).strip()
        header_part = header_part.rstrip("|–- ").strip()

        parts = re.split(r"\s*[\|@–]\s*|\s+at\s+", header_part, maxsplit=1)
        if len(parts) == 2:
            title   = parts[0].strip()
            company = parts[1].strip().rstrip("|–- ").strip()
        else:
            title   = ""
            company = header_part

        i += 1
        summary_lines = []
        while i < len(lines):
            if _DATE_RANGE_RE.search(lines[i]):
                break
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
    entries  = []
    blocks   = re.split(r"\n{2,}", edu_text.strip())

    degree_re = re.compile(
        r"(B\.?Tech|M\.?Tech|B\.?E|M\.?E|B\.?Sc|M\.?Sc|MBA|PhD|"
        r"Bachelor|Master|Doctor|Diploma|B\.?A|M\.?A)[^\n]*",
        re.IGNORECASE,
    )

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Use the second year in a range (graduation), not the first (admission).
        range_match = _EDU_YEAR_RANGE_RE.search(block)
        if range_match:
            end_str  = range_match.group(2)
            end_year = None if end_str.lower() in ("present", "current", "now") else int(end_str)
            clean_block = (block[:range_match.start()] + block[range_match.end():]).strip()
        else:
            year_match  = re.search(r"\b(19|20)\d{2}\b", block)
            end_year    = int(year_match.group()) if year_match else None
            clean_block = block

        lines = [l.strip() for l in clean_block.splitlines() if l.strip()]
        if not lines:
            continue

        institution    = lines[0]
        degree         = None
        field_of_study = None

        for line in lines[1:]:
            dm = degree_re.search(line)
            if dm:
                degree_line = dm.group().strip()
                in_split    = re.split(r"\s+in\s+", degree_line, maxsplit=1, flags=re.IGNORECASE)
                degree      = in_split[0].strip()
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


def extract_from_resume(resume_path: str | Path) -> Optional[RawResumeData]:
    """Returns RawResumeData on success, None on any failure (missing file, parse error, etc.)."""
    path = Path(resume_path)

    if not path.exists():
        logger.warning("Resume file not found: %s - skipping.", path)
        return None

    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        logger.warning("Unsupported resume format '%s' for file %s - skipping.", suffix, path)
        return None

    try:
        if suffix == ".pdf":
            text = _extract_text_from_pdf(path)
        else:
            text = _extract_text_from_docx(path)

        if not text.strip():
            logger.warning("Resume %s produced no extractable text - skipping.", path.name)
            return None

        sections    = _split_into_sections(text)
        header_data = _parse_header(sections.get("header", ""))
        skills      = _parse_skills(sections.get("skills", ""))
        experience  = _parse_experience(sections.get("experience", ""))
        education   = _parse_education(sections.get("education", ""))

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
            "Resume extractor: parsed %s - name=%r, skills=%d, exp=%d, edu=%d",
            path.name, data.name, len(data.skills), len(data.experience), len(data.education),
        )
        return data

    except Exception as exc:
        logger.warning("Failed to parse resume %s: %s - skipping.", path.name, exc)
        return None
