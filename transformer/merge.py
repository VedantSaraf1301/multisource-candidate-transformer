"""
merge.py — Groups raw records by match key, resolves field-level conflicts,
scores confidence, and produces one CandidateProfile per unique candidate.

Match key rules (in priority order):
  1. Normalized lowercased email
  2. Normalized E.164 phone (only when email is absent)
  3. No shared key → records are treated as separate candidates, never merged.

Trust ranking (which source wins when sources conflict):
  CSV wins    : current_company, title, name, email, phone
  Resume wins : headline, years_experience, skills, education, location, links
  Notes sit between CSV and resume:
    - company/title : notes trust == CSV (recruiter confirmed)
    - skills        : notes < resume (resume is more complete)
    - years_exp     : notes explicitly stated → confidence 0.6
    - location/links: notes → used only if resume has none

Confidence heuristics (intentionally simple — documented in README):
  1.0 — field present in exactly one source, no conflict
  0.6 — field present in multiple sources with a conflict, resolved by trust
  0.3 — field derived / inferred (not directly extracted)
  None — field not found in any source (never fabricate a value)

overall_confidence = mean of all per-field confidence scores that are not None.
"""

import hashlib
import logging
from typing import List, Optional, Tuple, Any, Dict

from transformer.models import (
    CandidateProfile, Location, Links, Skill, Experience, Education, ProvenanceEntry,
)
from transformer.normalize import (
    normalize_phone, normalize_email, normalize_name,
    normalize_date, canonicalize_skill, parse_location,
)
from transformer.extractors.csv_extractor import RawCandidate
from transformer.extractors.resume_extractor import RawResumeData
from transformer.extractors.notes_extractor import RawNotesData

logger = logging.getLogger(__name__)

# Trust ranking table

# When two sources both provide a value for the same field and the values differ,
# the source listed here is authoritative. Everything not listed defaults to CSV.
_RESUME_WINS_FIELDS = {"headline", "years_experience", "skills", "education", "location", "links"}


def _winner_for(field: str) -> str:
    """Return 'resume' or 'csv' indicating which source is trusted for this field."""
    return "resume" if field in _RESUME_WINS_FIELDS else "csv"


# Match key derivation

def _match_key_for_csv(rec: RawCandidate) -> Optional[str]:
    """
    Compute the match key for a CSV record.
    Returns "email:<addr>" when email is present, "phone:<e164>" as fallback,
    or None when neither is available (record cannot be matched to anything).
    """
    email = normalize_email(rec.email)
    if email:
        return f"email:{email}"
    phone = normalize_phone(rec.phone)
    if phone:
        return f"phone:{phone}"
    return None


def _match_key_for_resume(rec: RawResumeData) -> Optional[str]:
    """Same logic for resume records."""
    email = normalize_email(rec.email)
    if email:
        return f"email:{email}"
    phone = normalize_phone(rec.phone)
    if phone:
        return f"phone:{phone}"
    return None


def _match_key_for_notes(rec: RawNotesData) -> Optional[str]:
    """Same logic for recruiter notes records."""
    email = normalize_email(rec.email)
    if email:
        return f"email:{email}"
    phone = normalize_phone(rec.phone)
    if phone:
        return f"phone:{phone}"
    return None


def _candidate_id_from_key(match_key: str) -> str:
    """
    Derive a stable, opaque candidate ID from the match key using SHA-1.
    The first 16 hex characters give 64 bits of uniqueness — sufficient for
    thousands of candidates while remaining compact and URL-safe.
    """
    return hashlib.sha1(match_key.encode()).hexdigest()[:16]


# Field-level resolver

