"""
project.py — Reshape a CandidateProfile into the output format requested by
a runtime JSON config, WITHOUT touching the canonical record itself.

Architecture contract
---------------------
The CandidateProfile passed in is NEVER modified here. Projection is a pure
read-and-reshape operation: it produces a new plain dict. This keeps the
canonical engine stable regardless of what any downstream consumer asks for.

Config schema
-------------
{
  "fields": [                          // optional; omit to emit the full schema
    {
      "path":      "output_key",       // key name in the output dict
      "from":      "source.path",      // optional; path in the canonical record
                                       // (defaults to same as "path")
      "type":      "string|string[]|number|bool",  // informational; used in validation
      "required":  true|false,         // if true, on_missing="error" overrides on_missing
      "normalize": "E164|canonical"    // optional post-extraction normalizer
    }
  ],
  "include_confidence": true|false,    // include overall_confidence in output
  "include_provenance": true|false,    // include provenance array in output
  "on_missing": "null|omit|error"      // what to do when a field resolves to None
}

Path syntax for "from"
----------------------
  "full_name"        direct field on CandidateProfile
  "location.city"    nested attribute (works on Pydantic sub-models and dicts)
  "emails[0]"        first element of a list
  "skills[].name"    extract attribute from every element → returns a list
"""

import re
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from transformer.models import CandidateProfile
from transformer.normalize import normalize_phone, canonicalize_skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config: Union[str, Path, dict, None]) -> dict:
    """
    Accept a config as a file path, a raw JSON string, a dict, or None.
    Returns a normalised config dict.  Missing keys are filled with defaults
    so the rest of the code never has to handle absent keys.
    """
    if config is None:
        raw = {}
    elif isinstance(config, dict):
        raw = config
    else:
        path = Path(config)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            # Treat as a raw JSON string
            raw = json.loads(config)

    # Fill defaults
    return {
        "fields":              raw.get("fields"),          # None = emit full schema
        "include_confidence":  raw.get("include_confidence", True),
        "include_provenance":  raw.get("include_provenance", True),
        "on_missing":          raw.get("on_missing", "null"),
    }


# ---------------------------------------------------------------------------
# Path resolver
# ---------------------------------------------------------------------------

