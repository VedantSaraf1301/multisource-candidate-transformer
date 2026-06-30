"""
validate.py — Post-projection validation of the output dict.

Two validation modes:
  1. Canonical — when no custom fields config is used, check the full schema:
     required fields, correct list types, E.164 phones, YYYY-MM dates, and
     confidence values in [0, 1].
  2. Projection — when a custom fields config is used, check that each declared
     field is present (if required) and matches its declared type.

All errors are raised as ValidationError with a message that names the
candidate, the field, and exactly what was wrong — so the caller can log or
surface a useful error instead of a bare AttributeError.
"""

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """
    Raised when an output dict fails schema or type validation.
    The message always includes the candidate identifier and the
    offending field so errors are actionable.
    """
    pass


# ---------------------------------------------------------------------------
# Regex patterns for format checks
# ---------------------------------------------------------------------------

# E.164: starts with +, then 7–15 digits
_E164_RE = re.compile(r"^\+\d{7,15}$")

# YYYY-MM date
_YYYYMM_RE = re.compile(r"^\d{4}-\d{2}$")


# ---------------------------------------------------------------------------
# Type checker registry (used for custom-projection validation)
# ---------------------------------------------------------------------------

# Maps the "type" string from the projection config → a predicate that
# returns True if the value is the right type.
_TYPE_CHECKERS: Dict[str, Any] = {
    "string":   lambda v: isinstance(v, str),
    "string[]": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v),
    "number":   lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool":     lambda v: isinstance(v, bool),
}


# ---------------------------------------------------------------------------
# Small helper
# ---------------------------------------------------------------------------

def _err(candidate_id: str, field: str, msg: str) -> ValidationError:
    """Build a ValidationError with a consistent, descriptive message."""
    return ValidationError(f"[{candidate_id}] field '{field}': {msg}")


# ---------------------------------------------------------------------------
# Canonical schema validation
# ---------------------------------------------------------------------------

def _check_string(val: Any, field: str, cid: str, required: bool = True) -> None:
    """Assert val is a non-empty string when required, or str-or-None otherwise."""
    if required:
        if not isinstance(val, str) or not val.strip():
            raise _err(cid, field, f"expected a non-empty string, got {val!r}")
    else:
        if val is not None and not isinstance(val, str):
            raise _err(cid, field, f"expected str or null, got {type(val).__name__}")


def _check_list(val: Any, field: str, cid: str) -> None:
    if not isinstance(val, list):
        raise _err(cid, field, f"expected a list, got {type(val).__name__}")


def _check_confidence(val: Any, field: str, cid: str) -> None:
    """Confidence values must be a float in [0, 1]."""
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        raise _err(cid, field, f"expected a number, got {type(val).__name__}")
    if not (0.0 <= val <= 1.0):
        raise _err(cid, field, f"confidence must be in [0, 1], got {val}")


