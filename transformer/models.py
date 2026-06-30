from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class Location(BaseModel):
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None  # ISO-3166 alpha-2, e.g. "IN"


class Links(BaseModel):
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: List[str] = Field(default_factory=list)


class Skill(BaseModel):
    name: str
    confidence: float  # 0.0-1.0; see merge.py for scoring rules
    sources: List[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    title: str
    start: Optional[str] = None   # YYYY-MM
    end: Optional[str] = None     # YYYY-MM; None = ongoing
    summary: Optional[str] = None


class Education(BaseModel):
    institution: str
    degree: Optional[str] = None
    field: Optional[str] = None
    end_year: Optional[int] = None


class ProvenanceEntry(BaseModel):
    field: str
    source: str
    method: str


class CandidateProfile(BaseModel):
    candidate_id: str
    full_name: str
    emails: List[str] = Field(default_factory=list)
    phones: List[str] = Field(default_factory=list)
    location: Location = Field(default_factory=Location)
    links: Links = Field(default_factory=Links)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: List[Skill] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    provenance: List[ProvenanceEntry] = Field(default_factory=list)
    overall_confidence: float = 0.0
