#!/usr/bin/env python3
"""Regenerate the three clean, source-backed repository preview images."""

from __future__ import annotations

import csv
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
W, H = 1468, 768
NAVY, BLUE, TEAL = "#17324D", "#2E6E9E", "#24838B"
INK, GRAY, LIGHT, PALE, WHITE = "#24313D", "#65727D", "#E5EDF3", "#F5F8FA", "#FFFFFF"
AMBER, GREEN = "#D8912A", "#2F7D68"


def _font_path(bold: bool) -> str:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    return next(path for path in candidates if Path(path).is_file())


def f(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(bold), size=size)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def base(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(image)
    d.rectangle((0, 0, W, 12), fill=TEAL)
    d.text((58, 37), title, font=f(34, True), fill=NAVY)
    d.text((58, 84), subtitle, font=f(17), fill=GRAY)
    d.text((W - 58, 45), "SYNTHETIC DATA • INDEPENDENT PORTFOLIO", font=f(12, True), fill=TEAL, anchor="ra")
    return image, d


def box(d: ImageDraw.ImageDraw, bounds: tuple[int, int, int, int], fill: str = WHITE) -> None:
    d.rounded_rectangle(bounds, radius=16, fill=fill, outline=LIGHT, width=2)


def metric(d: ImageDraw.ImageDraw, bounds: tuple[int, int, int, int], value: str, label: str, color: str = BLUE) -> None:
    box(d, bounds, PALE)
    x1, y1, x2, _ = bounds
    d.text(((x1 + x2) / 2, y1 + 16), value, font=f(27, True), fill=color, anchor="ma")
    d.text(((x1 + x2) / 2, y1 + 53), label, font=f(13), fill=GRAY, anchor="ma")


def wrap(d: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], width: int, size: int = 16, color: str = INK) -> None:
    d.multiline_text(xy, "\n".join(textwrap.wrap(text, width=width)), font=f(size), fill=color, spacing=5)


def finish(image: Image.Image, path: Path) -> None:
    d = ImageDraw.Draw(image)
    d.line((58, H - 44, W - 58, H - 44), fill=LIGHT, width=2)
    d.text((58, H - 30), "Source-backed presentation preview • packaged Tableau workbook available", font=f(12), fill=GRAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=97, subsampling=0, optimize=True)


def data_quality() -> None:
    root = ROOT / "projects/data-quality-command-center"
    queue = sorted(rows(root / "data/synthetic/refresh_priority_queue.csv"), key=lambda r: int(r["priority_rank"]))
    inventory = rows(root / "data/synthetic/source_inventory.csv")
    lineage = rows(root / "data/synthetic/issue_lineage.csv")
    image, d = base("Data Quality Refresh Command Center", "A governed queue for choosing the next source-quality action")
    metric(d, (58, 122, 280, 207), str(len(inventory)), "inventory rows")
    metric(d, (298, 122, 520, 207), str(len(queue)), "priority issues")
    metric(d, (538, 122, 760, 207), str(len(lineage)), "lineage records")
    metric(d, (778, 122, 1000, 207), "1", "readback receipt", GREEN)
    metric(d, (1018, 122, 1410, 207), "Deterministic", "same inputs → same queue", TEAL)
    box(d, (58, 235, 1015, 699))
    d.text((88, 260), "PRIORITIZED INVESTIGATION QUEUE", font=f(14, True), fill=TEAL)
    for i, r in enumerate(queue):
        y = 306 + i * 60
        d.ellipse((90, y, 126, y + 36), fill=NAVY if i == 0 else LIGHT)
        d.text((108, y + 8), str(i + 1), font=f(15, True), fill=WHITE if i == 0 else NAVY, anchor="ma")
        title = r["issue_title"]
        d.text((158, y), title if len(title) <= 54 else title[:51] + "…", font=f(16, True), fill=INK)
        d.text((158, y + 27), r["record_family_group"].title(), font=f(12), fill=GRAY)
        status = r["severity"]
        d.rounded_rectangle((760, y + 2, 980, y + 33), radius=15, fill="#F7EBD9" if i < 2 else "#E4F1EE")
        d.text((870, y + 9), status, font=f(12, True), fill=AMBER if i < 2 else GREEN, anchor="ma")
        if i < len(queue) - 1:
            d.line((88, y + 49, 985, y + 49), fill=LIGHT, width=1)
    box(d, (1045, 235, 1410, 699), NAVY)
    d.text((1074, 263), "DECISION RULE", font=f(14, True), fill="#79D4D6")
    wrap(d, queue[0]["priority_reason"], (1074, 306), 28, 17, WHITE)
    d.line((1074, 500, 1380, 500), fill="#4D667B", width=2)
    d.text((1074, 526), "NEXT ACTION", font=f(13, True), fill="#79D4D6")
    wrap(d, queue[0]["owner_action"], (1074, 561), 29, 15, WHITE)
    finish(image, root / "images/dashboard-preview.jpeg")


