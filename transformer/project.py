import re
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from transformer.models import CandidateProfile
from transformer.normalize import normalize_phone, canonicalize_skill

logger = logging.getLogger(__name__)


def load_config(config: Union[str, Path, dict, None]) -> dict:
    """Accept a config as a file path, JSON string, dict, or None. Returns a normalised dict."""
    if config is None:
        raw = {}
    elif isinstance(config, dict):
        raw = config
    else:
        path = Path(config)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = json.loads(config)

    return {
        "fields":             raw.get("fields"),
        "include_confidence": raw.get("include_confidence", True),
        "include_provenance": raw.get("include_provenance", True),
        "on_missing":         raw.get("on_missing", "null"),
    }


def _get_attr(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _resolve_path(record: CandidateProfile, path: str) -> Any:
    # "skills[].name" - extract attribute from every element in a list
    array_map = re.match(r"^(.+?)\[\]\.(.*)", path)
    if array_map:
        list_val = _resolve_simple(record, array_map.group(1))
        if not isinstance(list_val, list):
            return None
        return [
            _get_attr(item, array_map.group(2))
            for item in list_val
            if _get_attr(item, array_map.group(2)) is not None
        ]

    # "emails[0]" - index into a list
    index_match = re.match(r"^(.+?)\[(\d+)\]$", path)
    if index_match:
        list_val = _resolve_simple(record, index_match.group(1))
        index    = int(index_match.group(2))
        if isinstance(list_val, list) and index < len(list_val):
            return list_val[index]
        return None

    return _resolve_simple(record, path)


def _resolve_simple(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        current = _get_attr(current, part)
    return current


_NORMALIZERS = {
    "E164":      lambda val: normalize_phone(str(val)) if val else None,
    "canonical": lambda val: (
        [canonicalize_skill(v) for v in val] if isinstance(val, list)
        else canonicalize_skill(str(val)) if val else None
    ),
}


def _apply_normalizer(value: Any, normalizer_name: str) -> Any:
    fn = _NORMALIZERS.get(normalizer_name)
    if fn is None:
        logger.warning("Unknown normalizer %r - value passed through unchanged.", normalizer_name)
        return value
    return fn(value)


def _serialize_full(profile: CandidateProfile, cfg: dict) -> dict:
    out = profile.model_dump()

    if not cfg["include_confidence"]:
        out.pop("overall_confidence", None)
        for skill in out.get("skills", []):
            skill.pop("confidence", None)

    if not cfg["include_provenance"]:
        out.pop("provenance", None)

    return out


def _project_fields(profile: CandidateProfile, cfg: dict) -> dict:
    on_missing = cfg["on_missing"]
    out: Dict[str, Any] = {}

    for field_cfg in cfg["fields"]:
        output_key  = field_cfg["path"]
        source_path = field_cfg.get("from", output_key)
        required    = field_cfg.get("required", False)
        normalizer  = field_cfg.get("normalize")

        value = _resolve_path(profile, source_path)

        if normalizer and value is not None:
            value = _apply_normalizer(value, normalizer)

        if value is None or value == [] or value == "":
            if required or on_missing == "error":
                raise ValueError(
                    f"Required field '{output_key}' (from '{source_path}') "
                    f"is missing for candidate '{profile.full_name}' ({profile.candidate_id})."
                )
            if on_missing == "omit":
                continue
            out[output_key] = None
        else:
            out[output_key] = value

    if cfg["include_confidence"]:
        out["overall_confidence"] = profile.overall_confidence

    if cfg["include_provenance"]:
        out["provenance"] = [p.model_dump() for p in profile.provenance]

    return out


def project(
    profile: CandidateProfile,
    config: Union[str, Path, dict, None] = None,
) -> dict:
    cfg = load_config(config)

    if cfg["fields"] is None:
        return _serialize_full(profile, cfg)
    else:
        return _project_fields(profile, cfg)
