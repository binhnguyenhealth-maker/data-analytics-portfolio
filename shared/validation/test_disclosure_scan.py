"""Test wrapper for the disclosure scan.

Fails closed if any forbidden marker, email, or phone pattern is found in
any tracked text file or filename. Verifies entity provenance: every city,
country, and code in synthetic outputs must come from the shared fictional
entity pools, not from any real-world source.

The negative-fixture class `TestDisclosureBoundaries` exercises the
case-insensitive marker gate (mixed-case variants of every forbidden category
must be caught), the phone-in-hex-digest suppression (a 10-digit decimal run
inside a 64-char SHA-256 digest is a hash, not a phone), and the requirement
that a real standalone phone number is still caught.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "_scan", ROOT / "shared" / "validation" / "scan.py")
scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scan)

_SPEC2 = importlib.util.spec_from_file_location(
    "_shared_syn", ROOT / "shared" / "synthetic" / "__init__.py")
shared_syn = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(shared_syn)


class TestDisclosureScan(unittest.TestCase):

    def test_no_forbidden_markers_anywhere(self):
        results = scan.scan_all()
        self.assertEqual(results, {},
                         f"forbidden markers found in tracked files: {results}")

    def test_excluded_local_staging_notes_do_not_mask_tracked_findings(self):
        # Excluded coordination and superseded staging notes are outside the
        # public payload. Every scanned file must still remain clean.
        results = scan.scan_all()
        for path in results:
            self.fail(f"unexpected marker hit in {path}")

    def test_scanner_source_self_scans_clean(self):
        # The scanner assembles forbidden markers from fragments at runtime so
        # its own source does not contain the literal substrings. This must
        # remain true after any edit to scan.py.
        src = (ROOT / "shared" / "validation" / "scan.py").read_text(
            encoding="utf-8")
        self.assertEqual(scan.scan_text(src), {},
                         "scanner source must self-scan clean")


class TestDisclosureBoundaries(unittest.TestCase):
    """Negative fixtures for the disclosure gate. Each test passes a
    hand-built adversarial text through the real `scan_text` and asserts the
    exact category is caught (or correctly suppressed).

    Forbidden material is assembled at runtime from fragments (mirroring the
    scanner's own technique) so this test source self-scans clean. Embedding
    the literal forbidden strings in the test would itself trip the gate.
    """

    def _cat(self, parts):
        return "".join(parts)

    # --- Case-insensitive marker gate (mixed-case variants must be caught) ---

    def test_mixed_case_org_acronym_title_case_is_caught(self):
        org = self._cat(["A", "t", "o"])
        hits = scan.scan_text(f"contact {org} helpdesk for access")
        self.assertIn("third_party_org", hits)
        canonical = self._cat(["A", "T", "O"])
        self.assertIn(canonical, hits["third_party_org"])

    def test_mixed_case_org_acronym_upper_is_caught(self):
        org = self._cat(["A", "T", "O"])
        hits = scan.scan_text(f"flagged by {org} reviewers")
        self.assertIn("third_party_org", hits)

    def test_mixed_case_org_acronym_lower_is_caught(self):
        org = self._cat(["a", "t", "o"])
        hits = scan.scan_text(f"flagged by {org} reviewers")
        self.assertIn("third_party_org", hits)

    def test_mixed_case_org_name_title_is_caught(self):
        name = self._cat(["asian", " ", "Transport"])
        hits = scan.scan_text(f"via {name} network")
        self.assertIn("third_party_org", hits)

    def test_mixed_case_org_name_mixed_words_is_caught(self):
        name = self._cat(["asian", " ", "DEVELOPMENT", " ", "bank"])
        hits = scan.scan_text(f"funded by {name}")
        self.assertIn("third_party_org", hits)

    def test_mixed_case_path_marker_upper_is_caught(self):
        marker = self._cat(["R", "F", "P"])
        hits = scan.scan_text(f"the {marker} deadline is soon")
        self.assertIn("private_path_or_marker", hits)

    def test_mixed_case_private_path_is_caught(self):
        path = self._cat(["/", "USERS", "/"])
        hits = scan.scan_text(f"see {path} for the file")
        self.assertIn("private_path_or_marker", hits)

    def test_mixed_case_access_material_is_caught(self):
        # The access-material category must catch title case.
        word = self._cat(["Pass", "word"])
        hits = scan.scan_text(f"enter your {word} here")
        self.assertIn("secret_like", hits)

    def test_mixed_case_access_material_partial_compound_is_not_caught(self):
        # 'Cred' alone is not a forbidden marker; the full compound is. This
        # asserts the gate does NOT over-match a short fragment (negative
        # control for the word-boundary rule).
        hits = scan.scan_text("store the user Cred in the vault")
        self.assertNotIn("secret_like", hits)

    # --- Phone-in-hex-digest suppression (the Wave 2 false positive) ---

    def test_phone_digit_run_inside_sha256_digest_is_suppressed(self):
        # Reproduce the exact Wave 2 false positive: a 10-digit decimal run
        # embedded in a 64-char lowercase hex SHA-256 digest. Assembled from
        # halves so the test source does not contain a literal forbidden
        # phone-shaped run.
        digest = (self._cat(["e333caeef554276dd50e949412fd27ec",
                             "2822f1f9aeb6bc370d94b075"]))
        digest = digest + self._cat(["8", "6", "7", "5", "7", "7", "6", "2"])
        text = f"source_manifest_sha256,{digest}\n"
        hits = scan.scan_text(text)
        self.assertNotIn("phone", hits,
                         "digit run inside a SHA-256 digest must not be a phone")

    def test_phone_digit_run_inside_upper_hex_digest_is_suppressed(self):
        digest = (self._cat(["E333CAEEF554276DD50E949412FD27EC",
                             "2822F1F9AEB6BC370D94B075"]))
        digest = digest + self._cat(["8", "6", "7", "5", "7", "7", "6", "2"])
        text = f"digest={digest}"
        hits = scan.scan_text(text)
        self.assertNotIn("phone", hits)

    def test_real_standalone_phone_is_still_caught(self):
        # Assemble the phone from fragments so the test source stays clean.
        num = self._cat(["(61", "7) 5", "55-0", "142"])
        hits = scan.scan_text(f"call me at {num} please")
        self.assertIn("phone", hits)
        self.assertIn(num, hits["phone"])

    def test_real_bare_ten_digit_phone_is_still_caught(self):
        # A bare 10-digit number NOT embedded in a hex digest must still fire.
        num = self._cat(["61", "7", "5", "5", "5", "0", "1", "4", "2"])
        # ensure 10 digits total
        num = self._cat(["617", "555", "0142"])
        hits = scan.scan_text(f"phone {num}")
        self.assertIn("phone", hits)

    def test_phone_adjacent_to_short_hex_is_still_caught(self):
        # A short hex fragment (under 40 chars) does not suppress phone matches.
        num = self._cat(["(61", "7) 5", "55-0", "142"])
        hits = scan.scan_text(f"digest=deadbeef; agent {num}")
        self.assertIn("phone", hits)

    # --- Email gate ---

    def test_email_is_caught(self):
        addr = self._cat(["agent", "@", "example", ".", "com"])
        hits = scan.scan_text(f"reply to {addr} today")
        self.assertIn("email", hits)


class TestEntityProvenance(unittest.TestCase):
    """Every entity in synthetic outputs must come from the shared fictional
    entity pools defined in shared.synthetic."""

    def test_cities_in_outputs_are_from_fictional_pool(self):
        # Project 3 roster is the most comprehensive city consumer
        p3_roster = ROOT / "projects" / "peer-scenario-explorer" / "data" / "synthetic" / "city_roster.csv"
        if not p3_roster.exists():
            self.skipTest("p3 roster not yet generated")
        with open(p3_roster, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        pool = {c["city_id"] for c in shared_syn.fictional_cities(30, project_id="peer-scenario-explorer")}
        for r in rows:
            self.assertIn(r["city_id"], pool,
                          f"city {r['city_id']} not in fictional pool")

    def test_countries_in_outputs_are_from_fictional_pool(self):
        p3_roster = ROOT / "projects" / "peer-scenario-explorer" / "data" / "synthetic" / "city_roster.csv"
        if not p3_roster.exists():
            self.skipTest("p3 roster not yet generated")
        with open(p3_roster, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        pool = {c["country_code"] for c in shared_syn.COUNTRIES}
        for r in rows:
            self.assertIn(r["country_code"], pool,
                          f"country {r['country_code']} not in fictional pool")

    def test_no_real_iso_country_codes(self):
        # Fictional codes all start with 'Z' and are 3 letters; none match
        # real ISO 3166-1 alpha-3 codes for major countries.
        real_alpha3 = {"USA", "CAN", "MEX", "BRA", "ARG", "GBR", "FRA", "DEU",
                       "ITA", "ESP", "RUS", "CHN", "JPN", "IND", "AUS", "ZWE"}
        for c in shared_syn.COUNTRIES:
            self.assertNotIn(c["country_code"], real_alpha3,
                             f"fictional code collides with real ISO alpha-3: {c['country_code']}")
            self.assertTrue(c["country_code"].startswith("Z"),
                            f"fictional country code must start with Z: {c['country_code']}")

    def test_city_ids_use_synthetic_prefix(self):
        for c in shared_syn.fictional_cities(30, project_id="test"):
            self.assertTrue(c["city_id"].startswith("CTY-"),
                            f"city_id must use CTY- prefix: {c['city_id']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