def mobility() -> None:
    root = ROOT / "projects/urban-mobility-gap"
    data = rows(root / "data/synthetic/city_model.csv")
    image, d = base("Urban Mobility Gap Diagnostic", "Which fictional cities merit contextual review after a frozen benchmark?")
    plot = (72, 155, 980, 640)
    box(d, (58, 125, 1010, 700))
    x0, y0, x1, y1 = plot
    for i in range(6):
        x = x0 + (x1 - x0) * i / 5
        y = y1 - (y1 - y0) * i / 5
        d.line((x, y0, x, y1), fill=LIGHT, width=1)
        d.line((x0, y, x1, y), fill=LIGHT, width=1)
        d.text((x, y1 + 9), f"{i*20}%", font=f(11), fill=GRAY, anchor="ma")
        d.text((x0 - 8, y), f"{i*20}%", font=f(11), fill=GRAY, anchor="rm")
    d.line((x0, y1, x1, y0), fill=GRAY, width=2)
    above = below = 0
    default = None
    for r in data:
        expected, actual = float(r["expected_association"]), float(r["actual_association"])
        x, y = x0 + expected * (x1 - x0), y1 - actual * (y1 - y0)
        color = TEAL if actual >= expected else AMBER
        above += actual >= expected
        below += actual < expected
        d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color, outline=WHITE, width=1)
        if r["is_default_focus"] == "True":
            default = (r, x, y)
    if default:
        r, x, y = default
        d.ellipse((x - 12, y - 12, x + 12, y + 12), outline=NAVY, width=3)
        d.text((x + 16, y - 12), r["city_name"], font=f(13, True), fill=NAVY)
    d.text(((x0 + x1) / 2, 662), "Expected association", font=f(14, True), fill=INK, anchor="ma")
    d.text((72, 133), "Observed association ↑", font=f(12, True), fill=INK)
    box(d, (1040, 125, 1410, 700), PALE)
    d.text((1070, 157), "REVIEW SUMMARY", font=f(14, True), fill=TEAL)
    metric(d, (1070, 198, 1220, 283), str(above), "above expected", TEAL)
    metric(d, (1235, 198, 1380, 283), str(below), "below expected", AMBER)
    d.text((1070, 330), "ANALYTICAL BOUNDARY", font=f(14, True), fill=TEAL)
    wrap(d, "Frozen benchmark. Filters subset stored results; they never refit, rescale, or rerank the model.", (1070, 370), 29, 17)
    d.line((1070, 500, 1380, 500), fill=LIGHT, width=2)
    d.text((1070, 530), "USE", font=f(14, True), fill=TEAL)
    wrap(d, "A gap is a context-review prompt—not a forecast, causal estimate, or performance ranking.", (1070, 569), 30, 17)
    finish(image, root / "images/dashboard-preview.jpeg")


