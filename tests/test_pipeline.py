"""
test_pipeline.py — pytest suite for the candidate data transformer.

Covers (per spec):
  - Happy path: full pipeline produces the expected number of candidates
    with correct merged data.
  - Duplicate-merge: two identical CSV rows for the same email collapse
    into one profile.
  - Missing-source robustness: corrupt / nonexistent files never crash the
    pipeline; missing sources are skipped gracefully.
  - Custom-config projection: the projection layer reshapes output correctly
    and on_missing behaviour works as specified.

Additional coverage:
  - Phone normalization (no country code → IN assumed, malformed → dropped)
  - Fallback match key (no email → matched by E.164 phone)
  - Skill canonicalization (ReactJS → react, PostgreSQL → postgres)
  - Confidence scoring (conflict → 0.6, single source → 1.0)
  - Validation (bad phone / date in output raises ValidationError)
"""

import pytest
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent
SAMPLE_CSV  = REPO_ROOT / "sample_inputs" / "candidates.csv"
RESUMES_DIR = REPO_ROOT / "sample_inputs" / "resumes"
DEFAULT_CFG = REPO_ROOT / "configs" / "default_config.json"


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def full_results():
    """
    Run the full pipeline once (CSV + resumes + default config) and share the
    result across all tests in this module.  scope="module" means the pipeline
    runs exactly once — not once per test — which keeps the suite fast.
    """
    from transformer.pipeline import run
    return run(csv_path=SAMPLE_CSV, resumes_dir=RESUMES_DIR, config=DEFAULT_CFG)


def _by_name(results, name):
    """Return the first profile whose full_name matches, or None."""
    return next((r for r in results if r["full_name"] == name), None)


# ══════════════════════════════════════════════════════════════════════════════
# 1. HAPPY PATH
# ══════════════════════════════════════════════════════════════════════════════

class TestHappyPath:
    """Full pipeline with both sources produces the expected output."""

    def test_produces_five_unique_candidates(self, full_results):
        # CSV: Priya(×2 duplicate), Arjun, Ravi, Sneha → 4 unique
        # Resume-only: Kiran (no CSV match)
        # Total: 5
        assert len(full_results) == 5

    def test_all_candidates_have_required_fields(self, full_results):
        for r in full_results:
            assert r.get("candidate_id"), f"Missing candidate_id: {r}"
            assert r.get("full_name"),    f"Missing full_name: {r}"

    def test_candidate_ids_are_unique(self, full_results):
        ids = [r["candidate_id"] for r in full_results]
        assert len(ids) == len(set(ids)), "Duplicate candidate_ids found"

    def test_priya_merged_has_resume_skills(self, full_results):
        """Priya appears in both CSV and resume — skills come from resume."""
        priya = _by_name(full_results, "Priya Sharma")
        assert priya is not None
        skill_names = [s["name"] for s in priya["skills"]]
        assert len(skill_names) > 0
        assert "python" in skill_names

    def test_priya_merged_has_two_experience_entries(self, full_results):
        """Resume gives Priya two experience entries; CSV-only candidates have one."""
        priya = _by_name(full_results, "Priya Sharma")
        assert len(priya["experience"]) == 2

    def test_priya_has_education(self, full_results):
        priya = _by_name(full_results, "Priya Sharma")
        assert len(priya["education"]) == 1
        assert "Indian Institute of Technology" in priya["education"][0]["institution"]

    def test_kiran_is_resume_only_candidate(self, full_results):
        """Kiran Rao exists only in a resume — still produces a valid profile."""
        kiran = _by_name(full_results, "Kiran Rao")
        assert kiran is not None
        assert "kiran.rao@email.com" in kiran["emails"]
        assert len(kiran["skills"]) > 0

    def test_provenance_is_populated(self, full_results):
        """Every merged candidate should have at least one provenance entry."""
        for r in full_results:
            assert len(r["provenance"]) > 0, f"No provenance for {r['full_name']}"

    def test_overall_confidence_in_valid_range(self, full_results):
        for r in full_results:
            oc = r["overall_confidence"]
            assert 0.0 <= oc <= 1.0, f"confidence {oc} out of range for {r['full_name']}"


