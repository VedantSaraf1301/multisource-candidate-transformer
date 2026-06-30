"""
models.py — Pydantic models for the canonical candidate schema.

Every field in CandidateProfile mirrors the output spec exactly.
This is the single source of truth for the internal record; nothing
downstream is allowed to add or rename fields here.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# Sub-models (used as nested objects inside CandidateProfile)

class Location(BaseModel):
    """Geographic location of the candidate."""
    city: Optional[str] = None
    region: Optional[str] = None
    # ISO-3166 alpha-2 country code, e.g. "IN", "US"
    country: Optional[str] = None


class Links(BaseModel):
    """Online presence / social links."""
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    # Any links that don't fit the named slots above
    other: List[str] = Field(default_factory=list)


class Skill(BaseModel):
    """A single skill with its extraction confidence and which sources mentioned it."""
    name: str
    # 0.0–1.0 confidence score (see merge.py for scoring rules)
    confidence: float
    # e.g. ["csv", "resume:resume1.pdf"]
    sources: List[str] = Field(default_factory=list)


class Experience(BaseModel):
    """One job / work-experience entry."""
    company: str
    title: str
    # YYYY-MM format; None when not found in source
    start: Optional[str] = None
    end: Optional[str] = None
    summary: Optional[str] = None


class Education(BaseModel):
    """One educational qualification."""
    institution: str
    degree: Optional[str] = None
    field: Optional[str] = None
    # Four-digit year, e.g. 2021
    end_year: Optional[int] = None


class ProvenanceEntry(BaseModel):
    """
    Records where a specific field's value came from and how it was extracted.
    This lets downstream consumers audit every populated field.
    """
    field: str    # canonical field name, e.g. "full_name"
    source: str   # e.g. "csv", "resume:resume1.pdf"
    method: str   # e.g. "direct", "regex", "trust-ranking-conflict"


# Top-level canonical record

class CandidateProfile(BaseModel):
    """
    The fully-populated internal canonical record for one candidate.

    Design rule: this object is NEVER reshaped here. The projection layer
    (project.py) is solely responsible for filtering / renaming fields for
    external output. This keeps the canonical engine stable regardless of
    what any downstream consumer asks for.
    """

    # Stable identifier derived deterministically from the match key
    # (lowercased primary email, or E.164 phone when email is absent).
    candidate_id: str

    full_name: str

    # All known emails, deduplicated and lowercased
    emails: List[str] = Field(default_factory=list)

    # All known phones in E.164 format, deduplicated
    phones: List[str] = Field(default_factory=list)

    location: Location = Field(default_factory=Location)
    links: Links = Field(default_factory=Links)

    # One-line professional summary / current role blurb
    headline: Optional[str] = None

    # Total years of work experience; derived by summing experience durations
    # or parsed from resume prose — marked 0.3 confidence when derived
    years_experience: Optional[float] = None

    skills: List[Skill] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)

    # One entry per field that was actually populated; conflicts are logged here
    provenance: List[ProvenanceEntry] = Field(default_factory=list)

    # Average confidence across all populated fields (0.0–1.0)
    overall_confidence: float = 0.0
