import re
import logging
from typing import Optional

import phonenumbers
from phonenumbers import PhoneNumberFormat, NumberParseException

logger = logging.getLogger(__name__)

# Change this to support a different default region for numberless phone inputs.
DEFAULT_PHONE_REGION = "IN"


def normalize_phone(raw: str) -> Optional[str]:
    """Returns E.164 or None. Rejects strings containing letters."""
    if not raw or not raw.strip():
        return None

    cleaned = raw.strip()

    # Recruiters don't enter mnemonic numbers; letters in this field always signal bad data.
    if re.search(r"[A-Za-z]", cleaned):
        return None

    try:
        parsed = phonenumbers.parse(cleaned, DEFAULT_PHONE_REGION)
    except NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


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

_PRESENT_WORDS = {"present", "current", "now", "ongoing", "till date", "to date"}


def normalize_date(raw: str) -> Optional[str]:
    """Returns YYYY-MM or None. Bare years become YYYY-01 (January assumed)."""
    if not raw or not raw.strip():
        return None

    raw = raw.strip()

    if raw.lower() in _PRESENT_WORDS:
        return None

    m = re.match(r"^(\d{4})[-/](\d{2})$", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{4})$", raw)
    if m:
        month_str = m.group(1).lower().rstrip(".")
        year      = m.group(2)
        month_num = _MONTH_MAP.get(month_str)
        if month_num:
            return f"{year}-{month_num}"

    m = re.match(r"^(\d{4})$", raw)
    if m:
        return f"{m.group(1)}-01"

    return None


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
    "cpp":              "c++",    # canonical form is "c++"; "cpp" is the alias
    "c#":               "csharp",

    # APIs / protocols
    "rest apis":        "rest",
    "rest api":         "rest",
    "restful":          "rest",
    "restful apis":     "rest",
    "graphql api":      "graphql",
}


def canonicalize_skill(raw: str) -> str:
    cleaned = raw.strip().lower()
    return SKILL_ALIASES.get(cleaned, cleaned)


def normalize_name(raw: str) -> str:
    if not raw:
        return ""
    return raw.strip().title()


_COUNTRY_CODES: dict[str, str] = {
    "india":                "IN",
    "united states":        "US",
    "usa":                  "US",
    "us":                   "US",
    "united kingdom":       "GB",
    "uk":                   "GB",
    "canada":               "CA",
    "australia":            "AU",
    "germany":              "DE",
    "france":               "FR",
    "singapore":            "SG",
    "japan":                "JP",
    "china":                "CN",
    "brazil":               "BR",
    "netherlands":          "NL",
    "uae":                  "AE",
    "united arab emirates": "AE",
}


def parse_location(raw: str) -> dict:
    """Splits 'City, Region, Country' into components. Country normalized to ISO-3166 alpha-2."""
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
        code = _COUNTRY_CODES.get(parts[1].lower())
        if code:
            country = code
        else:
            region = parts[1]
    elif len(parts) >= 3:
        city    = parts[0]
        region  = parts[1]
        code    = _COUNTRY_CODES.get(parts[2].lower())
        country = code if code else parts[2]

    return {"city": city, "region": region, "country": country}


def normalize_email(raw: str) -> Optional[str]:
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if "@" not in cleaned:
        return None
    return cleaned