# ══════════════════════════════════════════════════════════════════════════════
# 2. DUPLICATE-MERGE
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicateMerge:
    """CSV rows 1 and 3 are exact duplicates (same email). Only one profile."""

    def test_duplicate_csv_rows_collapse_to_one_profile(self, full_results):
        priya_profiles = [r for r in full_results if r["full_name"] == "Priya Sharma"]
        assert len(priya_profiles) == 1

    def test_merged_profile_retains_resume_data(self, full_results):
        """Skills come from resume even though the CSV duplicate triggered merge."""
        priya = _by_name(full_results, "Priya Sharma")
        skill_names = [s["name"] for s in priya["skills"]]
        # ReactJS → react  (alias canonicalization)
        assert "react" in skill_names
        # PostgreSQL → postgres
        assert "postgres" in skill_names

    def test_headline_conflict_resolved_by_trust(self, full_results):
        """
        CSV title='Software Engineer', resume headline='Software Engineer | Full-Stack Developer'.
        Resume wins for headline (per trust ranking).
        """
        priya = _by_name(full_results, "Priya Sharma")
        assert priya["headline"] == "Software Engineer | Full-Stack Developer"

    def test_headline_conflict_logged_in_provenance(self, full_results):
        """A conflict must leave a trust-ranking-conflict provenance entry."""
        priya = _by_name(full_results, "Priya Sharma")
        conflict_entries = [
            p for p in priya["provenance"]
            if "trust-ranking-conflict" in p["method"]
        ]
        assert len(conflict_entries) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. MISSING-SOURCE ROBUSTNESS
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingSourceRobustness:
    """A bad or missing source must never crash the pipeline."""

    def test_nonexistent_csv_does_not_crash(self):
        from transformer.pipeline import run
        results = run(csv_path="nonexistent_file.csv", resumes_dir=RESUMES_DIR)
        # Only resume candidates: Priya + Kiran
        assert len(results) == 2

    def test_nonexistent_resumes_dir_does_not_crash(self):
        from transformer.pipeline import run
        results = run(csv_path=SAMPLE_CSV, resumes_dir="nonexistent_dir")
        # CSV only: Priya (deduped), Arjun, Ravi, Sneha = 4
        assert len(results) == 4

    def test_both_sources_missing_returns_empty_list(self):
        from transformer.pipeline import run
        results = run(csv_path="ghost.csv", resumes_dir="ghost_dir")
        assert results == []

    def test_no_args_returns_empty_list(self):
        from transformer.pipeline import run
        results = run()
        assert results == []

    def test_corrupt_csv_content_does_not_crash(self, tmp_path):
        """A file with garbage content is handled gracefully."""
        from transformer.pipeline import run
        bad_csv = tmp_path / "bad.csv"
        bad_csv.write_bytes(b"\x00\xff\xfe garbage \x00\x00")
        # Should not raise; returns only resume candidates
        results = run(csv_path=bad_csv, resumes_dir=RESUMES_DIR)
        assert isinstance(results, list)

    def test_empty_csv_does_not_crash(self, tmp_path):
        """A completely empty CSV returns nothing from that source."""
        from transformer.pipeline import run
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("", encoding="utf-8")
        results = run(csv_path=empty_csv, resumes_dir=RESUMES_DIR)
        # Only resume candidates
        assert isinstance(results, list)


