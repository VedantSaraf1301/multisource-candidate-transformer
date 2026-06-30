"""
normalize.py — Pure functions that convert raw strings into canonical forms.

All functions are stateless and deterministic: same input always → same output.
No side effects, no logging here — callers decide what to do with None returns.

Normalization assumptions (documented per spec):
  - Phone default region: IN (India). Any number without a country code is
    assumed to be Indian. Document this prominently because it affects correctness
    for non-Indian candidates.
  - Dates with only a year (e.g. "2021") are stored as "2021-01" (January assumed).
    This is an approximation; experience entries typically have month+year.
  - Country names are mapped to ISO-3166 alpha-2 via a small hand-curated table.
    Countries not in the table fall back to storing the raw name string.
"""

import re
import logging
from typing import Optional

import phonenumbers
from phonenumbers import PhoneNumberFormat, NumberParseException

logger = logging.getLogger(__name__)

# Phone normalization

# Default region used when no country code is present in the raw phone string.
# Change this constant to support a different default region.
DEFAULT_PHONE_REGION = "IN"


def normalize_phone(raw: str) -> Optional[str]:
    """
    Convert a raw phone string to E.164 format using the `phonenumbers` library.

    If the number has no country code, DEFAULT_PHONE_REGION ("IN") is assumed.
    Returns None if the string cannot be parsed into a valid phone number,
    so the pipeline can treat it as missing rather than storing garbage.

    Examples:
        "+919876543210"  → "+919876543210"
        "9876543210"     → "+919876543210"   (IN assumed)
        "9876543210"     → "+919876543210"
        "00-1800-CALL-NOW" → None            (invalid)
    """
    if not raw or not raw.strip():
        return None

    cleaned = raw.strip()

    # Reject strings that contain letters — vanity numbers like "1-800-CALL-NOW"
    # are technically valid but we treat them as malformed input because recruiters
    # don't enter mnemonic numbers; letters in this field always signal bad data.
    if re.search(r"[A-Za-z]", cleaned):
        return None

    try:
        parsed = phonenumbers.parse(cleaned, DEFAULT_PHONE_REGION)
    except NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


# Date normalization

# Month abbreviation / full-name → zero-padded month number
_MONTH_MAP = {
    "jan": "01", "january":   "01",
    "feb": "02", "february":  "02",
    "mar": "03", "march":     "03",
    "apr": "04", "april":     "04",
    "may": "05",
    "jun": "06", "june":      "06",
    "jul": "07", "july":      "07",
    "aug": "08", "august":    "08",
    "sep": "09", "september": "09",
    "oct": "10", "october":   "10",
    "nov": "11", "november":  "11",
    "dec": "12", "december":  "12",
}

# Words that mean "still ongoing" — normalize to None (no end date)
_PRESENT_WORDS = {"present", "current", "now", "ongoing", "till date", "to date"}


def normalize_date(raw: str) -> Optional[str]:
    """
    Convert a raw date string to YYYY-MM format.

    Handles:
        "Jan 2021"       → "2021-01"
        "January 2021"   → "2021-01"
        "2021-01"        → "2021-01"
        "2021/01"        → "2021-01"
        "2021"           → "2021-01"   (January assumed — documented assumption)
        "Present"        → None        (ongoing role, no end date)
        ""               → None

    Returns None for anything unparseable so callers never store a fabricated date.
    """
    if not raw or not raw.strip():
        return None

    raw = raw.strip()

    # "Present / Current / Now" means the role is ongoing — no end date
    if raw.lower() in _PRESENT_WORDS:
        return None

    # Already in YYYY-MM or YYYY/MM form
    m = re.match(r"^(\d{4})[-/](\d{2})$", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # "Jan 2021" or "January 2021"  (month name before year)
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{4})$", raw)
    if m:
        month_str = m.group(1).lower().rstrip(".")
        year      = m.group(2)
        month_num = _MONTH_MAP.get(month_str)
        if month_num:
            return f"{year}-{month_num}"

    # Just a four-digit year — assume January (documented assumption)
    m = re.match(r"^(\d{4})$", raw)
    if m:
        return f"{m.group(1)}-01"

    # Nothing matched
    return None


# Skill canonicalization