def _validate_canonical(out: dict) -> None:
    """
    Validate the full canonical output dict (produced when no fields config
    is active). Checks required fields, types, format patterns, and value
    ranges. Does NOT re-validate fields that may have been stripped by
    include_confidence=False or include_provenance=False.
    """
    # Use candidate_id as the identifier in error messages; fall back to "?"
    cid = out.get("candidate_id") or "?"

    # --- Required top-level strings ---
    _check_string(out.get("candidate_id"), "candidate_id", cid, required=True)
    _check_string(out.get("full_name"),    "full_name",    cid, required=True)

    # --- emails ---
    emails = out.get("emails", [])
    _check_list(emails, "emails", cid)
    for i, e in enumerate(emails):
        if not isinstance(e, str) or "@" not in e:
            raise _err(cid, f"emails[{i}]", f"expected a valid email string, got {e!r}")

    # --- phones: must all be E.164 ---
    phones = out.get("phones", [])
    _check_list(phones, "phones", cid)
    for i, p in enumerate(phones):
        if not isinstance(p, str) or not _E164_RE.match(p):
            raise _err(cid, f"phones[{i}]", f"expected E.164 format (e.g. +919876543210), got {p!r}")

    # --- location ---
    loc = out.get("location")
    if loc is not None:
        if not isinstance(loc, dict):
            raise _err(cid, "location", f"expected a dict, got {type(loc).__name__}")
        for sub in ("city", "region", "country"):
            v = loc.get(sub)
            if v is not None and not isinstance(v, str):
                raise _err(cid, f"location.{sub}", f"expected str or null, got {type(v).__name__}")

    # --- links ---
    links = out.get("links")
    if links is not None:
        if not isinstance(links, dict):
            raise _err(cid, "links", f"expected a dict, got {type(links).__name__}")
        for sub in ("linkedin", "github", "portfolio"):
            v = links.get(sub)
            if v is not None and not isinstance(v, str):
                raise _err(cid, f"links.{sub}", f"expected str or null, got {type(v).__name__}")
        other = links.get("other", [])
        _check_list(other, "links.other", cid)

    # --- headline ---
    _check_string(out.get("headline"), "headline", cid, required=False)

    # --- years_experience ---
    ye = out.get("years_experience")
    if ye is not None:
        if not isinstance(ye, (int, float)) or isinstance(ye, bool) or ye < 0:
            raise _err(cid, "years_experience", f"expected a non-negative number or null, got {ye!r}")

    # --- skills ---
    skills = out.get("skills", [])
    _check_list(skills, "skills", cid)
    for i, s in enumerate(skills):
        if not isinstance(s, dict):
            raise _err(cid, f"skills[{i}]", "expected a dict")
        _check_string(s.get("name"), f"skills[{i}].name", cid, required=True)
        # confidence may be absent when include_confidence=False stripped it
        if "confidence" in s:
            _check_confidence(s["confidence"], f"skills[{i}].confidence", cid)
        _check_list(s.get("sources", []), f"skills[{i}].sources", cid)

    # --- experience ---
    experience = out.get("experience", [])
    _check_list(experience, "experience", cid)
    for i, exp in enumerate(experience):
        if not isinstance(exp, dict):
            raise _err(cid, f"experience[{i}]", "expected a dict")
        _check_string(exp.get("company"), f"experience[{i}].company", cid, required=True)
        _check_string(exp.get("title"),   f"experience[{i}].title",   cid, required=True)
        for date_field in ("start", "end"):
            dv = exp.get(date_field)
            if dv is not None:
                if not isinstance(dv, str) or not _YYYYMM_RE.match(dv):
                    raise _err(
                        cid, f"experience[{i}].{date_field}",
                        f"expected YYYY-MM format or null, got {dv!r}",
                    )

    # --- education ---
    education = out.get("education", [])
    _check_list(education, "education", cid)
    for i, edu in enumerate(education):
        if not isinstance(edu, dict):
            raise _err(cid, f"education[{i}]", "expected a dict")
        _check_string(edu.get("institution"), f"education[{i}].institution", cid, required=True)
        ey = edu.get("end_year")
        if ey is not None and (not isinstance(ey, int) or ey < 1900 or ey > 2100):
            raise _err(cid, f"education[{i}].end_year", f"expected a 4-digit year or null, got {ey!r}")

    # --- provenance (optional — may have been stripped) ---
    provenance = out.get("provenance")
    if provenance is not None:
        _check_list(provenance, "provenance", cid)
        for i, prov in enumerate(provenance):
            if not isinstance(prov, dict):
                raise _err(cid, f"provenance[{i}]", "expected a dict")
            for key in ("field", "source", "method"):
                _check_string(prov.get(key), f"provenance[{i}].{key}", cid, required=True)

    # --- overall_confidence (optional — may have been stripped) ---
    oc = out.get("overall_confidence")
    if oc is not None:
        _check_confidence(oc, "overall_confidence", cid)


# ---------------------------------------------------------------------------
# Custom-projection validation
# ---------------------------------------------------------------------------

def _validate_projection(out: dict, fields_config: List[dict]) -> None:
    """
    Validate a custom-projection output dict by checking each declared field:
    - required=True fields must be non-null and non-empty.
    - declared types must match the actual value type.
    """
    cid = out.get("candidate_id") or str(out.get("full_name") or "?")

    for field_cfg in fields_config:
        output_key = field_cfg["path"]
        declared_type = field_cfg.get("type")
        required = field_cfg.get("required", False)

        val = out.get(output_key)

        # Required presence check
        if required and (val is None or val == "" or val == []):
            raise _err(cid, output_key, "field is required but missing or null")

        # Skip type check when value is null (absence is allowed)
        if val is None:
            continue

        # Type check
        if declared_type and declared_type in _TYPE_CHECKERS:
            if not _TYPE_CHECKERS[declared_type](val):
                raise _err(
                    cid, output_key,
                    f"declared type is '{declared_type}' but got {type(val).__name__} = {val!r}",
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(output_dict: dict, config: dict) -> None:
    """
    Validate a projected output dict.

    Selects canonical or projection validation mode based on whether the
    config has a 'fields' list. Raises ValidationError (a subclass of
    Exception) on any failure — callers should catch and log/re-raise.

    Args:
        output_dict: The plain dict produced by project.project().
        config:      The normalised config dict from project.load_config().

    Raises:
        ValidationError: with a descriptive message identifying the
                         candidate, field, and problem.
    """
    if config.get("fields") is None:
        _validate_canonical(output_dict)
    else:
        _validate_projection(output_dict, config["fields"])