# ══════════════════════════════════════════════════════════════════════════════
# 4. CUSTOM-CONFIG PROJECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestCustomConfigProjection:
    """The projection layer reshapes the output per the runtime config."""

    def test_custom_fields_renames_and_filters(self):
        """primary_email remapped from emails[0]; canonical emails key absent."""
        from transformer.pipeline import run
        cfg = {
            "fields": [
                {"path": "full_name",     "type": "string",   "required": True},
                {"path": "primary_email", "from": "emails[0]","type": "string"},
                {"path": "skills",        "from": "skills[].name", "type": "string[]"},
            ],
            "include_confidence": True,
            "include_provenance": False,
            "on_missing": "null",
        }
        results = run(csv_path=SAMPLE_CSV, resumes_dir=RESUMES_DIR, config=cfg)
        priya = _by_name(results, "Priya Sharma")
        assert priya is not None
        assert "primary_email" in priya
        assert priya["primary_email"] == "priya.sharma@email.com"
        assert "emails" not in priya          # original key not in output
        assert "provenance" not in priya      # include_provenance=False
        assert "overall_confidence" in priya  # include_confidence=True

    def test_on_missing_null_includes_key_as_none(self):
        from transformer.pipeline import run
        cfg = {
            "fields": [
                {"path": "full_name", "type": "string", "required": True},
                {"path": "github",    "from": "links.github", "type": "string"},
            ],
            "include_confidence": False,
            "on_missing": "null",
        }
        results = run(csv_path=SAMPLE_CSV, resumes_dir=RESUMES_DIR, config=cfg)
        arjun = _by_name(results, "Arjun Mehta")  # CSV-only, no github
        assert arjun is not None
        assert "github" in arjun          # key present
        assert arjun["github"] is None    # value is null

    def test_on_missing_omit_removes_null_keys(self):
        from transformer.pipeline import run
        cfg = {
            "fields": [
                {"path": "full_name", "type": "string", "required": True},
                {"path": "github",    "from": "links.github", "type": "string"},
            ],
            "include_confidence": False,
            "on_missing": "omit",
        }
        results = run(csv_path=SAMPLE_CSV, resumes_dir=RESUMES_DIR, config=cfg)
        arjun = _by_name(results, "Arjun Mehta")
        assert arjun is not None
        assert "github" not in arjun      # key entirely absent

    def test_on_missing_error_skips_candidate_without_required_field(self):
        """
        Ravi Kumar has no email. If primary_email is required=True and
        on_missing=error, Ravi should be skipped (not crash the whole pipeline).
        """
        from transformer.pipeline import run
        cfg = {
            "fields": [
                {"path": "full_name",     "type": "string", "required": True},
                {"path": "primary_email", "from": "emails[0]", "required": True},
            ],
            "on_missing": "error",
        }
        results = run(csv_path=SAMPLE_CSV, resumes_dir=RESUMES_DIR, config=cfg)
        names = [r["full_name"] for r in results]
        assert "Ravi Kumar" not in names   # skipped — no email
        assert "Priya Sharma" in names     # still present

    def test_array_path_extracts_list_of_values(self):
        """skills[].name extracts the name field from every skill object."""
        from transformer.pipeline import run
        cfg = {
            "fields": [
                {"path": "full_name", "type": "string", "required": True},
                {"path": "skills",    "from": "skills[].name", "type": "string[]"},
            ],
            "include_confidence": False,
            "on_missing": "null",
        }
        results = run(csv_path=SAMPLE_CSV, resumes_dir=RESUMES_DIR, config=cfg)
        priya = _by_name(results, "Priya Sharma")
        assert isinstance(priya["skills"], list)
        assert all(isinstance(s, str) for s in priya["skills"])

    def test_index_path_extracts_first_element(self):
        """emails[0] extracts just the first email string, not a list."""
        from transformer.pipeline import run
        cfg = {
            "fields": [
                {"path": "full_name", "type": "string", "required": True},
                {"path": "email",     "from": "emails[0]", "type": "string"},
            ],
            "include_confidence": False,
            "on_missing": "null",
        }
        results = run(csv_path=SAMPLE_CSV, resumes_dir=RESUMES_DIR, config=cfg)
        priya = _by_name(results, "Priya Sharma")
        assert isinstance(priya["email"], str)   # string, not list


# ══════════════════════════════════════════════════════════════════════════════
# 5. NORMALIZATION
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalization:
    """Field-level normalization correctness."""

    def test_phone_without_country_code_gets_india_prefix(self, full_results):
        """Arjun's 9123456780 → +919123456780 (default region IN)."""
        arjun = _by_name(full_results, "Arjun Mehta")
        assert "+919123456780" in arjun["phones"]

    def test_malformed_phone_is_dropped(self, full_results):
        """Sneha's '00-1800-CALL-NOW' contains letters → rejected → empty phones."""
        sneha = _by_name(full_results, "Sneha Patel")
        assert sneha["phones"] == []

    def test_candidate_with_no_email_matched_by_phone(self, full_results):
        """Ravi Kumar has no email — phone becomes the match key."""
        ravi = _by_name(full_results, "Ravi Kumar")
        assert ravi is not None
        assert ravi["emails"] == []
        assert "+911234567890" in ravi["phones"]

    def test_skill_aliases_applied(self, full_results):
        priya = _by_name(full_results, "Priya Sharma")
        skill_names = [s["name"] for s in priya["skills"]]
        assert "react"      in skill_names   # ReactJS → react
        assert "javascript" in skill_names   # JS → javascript
        assert "postgres"   in skill_names   # PostgreSQL → postgres
        assert "node"       in skill_names   # Node.js → node
        assert "rest"       in skill_names   # REST APIs → rest

    def test_experience_dates_normalized_to_yyyy_mm(self, full_results):
        priya = _by_name(full_results, "Priya Sharma")
        closed_exp = next(e for e in priya["experience"] if e["end"] is not None)
        assert closed_exp["start"] == "2019-06"
        assert closed_exp["end"]   == "2020-12"

    def test_location_parsed_to_components(self, full_results):
        priya = _by_name(full_results, "Priya Sharma")
        loc = priya["location"]
        assert loc["city"]    == "Bengaluru"
        assert loc["region"]  == "Karnataka"
        assert loc["country"] == "IN"          # ISO-3166 alpha-2


