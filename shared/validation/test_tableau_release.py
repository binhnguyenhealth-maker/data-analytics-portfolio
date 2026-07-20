"""Fail-closed checks for the exact public Tableau portfolio payload."""
from __future__ import annotations

import csv
import io
import re
import struct
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]

PROJECTS = {
    "data-quality-command-center": {
        "package": "projects/data-quality-command-center/tableau/Data_Quality_Refresh_Command_Center_SYNTHETIC_PORTFOLIO.twbx",
        "preview": "projects/data-quality-command-center/images/dashboard-preview.jpeg",
        "dashboard": "Data Quality Refresh Command Center",
        "worksheets": [
            "01 Inventory & Reconciliation",
            "02 Refresh Priority Queue",
            "03 Lineage Evidence Detail",
            "04 Remediation Receipt",
        ],
        "embedded": {
            "Data/synthetic/source_inventory.csv": "projects/data-quality-command-center/data/synthetic/source_inventory.csv",
            "Data/synthetic/issue_lineage.csv": "projects/data-quality-command-center/data/synthetic/issue_lineage.csv",
            "Data/synthetic/refresh_priority_queue.csv": "projects/data-quality-command-center/data/synthetic/refresh_priority_queue.csv",
            "Data/synthetic/remediation_receipt.csv": "projects/data-quality-command-center/data/synthetic/remediation_receipt.csv",
        },
    },
    "urban-mobility-gap": {
        "package": "projects/urban-mobility-gap/tableau/Urban_Mobility_Gap_Diagnostic_SYNTHETIC_PORTFOLIO.twbx",
        "preview": "projects/urban-mobility-gap/images/dashboard-preview.jpeg",
        "dashboard": "Urban Mobility Gap Diagnostic — SYNTHETIC PORTFOLIO",
        "worksheets": [
            "01 Actual vs Expected Association",
            "01A Equality Guide (same 0–100% axes)",
            "02A City Summary",
            "02B Modal Composition",
            "02C Predictor Profile",
            "02D Default Guidance and No-match",
            "03A Robustness Summary",
            "03B Geographic Warnings",
        ],
        "embedded": {
            "Data/synthetic/dashboard_presentation.csv": "projects/urban-mobility-gap/data/synthetic/dashboard_presentation.csv",
        },
    },
    "peer-scenario-explorer": {
        "package": "projects/peer-scenario-explorer/tableau/Peer_Scenario_Stability_Explorer_SYNTHETIC_PORTFOLIO.twbx",
        "preview": "projects/peer-scenario-explorer/images/dashboard-preview.jpeg",
        "dashboard": "Explainable Urban Peer Scenario Stability Explorer — SYNTHETIC PORTFOLIO",
        "worksheets": [
            "01 Focal City + Five Peers",
            "02 Why This Peer",
            "03 Closest vs Diversified",
            "04 Context After Matching",
        ],
        "embedded": {
            "Data/synthetic/peer_scenario_surface.csv": "projects/peer-scenario-explorer/data/synthetic/peer_scenario_surface.csv",
        },
    },
}


def jpeg_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise AssertionError("preview is not JPEG")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        if marker in {0xD8, 0xD9}:
            i += 2
            continue
        if i + 4 > len(data):
            break
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + length
    raise AssertionError("JPEG dimensions not found")


def xml_findings(text: str) -> list[str]:
    findings = []
    lower = text.lower()
    assembled = [
        "/" + "Users" + "/",
        "Asian" + " " + "Development" + " " + "Bank",
        "I" + "D" + "N" + "-",
        "K" + "A" + "Z" + "-",
        "V" + "N" + "M" + "-",
    ]
    for marker in assembled:
        if marker.lower() in lower:
            findings.append(marker)
    acronym = "A" + "T" + "O"
    if re.search(r"\b" + acronym.lower() + r"\b", lower):
        findings.append(acronym)
    access_word = "pass" + "word"
    if re.search(access_word + r"\s*=\s*['\"][^'\"]+", lower):
        findings.append("nonempty access attribute")
    return findings


class TestTableauRelease(unittest.TestCase):

    def test_exact_package_contracts_and_embedded_bytes(self):
        for project, spec in PROJECTS.items():
            with self.subTest(project=project):
                package = ROOT / spec["package"]
                self.assertTrue(package.is_file())
                with zipfile.ZipFile(package) as archive:
                    self.assertIsNone(archive.testzip())
                    names = archive.namelist()
                    twbs = [name for name in names if name.endswith(".twb")]
                    self.assertEqual(len(twbs), 1)
                    self.assertEqual(set(names), set(twbs) | set(spec["embedded"]))
                    xml = archive.read(twbs[0]).decode("utf-8")
                    root = ET.fromstring(xml)
                    worksheets = [n.attrib["name"] for n in root.findall("./worksheets/worksheet")]
                    dashboards = root.findall("./dashboards/dashboard")
                    self.assertEqual(worksheets, spec["worksheets"])
                    self.assertEqual([d.attrib["name"] for d in dashboards], [spec["dashboard"]])
                    size = dashboards[0].find("./size")
                    self.assertEqual(
                        {key: size.attrib.get(key) for key in ("minwidth", "maxwidth", "minheight", "maxheight")},
                        {"minwidth": "1366", "maxwidth": "1366", "minheight": "768", "maxheight": "768"},
                    )
                    self.assertIn("SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT", xml)
                    self.assertEqual(xml_findings(xml), [])
                    for embedded, source in spec["embedded"].items():
                        self.assertEqual(archive.read(embedded), (ROOT / source).read_bytes())

    def test_previews_are_native_sized_jpegs(self):
        for project, spec in PROJECTS.items():
            with self.subTest(project=project):
                preview = ROOT / spec["preview"]
                self.assertGreater(preview.stat().st_size, 100_000)
                self.assertEqual(jpeg_size(preview), (1468, 768))

    def test_expected_tableau_payload_is_exactly_three_projects(self):
        packages = sorted(ROOT.glob("projects/*/tableau/*.twbx"))
        previews = sorted(ROOT.glob("projects/*/images/*.jpeg"))
        self.assertEqual(len(packages), 3)
        self.assertEqual(len(previews), 3)
        self.assertEqual(
            {p.relative_to(ROOT).as_posix() for p in packages},
            {spec["package"] for spec in PROJECTS.values()},
        )
        self.assertEqual(
            {p.relative_to(ROOT).as_posix() for p in previews},
            {spec["preview"] for spec in PROJECTS.values()},
        )

    def test_negative_private_path_fixture_fails(self):
        marker = "/" + "Users" + "/example/source.csv"
        self.assertTrue(xml_findings(f"<connection directory='{marker}' />"))

    def test_negative_real_entity_fixture_fails(self):
        marker = "Asian" + " " + "Development" + " " + "Bank"
        self.assertTrue(xml_findings(f"<caption>{marker}</caption>"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