def peers() -> None:
    root = ROOT / "projects/peer-scenario-explorer"
    roster = {r["city_id"]: r for r in rows(root / "data/synthetic/city_roster.csv")}
    focal_id = "CTY-001"
    peers = [r for r in rows(root / "data/synthetic/peer_result.csv") if r["focal_city_id"] == focal_id and r["scenario_label"] == "baseline"]
    peers.sort(key=lambda r: int(r["peer_rank"]))
    summaries = rows(root / "data/synthetic/stability_summary.csv")
    summary = next(r for r in summaries if r["focal_city_id"] == focal_id)
    stable = sum(r["stability_badge"].startswith("stable") for r in summaries)
    image, d = base("Explainable Peer Scenario Explorer", f"Focal: {roster[focal_id]['city_name']} • baseline scenario • five structural peers")
    box(d, (58, 125, 955, 700))
    d.text((88, 154), "WHY THESE FIVE PEERS", font=f(14, True), fill=TEAL)
    d.text((88, 193), "RANK", font=f(12, True), fill=GRAY)
    d.text((192, 193), "PEER", font=f(12, True), fill=GRAY)
    d.text((560, 193), "COUNTRY", font=f(12, True), fill=GRAY)
    d.text((808, 193), "DISTANCE", font=f(12, True), fill=GRAY)
    d.line((88, 217, 925, 217), fill=LIGHT, width=2)
    mx = max(float(r["final_distance"]) for r in peers)
    for i, r in enumerate(peers):
        y = 244 + i * 78
        city = roster[r["peer_city_id"]]
        d.ellipse((94, y, 132, y + 38), fill=NAVY)
        d.text((113, y + 9), r["peer_rank"], font=f(16, True), fill=WHITE, anchor="ma")
        d.text((192, y), city["city_name"], font=f(18, True), fill=INK)
        d.text((192, y + 29), city["subregion"], font=f(12), fill=GRAY)
        d.text((560, y + 10), city["country_name"], font=f(14), fill=INK)
        val = float(r["final_distance"])
        d.rounded_rectangle((808, y + 8, 905, y + 25), radius=8, fill=LIGHT)
        d.rounded_rectangle((808, y + 8, 808 + 97 * val / mx, y + 25), radius=8, fill=BLUE)
        d.text((925, y + 6), f"{val:.2f}", font=f(12, True), fill=NAVY, anchor="ra")
        d.line((88, y + 59, 925, y + 59), fill=LIGHT, width=1)
    d.text((88, 653), "Context is shown only after matching and never affects peer selection.", font=f(13, True), fill=GREEN)
    box(d, (985, 125, 1410, 700), PALE)
    d.text((1015, 157), "SENSITIVITY", font=f(14, True), fill=TEAL)
    metric(d, (1015, 198, 1188, 283), summary["n_variants"], "audited variants")
    metric(d, (1204, 198, 1380, 283), f"{float(summary['min_jaccard']):.2f}", "worst overlap", AMBER)
    for i, (label, key, color) in enumerate([("Minimum", "min_jaccard", AMBER), ("Median", "median_jaccard", TEAL), ("Mean", "mean_jaccard", BLUE)]):
        y = 335 + i * 58
        val = float(summary[key])
        d.text((1015, y), label, font=f(13), fill=GRAY)
        d.rounded_rectangle((1110, y + 2, 1350, y + 18), radius=8, fill=LIGHT)
        d.rounded_rectangle((1110, y + 2, 1110 + 240 * val, y + 18), radius=8, fill=color)
        d.text((1380, y - 1), f"{val:.2f}", font=f(12, True), fill=NAVY, anchor="ra")
    d.line((1015, 520, 1380, 520), fill=LIGHT, width=2)
    d.text((1015, 548), "PORTFOLIO-WIDE", font=f(13, True), fill=TEAL)
    d.text((1015, 586), str(stable), font=f(30, True), fill=GREEN)
    d.text((1068, 595), "stable", font=f(14), fill=GRAY)
    d.text((1195, 586), str(len(summaries) - stable), font=f(30, True), fill=AMBER)
    d.text((1253, 595), "sensitive", font=f(14), fill=GRAY)
    wrap(d, "Sensitivity is a result to inspect, not a defect to hide.", (1015, 640), 34, 14)
    finish(image, root / "images/dashboard-preview.jpeg")


def main() -> int:
    data_quality()
    mobility()
    peers()
    for path in sorted(ROOT.glob("projects/*/images/dashboard-preview.jpeg")):
        print(path.relative_to(ROOT), path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