# Canonical alias map: raw lowercase variant → canonical lowercase name.
# Keep all aliases here — this is the single place to extend the mapping.
SKILL_ALIASES: dict[str, str] = {
    # JavaScript ecosystem
    "js":               "javascript",
    "reactjs":          "react",
    "react.js":         "react",
    "vuejs":            "vue",
    "vue.js":           "vue",
    "nodejs":           "node",
    "node.js":          "node",
    "expressjs":        "express",
    "express.js":       "express",
    "nextjs":           "next.js",
    "ts":               "typescript",

    # Python ecosystem
    "py":               "python",
    "sklearn":          "scikit-learn",

    # Data / ML
    "ml":               "machine learning",
    "dl":               "deep learning",
    "nlp":              "natural language processing",
    "ai":               "artificial intelligence",
    "tf":               "tensorflow",

    # Databases
    "pg":               "postgres",
    "postgresql":       "postgres",
    "mongo":            "mongodb",
    "mssql":            "sql server",

    # DevOps / cloud
    "k8s":              "kubernetes",
    "gcp":              "google cloud",
    "az":               "azure",

    # Languages
    "golang":           "go",
    "cpp":              "c++",    # canonical form is "c++"; "cpp" aliases to it
    "c#":               "csharp",

    # APIs / protocols
    "rest apis":        "rest",
    "rest api":         "rest",
    "restful":          "rest",
    "restful apis":     "rest",
    "graphql api":      "graphql",
}


def canonicalize_skill(raw: str) -> str:
    """
    Lowercase, strip whitespace, then look up in SKILL_ALIASES.
    Returns the canonical name if a mapping exists, otherwise the cleaned raw value.

    Always returns a string (never None) — callers decide whether to keep or drop it.
    """
    cleaned = raw.strip().lower()
    return SKILL_ALIASES.get(cleaned, cleaned)


# Name normalization

def normalize_name(raw: str) -> str:
    """
    Strip leading/trailing whitespace and apply title-case.
    Returns empty string if input is empty — callers treat that as missing.

    Example:  "  priya SHARMA  " → "Priya Sharma"
    """
    if not raw:
        return ""
    return raw.strip().title()


# Location parsing

# Mapping of common country name variants (lowercase) → ISO-3166 alpha-2 code.
# Extend this table to support more countries without changing any other code.
_COUNTRY_CODES: dict[str, str] = {
    "india":          "IN",
    "united states":  "US",
    "usa":            "US",
    "us":             "US",
    "united kingdom": "GB",
    "uk":             "GB",
    "canada":         "CA",
    "australia":      "AU",
    "germany":        "DE",
    "france":         "FR",
    "singapore":      "SG",
    "japan":          "JP",
    "china":          "CN",
    "brazil":         "BR",
    "netherlands":    "NL",
    "uae":            "AE",
    "united arab emirates": "AE",
}


def parse_location(raw: str) -> dict:
    """
    Parse a raw location string into {city, region, country} components.

    Strategy: split on commas. The most common formats are:
        "City"                         → city only
        "City, Country"                → city + country
        "City, Region, Country"        → all three
        "City, Region"                 → city + region (country unknown)

    Country is converted to ISO-3166 alpha-2 if recognised; otherwise stored
    as the raw string. Unknown parts are None.

    Returns a dict (not a Location model) so this function stays dependency-free
    — callers (merge.py) convert to the model.
    """
    if not raw or not raw.strip():
        return {"city": None, "region": None, "country": None}

    parts = [p.strip().title() for p in raw.split(",") if p.strip()]

    city    = None
    region  = None
    country = None

    if len(parts) == 1:
        city = parts[0]
    elif len(parts) == 2:
        city = parts[0]
        # Second part could be country or region — check the country table
        code = _COUNTRY_CODES.get(parts[1].lower())
        if code:
            country = code
        else:
            region = parts[1]
    elif len(parts) >= 3:
        city   = parts[0]
        region = parts[1]
        code   = _COUNTRY_CODES.get(parts[2].lower())
        country = code if code else parts[2]

    return {"city": city, "region": region, "country": country}


# Email normalization

def normalize_email(raw: str) -> Optional[str]:
    """
    Lowercase and strip whitespace. Returns None if the result is empty
    or doesn't look like an email (no '@').
    """
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if "@" not in cleaned:
        return None
    return cleaned
