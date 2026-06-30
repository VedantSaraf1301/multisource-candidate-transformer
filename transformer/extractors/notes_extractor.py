import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RawNotesData:
    name: str = ""
    email: str = ""
    phone: str = ""
    raw_location: str = ""
    current_company: str = ""
    title: str = ""
    linkedin: str = ""
    skills: List[str] = field(default_factory=list)
    years_experience: Optional[float] = None
    source_label: str = ""


_EMAIL_RE    = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE    = re.compile(r"[\+\(]?[\d][\d\s\-\(\)\.]{6,}[\d]")
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w\-]+", re.IGNORECASE)

_YEARS_RE = re.compile(r"(\d+)\+?\s+years?", re.IGNORECASE)

_TITLE_AT_COMPANY_RE = re.compile(
    r"([\w\s]+?)\s+at\s+([A-Z][\w\s&,\.]+?)(?:\s*[,\.\n]|$)",
)

_SKILLS_SECTION_RE = re.compile(
    r"(?:technical\s+)?skills?\s*(?:\w+\s*)?[:]\s*(.+)",
    re.IGNORECASE,
)

_NOTES_HEADER_NAME_RE = re.compile(
    r"(?:recruiter\s+notes?\s*[-–:]\s*|candidate\s*:\s*)([\w\s]+)",
    re.IGNORECASE,
)

_LOCATION_RE = re.compile(
    r"^[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*"
    r",\s*[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*"
    r"(?:,\s*[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*)?$"
)


def extract_from_notes(notes_path: str | Path) -> Optional[RawNotesData]:
    """Returns RawNotesData on success, None on any failure."""
    path = Path(notes_path)

    if not path.exists():
        logger.warning("Notes file not found: %s - skipping.", path)
        return None

    if path.suffix.lower() != ".txt":
        logger.warning("Unsupported notes format '%s' - skipping.", path.suffix)
        return None

    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("Could not read notes file %s: %s - skipping.", path.name, exc)
        return None

    if not text:
        logger.warning("Notes file %s is empty - skipping.", path.name)
        return None

    try:
        data = _parse_notes(text, source_label=f"notes:{path.name}")
        logger.info(
            "Notes extractor: parsed %s - name=%r, skills=%d, company=%r",
            path.name, data.name, len(data.skills), data.current_company,
        )
        return data
    except Exception as exc:
        logger.warning("Failed to parse notes %s: %s - skipping.", path.name, exc)
        return None


def _parse_notes(text: str, source_label: str) -> RawNotesData:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    data  = RawNotesData(source_label=source_label)

    m = _EMAIL_RE.search(text)
    if m:
        data.email = m.group()

    # Iterate all matches and take the first that is a valid phone, not a date string.
    from transformer.normalize import normalize_phone as _norm_phone
    for m in _PHONE_RE.finditer(text):
        raw = m.group().strip()
        if re.search(r"[A-Za-z]", raw):
            continue
        if _norm_phone(raw):
            data.phone = raw
            break

    m = _LINKEDIN_RE.search(text)
    if m:
        data.linkedin = "https://" + m.group()

    years_matches = _YEARS_RE.findall(text)
    if years_matches:
        data.years_experience = float(max(int(y) for y in years_matches))

    skills_match = _SKILLS_SECTION_RE.search(text)
    if skills_match:
        raw_skills  = re.split(r"[,\n]+", skills_match.group(1))
        data.skills = [s.strip() for s in raw_skills if s.strip() and len(s.strip()) > 1]

    tc_match = _TITLE_AT_COMPANY_RE.search(text)
    if tc_match:
        data.title           = tc_match.group(1).strip()
        data.current_company = tc_match.group(2).strip().rstrip(".,")

    hm = _NOTES_HEADER_NAME_RE.search(text)
    if hm:
        data.name = hm.group(1).splitlines()[0].strip()
    else:
        for line in lines:
            if re.search(r"recruiter|notes|date|email|phone|skills|location",
                         line, re.IGNORECASE):
                continue
            if _EMAIL_RE.search(line) or _PHONE_RE.search(line):
                continue
            words = line.split()
            if 2 <= len(words) <= 4 and all(w[0].isupper() for w in words if w):
                if not re.search(r"\d", line):
                    data.name = line
                    break

    for line in lines:
        if _LOCATION_RE.match(line):
            data.raw_location = line
            break

    return data
