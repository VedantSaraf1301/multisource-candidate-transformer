import re
from typing import Any, Dict, List, Optional


class ValidationError(Exception):
    pass


_E164_RE   = re.compile(r"^\+\d{7,15}$")
_YYYYMM_RE = re.compile(r"^\d{4}-\d{2}$")

_TYPE_CHECKERS: Dict[str, Any] = {
    "string":   lambda v: isinstance(v, str),
    "string[]": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v),
    "number":   lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool":     lambda v: isinstance(v, bool),
}


def _err(candidate_id: str, field: str, msg: str) -> ValidationError:
    return ValidationError(f"[{candidate_id}] field '{field}': {msg}")


def _check_string(val: Any, field: str, cid: str, required: bool = True) -> None:
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
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        raise _err(cid, field, f"expected a number, got {type(val).__name__}")
    if not (0.0 <= val <= 1.0):
        raise _err(cid, field, f"confidence must be in [0, 1], got {val}")


def _validate_canonical(out: dict) -> None:
    cid = out.get("candidate_id") or "?"

    _check_string(out.get("candidate_id"), "candidate_id", cid, required=True)
    _check_string(out.get("full_name"),    "full_name",    cid, required=True)

    emails = out.get("emails", [])
    _check_list(emails, "emails", cid)
    for i, e in enumerate(emails):
        if not isinstance(e, str) or "@" not in e:
            raise _err(cid, f"emails[{i}]", f"expected a valid email string, got {e!r}")

    phones = out.get("phones", [])
    _check_list(phones, "phones", cid)
    for i, p in enumerate(phones):
        if not isinstance(p, str) or not _E164_RE.match(p):
            raise _err(cid, f"phones[{i}]", f"expected E.164 format (e.g. +919876543210), got {p!r}")

    loc = out.get("location")
    if loc is not None:
        if not isinstance(loc, dict):
            raise _err(cid, "location", f"expected a dict, got {type(loc).__name__}")
        for sub in ("city", "region", "country"):
            v = loc.get(sub)
            if v is not None and not isinstance(v, str):
                raise _err(cid, f"location.{sub}", f"expected str or null, got {type(v).__name__}")

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

    _check_string(out.get("headline"), "headline", cid, required=False)

    ye = out.get("years_experience")
    if ye is not None:
        if not isinstance(ye, (int, float)) or isinstance(ye, bool) or ye < 0:
            raise _err(cid, "years_experience", f"expected a non-negative number or null, got {ye!r}")

    skills = out.get("skills", [])
    _check_list(skills, "skills", cid)
    for i, s in enumerate(skills):
        if not isinstance(s, dict):
            raise _err(cid, f"skills[{i}]", "expected a dict")
        _check_string(s.get("name"), f"skills[{i}].name", cid, required=True)
        if "confidence" in s:
            _check_confidence(s["confidence"], f"skills[{i}].confidence", cid)
        _check_list(s.get("sources", []), f"skills[{i}].sources", cid)

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

    education = out.get("education", [])
    _check_list(education, "education", cid)
    for i, edu in enumerate(education):
        if not isinstance(edu, dict):
            raise _err(cid, f"education[{i}]", "expected a dict")
        _check_string(edu.get("institution"), f"education[{i}].institution", cid, required=True)
        ey = edu.get("end_year")
        if ey is not None and (not isinstance(ey, int) or ey < 1900 or ey > 2100):
            raise _err(cid, f"education[{i}].end_year", f"expected a 4-digit year or null, got {ey!r}")

    provenance = out.get("provenance")
    if provenance is not None:
        _check_list(provenance, "provenance", cid)
        for i, prov in enumerate(provenance):
            if not isinstance(prov, dict):
                raise _err(cid, f"provenance[{i}]", "expected a dict")
            for key in ("field", "source", "method"):
                _check_string(prov.get(key), f"provenance[{i}].{key}", cid, required=True)

    oc = out.get("overall_confidence")
    if oc is not None:
        _check_confidence(oc, "overall_confidence", cid)


def _validate_projection(out: dict, fields_config: List[dict]) -> None:
    cid = out.get("candidate_id") or str(out.get("full_name") or "?")

    for field_cfg in fields_config:
        output_key    = field_cfg["path"]
        declared_type = field_cfg.get("type")
        required      = field_cfg.get("required", False)

        val = out.get(output_key)

        if required and (val is None or val == "" or val == []):
            raise _err(cid, output_key, "field is required but missing or null")

        if val is None:
            continue

        if declared_type and declared_type in _TYPE_CHECKERS:
            if not _TYPE_CHECKERS[declared_type](val):
                raise _err(
                    cid, output_key,
                    f"declared type is '{declared_type}' but got {type(val).__name__} = {val!r}",
                )


def validate(output_dict: dict, config: dict) -> None:
    if config.get("fields") is None:
        _validate_canonical(output_dict)
    else:
        _validate_projection(output_dict, config["fields"])