def _resolve(
    field: str,
    csv_val: Any,
    resume_val: Any,
    csv_source: str,
    resume_source: str,
    provenance: List[ProvenanceEntry],
    method: str = "direct",
) -> Tuple[Any, Optional[float]]:
    """
    Choose between csv_val and resume_val for the given field.

    Returns (chosen_value, confidence):
      - (None, None)  when neither source has a value
      - (val,  1.0)   when only one source has a value
      - (val,  1.0)   when both agree
      - (val,  0.6)   when both disagree; trust ranking decides the winner
    """
    has_csv    = bool(csv_val)
    has_resume = bool(resume_val)

    # Neither source has a value
    if not has_csv and not has_resume:
        return None, None

    # Only one source has a value
    if has_csv and not has_resume:
        provenance.append(ProvenanceEntry(field=field, source=csv_source, method=method))
        return csv_val, 1.0

    if has_resume and not has_csv:
        provenance.append(ProvenanceEntry(field=field, source=resume_source, method=method))
        return resume_val, 1.0

    # Both have a value — check for conflict
    if csv_val == resume_val:
        # Perfect agreement: log both sources, full confidence
        provenance.append(
            ProvenanceEntry(field=field, source=f"{csv_source}+{resume_source}", method=method)
        )
        return csv_val, 1.0

    # Conflict: apply trust ranking
    winner = _winner_for(field)
    if winner == "csv":
        chosen, winning_source, losing_source, losing_val = csv_val, csv_source, resume_source, resume_val
    else:
        chosen, winning_source, losing_source, losing_val = resume_val, resume_source, csv_source, csv_val

    logger.info(
        "Field '%s' conflict: csv=%r vs resume=%r — %s wins (trust ranking).",
        field, csv_val, resume_val, winner,
    )
    provenance.append(
        ProvenanceEntry(
            field=field,
            source=winning_source,
            method=f"trust-ranking-conflict (discarded {losing_source}={losing_val!r})",
        )
    )
    return chosen, 0.6


# Experience builder

def _build_experience(
    csv_rec: Optional[RawCandidate],
    resume_rec: Optional[RawResumeData],
    csv_source: str,
    resume_source: str,
    provenance: List[ProvenanceEntry],
) -> Tuple[List[Experience], Optional[float]]:
    """
    Build the experience list and derive years_experience.

    Rules:
    - Resume provides the full experience history (resume wins for history).
    - CSV's current_company and title override the most recent (open-ended) resume
      experience entry if they conflict (CSV wins for current_company / title).
    - If the resume has no experience at all but CSV has current_company/title,
      synthesize a single experience entry from CSV data.
    - years_experience is computed by summing the durations of CLOSED roles only
      (roles with both a start and end date in YYYY-MM format).  Open-ended
      ("Present") roles are excluded to keep the output deterministic — using
      today's date would make the result change every day.
    """
    experience: List[Experience] = []

    # --- start with resume experience entries ---
    if resume_rec and resume_rec.experience:
        for raw_exp in resume_rec.experience:
            experience.append(Experience(
                company=raw_exp.get("company", "").strip(),
                title=raw_exp.get("title", "").strip(),
                start=normalize_date(raw_exp.get("start", "")),
                end=normalize_date(raw_exp.get("end", "")),
                summary=raw_exp.get("summary"),
            ))
        provenance.append(ProvenanceEntry(
            field="experience", source=resume_source, method="direct"
        ))

    # --- apply CSV current_company / title override ---
    csv_company = csv_rec.current_company.strip() if csv_rec else ""
    csv_title   = csv_rec.title.strip()           if csv_rec else ""

    if csv_company or csv_title:
        # Find the most recent open-ended experience entry (end=None means "current job")
        current_entry = next(
            (e for e in experience if e.end is None),
            None,
        )

        if current_entry:
            # Check each sub-field for conflicts
            if csv_company and current_entry.company != csv_company:
                logger.info(
                    "current_company conflict: resume=%r, csv=%r — csv wins.",
                    current_entry.company, csv_company,
                )
                provenance.append(ProvenanceEntry(
                    field="experience[current].company",
                    source=csv_source,
                    method=f"trust-ranking-conflict (discarded resume={current_entry.company!r})",
                ))
                current_entry.company = csv_company

            if csv_title and current_entry.title != csv_title:
                logger.info(
                    "title conflict: resume=%r, csv=%r — csv wins.",
                    current_entry.title, csv_title,
                )
                provenance.append(ProvenanceEntry(
                    field="experience[current].title",
                    source=csv_source,
                    method=f"trust-ranking-conflict (discarded resume={current_entry.title!r})",
                ))
                current_entry.title = csv_title
        else:
            # Resume has no current role — synthesize one from CSV
            experience.insert(0, Experience(
                company=csv_company,
                title=csv_title,
                start=None,
                end=None,
                summary=None,
            ))
            provenance.append(ProvenanceEntry(
                field="experience[current]", source=csv_source, method="direct"
            ))

    # --- derive years_experience from closed roles ---
    years = _compute_years_experience(experience)

    return experience, years