def _get_attr(obj: Any, key: str) -> Any:
    """Get a key from either a dict or an object (Pydantic model)."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _resolve_path(record: CandidateProfile, path: str) -> Any:
    """
    Walk a path string against the canonical record and return the value.

    Supported patterns:
      "full_name"         → record.full_name
      "location.city"     → record.location.city
      "emails[0]"         → record.emails[0]  (None if list is shorter)
      "skills[].name"     → [s.name for s in record.skills]
      "links.linkedin"    → record.links.linkedin
    """
    # --- Array-map pattern: "skills[].name" ---
    # "[]. " means "for every element in the list, extract the following attribute"
    array_map = re.match(r"^(.+?)\[\]\.(.*)", path)
    if array_map:
        list_path = array_map.group(1)   # e.g. "skills"
        item_attr = array_map.group(2)   # e.g. "name"
        list_val  = _resolve_simple(record, list_path)
        if not isinstance(list_val, list):
            return None
        return [
            _get_attr(item, item_attr)
            for item in list_val
            if _get_attr(item, item_attr) is not None
        ]

    # --- Index pattern: "emails[0]" ---
    index_match = re.match(r"^(.+?)\[(\d+)\]$", path)
    if index_match:
        list_path = index_match.group(1)   # e.g. "emails"
        index     = int(index_match.group(2))
        list_val  = _resolve_simple(record, list_path)
        if isinstance(list_val, list) and index < len(list_val):
            return list_val[index]
        return None

    # --- Simple dotted path: "location.city" ---
    return _resolve_simple(record, path)


def _resolve_simple(obj: Any, path: str) -> Any:
    """Navigate a dot-separated path through nested objects/dicts."""
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        current = _get_attr(current, part)
    return current


# ---------------------------------------------------------------------------
# Normalizer registry
# ---------------------------------------------------------------------------

# Maps normalizer name (from config) → callable that transforms a value.
# Add new normalizers here to extend the system without changing any other code.
_NORMALIZERS = {
    # Re-validate / re-format a phone number to E.164
    "E164": lambda val: normalize_phone(str(val)) if val else None,

    # Apply skill canonicalization; works on both a single string and a list
    "canonical": lambda val: (
        [canonicalize_skill(v) for v in val] if isinstance(val, list)
        else canonicalize_skill(str(val)) if val else None
    ),
}


def _apply_normalizer(value: Any, normalizer_name: str) -> Any:
    """Run the named normalizer, or return value unchanged if name is unknown."""
    fn = _NORMALIZERS.get(normalizer_name)
    if fn is None:
        logger.warning("Unknown normalizer %r — value passed through unchanged.", normalizer_name)
        return value
    return fn(value)


# ---------------------------------------------------------------------------
# Full-schema (no fields config) serializer
# ---------------------------------------------------------------------------

def _serialize_full(profile: CandidateProfile, cfg: dict) -> dict:
    """
    Serialize the complete canonical record to a plain dict.
    Respects include_confidence and include_provenance toggles.
    """
    # model_dump() converts the Pydantic model to a nested dict
    out = profile.model_dump()

    if not cfg["include_confidence"]:
        out.pop("overall_confidence", None)
        # Also strip per-skill confidence
        for skill in out.get("skills", []):
            skill.pop("confidence", None)

    if not cfg["include_provenance"]:
        out.pop("provenance", None)

    return out


# ---------------------------------------------------------------------------
# Custom-fields projection
# ---------------------------------------------------------------------------

def _project_fields(profile: CandidateProfile, cfg: dict) -> dict:
    """
    Build an output dict containing only the fields listed in cfg["fields"].
    Each field entry drives: path resolution → normalization → missing handling.
    """
    on_missing = cfg["on_missing"]   # "null" | "omit" | "error"
    out: Dict[str, Any] = {}

    for field_cfg in cfg["fields"]:
        output_key   = field_cfg["path"]
        source_path  = field_cfg.get("from", output_key)  # default: same as path
        required     = field_cfg.get("required", False)
        normalizer   = field_cfg.get("normalize")

        # 1. Resolve value from the canonical record
        value = _resolve_path(profile, source_path)

        # 2. Apply normalizer if requested
        if normalizer and value is not None:
            value = _apply_normalizer(value, normalizer)

        # 3. Handle missing / None values
        if value is None or value == [] or value == "":
            if required or on_missing == "error":
                raise ValueError(
                    f"Required field '{output_key}' (from '{source_path}') "
                    f"is missing for candidate '{profile.full_name}' ({profile.candidate_id})."
                )
            if on_missing == "omit":
                continue   # don't include this key in the output at all
            # on_missing == "null": include key with null value
            out[output_key] = None
        else:
            out[output_key] = value

    # Always include overall_confidence when requested (appended at the end)
    if cfg["include_confidence"]:
        out["overall_confidence"] = profile.overall_confidence

    if cfg["include_provenance"]:
        out["provenance"] = [p.model_dump() for p in profile.provenance]

    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def project(
    profile: CandidateProfile,
    config: Union[str, Path, dict, None] = None,
) -> dict:
    """
    Apply a runtime config to a CandidateProfile and return a plain dict
    ready for JSON serialization.

    Args:
        profile: The fully-populated canonical record (never modified).
        config:  A config dict, a path to a JSON file, a raw JSON string,
                 or None (→ emit the full canonical schema with all fields).

    Returns:
        A plain Python dict suitable for json.dumps().

    Raises:
        ValueError: when on_missing="error" and a required field is absent.
        json.JSONDecodeError: when config is a string that isn't valid JSON
                              and doesn't resolve to a file path.
    """
    cfg = load_config(config)

    if cfg["fields"] is None:
        # No fields list → full canonical output
        return _serialize_full(profile, cfg)
    else:
        # Custom fields list → reshape
        return _project_fields(profile, cfg)