# ══════════════════════════════════════════════════════════════════════════════
# 6. CONFIDENCE SCORING
# ══════════════════════════════════════════════════════════════════════════════

class TestConfidenceScoring:
    """Confidence heuristics produce the expected scores."""

    def test_conflict_lowers_overall_confidence(self, full_results):
        """Priya's headline conflicts → overall_confidence drops below 1.0."""
        priya = _by_name(full_results, "Priya Sharma")
        assert priya["overall_confidence"] < 1.0

    def test_single_source_candidate_has_max_confidence(self, full_results):
        """Arjun is CSV-only, no conflicts → overall_confidence == 1.0."""
        arjun = _by_name(full_results, "Arjun Mehta")
        assert arjun["overall_confidence"] == 1.0

    def test_each_skill_has_confidence_in_range(self, full_results):
        priya = _by_name(full_results, "Priya Sharma")
        for skill in priya["skills"]:
            assert 0.0 <= skill["confidence"] <= 1.0

    def test_years_experience_is_derived(self, full_results):
        """years_experience is derived from date arithmetic, not directly extracted."""
        priya = _by_name(full_results, "Priya Sharma")
        # Priya has one closed role (Jun 2019 – Dec 2020 = 18 months = 1.5 years)
        assert priya["years_experience"] == 1.5
        # Provenance method should say "derived"
        ye_prov = next(
            (p for p in priya["provenance"] if p["field"] == "years_experience"), None
        )
        assert ye_prov is not None
        assert "derived" in ye_prov["method"]


# ══════════════════════════════════════════════════════════════════════════════
# 7. VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation:
    """validate.py catches malformed output before it reaches the caller."""

    def test_bad_phone_format_raises(self):
        from transformer.validate import validate, ValidationError
        from transformer.project import load_config
        cfg = load_config(None)
        bad_output = {
            "candidate_id": "abc123",
            "full_name": "Test User",
            "emails": [],
            "phones": ["not-e164"],      # ← invalid
            "location": {}, "links": {}, "skills": [],
            "experience": [], "education": [], "provenance": [],
            "overall_confidence": 1.0,
        }
        with pytest.raises(ValidationError, match="E.164"):
            validate(bad_output, cfg)

    def test_bad_experience_date_raises(self):
        from transformer.validate import validate, ValidationError
        from transformer.project import load_config
        cfg = load_config(None)
        bad_output = {
            "candidate_id": "abc123",
            "full_name": "Test User",
            "emails": [], "phones": [],
            "location": {}, "links": {}, "skills": [],
            "experience": [{"company": "Acme", "title": "Dev", "start": "Jan-2021", "end": None, "summary": None}],
            "education": [], "provenance": [],
            "overall_confidence": 1.0,
        }
        with pytest.raises(ValidationError, match="YYYY-MM"):
            validate(bad_output, cfg)

    def test_out_of_range_confidence_raises(self):
        from transformer.validate import validate, ValidationError
        from transformer.project import load_config
        cfg = load_config(None)
        bad_output = {
            "candidate_id": "abc123", "full_name": "Test User",
            "emails": [], "phones": [], "location": {}, "links": {},
            "skills": [], "experience": [], "education": [], "provenance": [],
            "overall_confidence": 2.5,   # ← out of range
        }
        with pytest.raises(ValidationError, match=r"\[0, 1\]"):
            validate(bad_output, cfg)