def _compute_years_experience(experience: List[Experience]) -> Optional[float]:
    """
    Sum months across all CLOSED experience entries (both start and end are known).
    Returns total years rounded to one decimal, or None if no closed roles exist.

    Open-ended roles ("Present") are intentionally excluded to keep the
    result deterministic (using today's date would vary across runs).
    """
    total_months = 0

    for exp in experience:
        if not exp.start or not exp.end:
            continue  # skip open-ended or undated roles
        try:
            sy, sm = map(int, exp.start.split("-"))
            ey, em = map(int, exp.end.split("-"))
            months = (ey - sy) * 12 + (em - sm)
            if months > 0:
                total_months += months
        except ValueError:
            continue  # malformed date — skip silently

    if total_months == 0:
        return None
    return round(total_months / 12, 1)


# Skills builder

def _build_skills(
    resume_rec: Optional[RawResumeData],
    resume_source: str,
) -> List[Skill]:
    """
    Canonicalize and deduplicate skills from the resume.

    CSV has no skills column, so skills always come from the resume alone.
    Each skill gets confidence=1.0 (single source, no conflict).
    Duplicate canonical names (e.g. "JS" and "javascript") are collapsed
    into one entry; their raw source labels are merged.
    """
    if not resume_rec or not resume_rec.skills:
        return []

    # canonical_name -> Skill (deduplicate as we go)
    seen: Dict[str, Skill] = {}
    for raw_skill in resume_rec.skills:
        canonical = canonicalize_skill(raw_skill)
        if not canonical:
            continue
        if canonical in seen:
            # Already present — just ensure this source is recorded
            if resume_source not in seen[canonical].sources:
                seen[canonical].sources.append(resume_source)
        else:
            seen[canonical] = Skill(name=canonical, confidence=1.0, sources=[resume_source])

    return list(seen.values())


# Education builder

def _build_education(
    resume_rec: Optional[RawResumeData],
    resume_source: str,
    provenance: List[ProvenanceEntry],
) -> List[Education]:
    """
    Convert raw education dicts from the resume extractor into Education models.
    Resume wins for education (CSV has no education column).
    """
    if not resume_rec or not resume_rec.education:
        return []

    entries = []
    for raw_edu in resume_rec.education:
        institution = raw_edu.get("institution", "").strip()
        if not institution:
            continue
        entries.append(Education(
            institution=institution,
            degree=raw_edu.get("degree"),
            field=raw_edu.get("field"),
            end_year=raw_edu.get("end_year"),
        ))

    if entries:
        provenance.append(ProvenanceEntry(
            field="education", source=resume_source, method="direct"
        ))
    return entries


# Per-group merge

def _merge_group(
    csv_recs: List[RawCandidate],
    resume_recs: List[RawResumeData],
    match_key: str,
    notes_recs: Optional[List[RawNotesData]] = None,
) -> CandidateProfile:
    """
    Merge all CSV and resume records that share the same match key into one
    CandidateProfile.  When there are multiple CSV or resume records for the
    same candidate (true duplicates), only the first of each is used — exact
    duplicates carry no additional information.
    """
    # Take the first record from each source (duplicates are already grouped here)
    csv_rec    = csv_recs[0]                          if csv_recs    else None
    resume_rec = resume_recs[0]                       if resume_recs else None
    notes_rec  = (notes_recs or [])[0] if notes_recs else None

    csv_source    = csv_rec.source_label    if csv_rec    else "csv"
    resume_source = resume_rec.source_label if resume_rec else "resume"
    notes_source  = notes_rec.source_label  if notes_rec  else "notes"

    provenance: List[ProvenanceEntry] = []
    field_confidences: List[float] = []

    def track(conf: Optional[float]) -> Optional[float]:
        """Add confidence to the running list if the field was populated."""
        if conf is not None:
            field_confidences.append(conf)
        return conf

    # --- candidate_id ---
    candidate_id = _candidate_id_from_key(match_key)

    # --- full_name ---
    csv_name    = normalize_name(csv_rec.name)    if csv_rec    else ""
    resume_name = normalize_name(resume_rec.name) if resume_rec else ""
    full_name, name_conf = _resolve(
        "full_name", csv_name, resume_name, csv_source, resume_source, provenance
    )
    track(name_conf)
    full_name = full_name or ""

    # --- emails: union of all normalized emails, deduplicated ---
    # Track which sources actually contributed an email so provenance is accurate
    # even when no CSV was provided (where csv_source is just a fallback literal).
    emails: List[str] = []
    _email_sources: List[str] = []
    for raw_email, src in [
        (csv_rec.email    if csv_rec    else None, csv_source    if csv_rec    else None),
        (resume_rec.email if resume_rec else None, resume_source if resume_rec else None),
    ]:
        if not raw_email or src is None:
            continue
        normalized = normalize_email(raw_email)
        if normalized and normalized not in emails:
            emails.append(normalized)
            if src not in _email_sources:
                _email_sources.append(src)

    if emails:
        track(1.0)
        provenance.append(ProvenanceEntry(
            field="emails",
            source="+".join(_email_sources),
            method="direct",
        ))

    # --- phones: union of all valid E.164 phones, deduplicated ---
    phones = []
    raw_phones = []
    if csv_rec    and csv_rec.phone:    raw_phones.append((csv_rec.phone,    csv_source))
    if resume_rec and resume_rec.phone: raw_phones.append((resume_rec.phone, resume_source))

    for raw_phone, src in raw_phones:
        e164 = normalize_phone(raw_phone)
        if e164 and e164 not in phones:
            phones.append(e164)
            provenance.append(ProvenanceEntry(field="phones", source=src, method="direct"))

    if phones:
        track(1.0)

    # --- location: resume wins ---
    raw_location = resume_rec.raw_location if resume_rec else ""
    if not raw_location and csv_rec:
        # CSV has no location column — nothing to fall back to
        raw_location = ""
    loc_dict = parse_location(raw_location)
    location = Location(**loc_dict)
    if any(loc_dict.values()):
        track(1.0)
        provenance.append(ProvenanceEntry(
            field="location", source=resume_source, method="direct"
        ))

    # --- links: resume wins (CSV has no link columns) ---
    # RawResumeData defaults linkedin/github/portfolio to "" when not found;
    # coerce empty strings to None so the JSON output emits null, not "".
    links = Links(
        linkedin  = (resume_rec.linkedin  or None) if resume_rec else None,
        github    = (resume_rec.github    or None) if resume_rec else None,
        portfolio = (resume_rec.portfolio or None) if resume_rec else None,
    )
    if any([links.linkedin, links.github, links.portfolio]):
        track(1.0)
        provenance.append(ProvenanceEntry(
            field="links", source=resume_source, method="direct"
        ))

    # --- headline: resume wins over CSV; CSV title is fallback ---
    resume_headline = resume_rec.headline if resume_rec else ""
    # CSV doesn't have a headline column, but we can use its title as a weak fallback
    csv_headline    = csv_rec.title if csv_rec else ""
    headline, hl_conf = _resolve(
        "headline", csv_headline, resume_headline, csv_source, resume_source, provenance
    )
    track(hl_conf)

    # --- experience + years_experience ---
    experience, years_exp = _build_experience(
        csv_rec, resume_rec, csv_source, resume_source, provenance
    )
    if experience:
        track(1.0)
    if years_exp is not None:
        # Derived from date arithmetic → confidence 0.3
        track(0.3)
        provenance.append(ProvenanceEntry(
            field="years_experience", source=resume_source, method="derived-from-experience-dates"
        ))

    # --- skills ---
    skills = _build_skills(resume_rec, resume_source)
    if skills:
        track(1.0)
        provenance.append(ProvenanceEntry(
            field="skills", source=resume_source, method="direct"
        ))

    # --- education ---
    education = _build_education(resume_rec, resume_source, provenance)
    if education:
        track(1.0)

    # --- notes: blend in additional data from recruiter notes ---
    # Notes are processed AFTER resume so resume always wins for overlapping fields.
    if notes_rec:
        # Skills from notes: append any that aren't already in the skills list
        if notes_rec.skills:
            existing_names = {s.name for s in skills}
            for raw_skill in notes_rec.skills:
                canonical = canonicalize_skill(raw_skill)
                if canonical in existing_names:
                    # Skill already known — add notes as a second source
                    for s in skills:
                        if s.name == canonical and notes_source not in s.sources:
                            s.sources.append(notes_source)
                            # Confirmed by a second source → boost to 1.0 if it was lower
                            s.confidence = max(s.confidence, 1.0)
                else:
                    # New skill seen only in notes — confidence 0.6 (observed, not self-reported)
                    skills.append(Skill(name=canonical, confidence=0.6, sources=[notes_source]))
                    existing_names.add(canonical)
            provenance.append(ProvenanceEntry(
                field="skills[notes]", source=notes_source, method="direct"
            ))

        # Location from notes: fill in only if resume gave us nothing
        if not any(loc_dict.values()) and notes_rec.raw_location:
            from transformer.normalize import parse_location as _parse_loc
            notes_loc = _parse_loc(notes_rec.raw_location)
            if any(notes_loc.values()):
                location = Location(**notes_loc)
                track(1.0)
                provenance.append(ProvenanceEntry(
                    field="location", source=notes_source, method="direct"
                ))

        # LinkedIn from notes: fill in only if resume gave us nothing
        if notes_rec.linkedin and not links.linkedin:
            links.linkedin = notes_rec.linkedin
            provenance.append(ProvenanceEntry(
                field="links.linkedin", source=notes_source, method="direct"
            ))

        # years_experience from notes: explicitly stated → confidence 0.6
        # Only use if not already set (resume derivation takes priority)
        if notes_rec.years_experience is not None and years_exp is None:
            years_exp = notes_rec.years_experience
            track(0.6)
            provenance.append(ProvenanceEntry(
                field="years_experience", source=notes_source,
                method="explicitly-stated-in-notes"
            ))

    # --- overall_confidence: mean of all populated field confidences ---
    overall_confidence = (
        round(sum(field_confidences) / len(field_confidences), 4)
        if field_confidences else 0.0
    )

    return CandidateProfile(
        candidate_id=candidate_id,
        full_name=full_name,
        emails=emails,
        phones=phones,
        location=location,
        links=links,
        headline=headline,
        years_experience=years_exp,
        skills=skills,
        experience=experience,
        education=education,
        provenance=provenance,
        overall_confidence=overall_confidence,
    )


# Public API

def merge_candidates(
    csv_records: List[RawCandidate],
    resume_records: List[RawResumeData],
    notes_records: Optional[List[RawNotesData]] = None,
) -> List[CandidateProfile]:
    """
    Group all raw records by match key, then merge each group into one
    CandidateProfile. Returns one profile per unique candidate.

    Records with no usable match key (no email AND no valid phone) are still
    processed as solo candidates using a synthetic key derived from their name,
    so no data is silently dropped.

    Args:
        csv_records:    List of RawCandidate from the CSV extractor.
        resume_records: List of RawResumeData from the resume extractor.
                        None entries are filtered out automatically.

    Returns:
        List of CandidateProfile, one per unique candidate.
    """
    # Filter out None records (failed extractions return None)
    resume_records = [r for r in resume_records if r is not None]
    notes_records  = [r for r in (notes_records or []) if r is not None]

    # Build groups: match_key -> {"csv": [...], "resume": [...], "notes": [...]}
    groups: Dict[str, Dict[str, list]] = {}

    def _add_to_group(key: str, source_type: str, record: Any) -> None:
        if key not in groups:
            groups[key] = {"csv": [], "resume": [], "notes": []}
        groups[key][source_type].append(record)

    # Index all CSV records
    for rec in csv_records:
        key = _match_key_for_csv(rec)
        if key is None:
            synthetic = f"name:{normalize_name(rec.name).lower()}"
            logger.warning(
                "CSV record for %r has no email or phone — using synthetic key %r.",
                rec.name, synthetic,
            )
            key = synthetic
        _add_to_group(key, "csv", rec)

    # Index all resume records
    for rec in resume_records:
        key = _match_key_for_resume(rec)
        if key is None:
            synthetic = f"name:{normalize_name(rec.name).lower()}"
            logger.warning(
                "Resume record for %r has no email or phone — using synthetic key %r.",
                rec.name, synthetic,
            )
            key = synthetic
        _add_to_group(key, "resume", rec)

    # Index all notes records
    for rec in notes_records:
        key = _match_key_for_notes(rec)
        if key is None:
            synthetic = f"name:{normalize_name(rec.name).lower()}"
            logger.warning(
                "Notes record for %r has no email or phone — using synthetic key %r.",
                rec.name, synthetic,
            )
            key = synthetic
        _add_to_group(key, "notes", rec)

    # Merge each group
    profiles: List[CandidateProfile] = []
    for match_key, sources in groups.items():
        csv_recs    = sources["csv"]
        resume_recs = sources["resume"]
        notes_recs  = sources["notes"]

        n_csv    = len(csv_recs)
        n_resume = len(resume_recs)
        n_notes  = len(notes_recs)

        if n_csv > 1:
            logger.info(
                "Duplicate CSV rows for match key %r (%d rows) — using first, discarding rest.",
                match_key, n_csv,
            )

        profile = _merge_group(csv_recs, resume_recs, match_key, notes_recs or None)
        profiles.append(profile)
        logger.info(
            "Merged candidate %r (key=%r) from csv=%d, resume=%d, notes=%d source(s).",
            profile.full_name, match_key, n_csv, n_resume, n_notes,
        )

    return profiles
