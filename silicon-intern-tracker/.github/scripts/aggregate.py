#!/usr/bin/env python3
"""
Silicon Intern Tracker — aggregator.

Pulls the community-maintained Summer 2027 internship JSON (Pitt CSC / Simplify
lineage, currently the vanshb03 fork), keeps only hardware Design and
Verification roles that match your keyword lists, classifies each into a lane,
and writes data.json for the static dashboard to render.

Runs in CI (GitHub Actions) on a cron, or locally: `python3 aggregate.py`.

You own the keyword lists below — tune them. Everything else is plumbing.
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Sources. The community list already scrapes hundreds of career pages hourly.
# Add more raw-JSON sources here if you find other trackers with the same shape.
# ---------------------------------------------------------------------------
SOURCES = [
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
]

# Seasons to keep. The community list mixes Summer + off-season (Fall/Winter/
# Spring) 2027. You asked for Summer — add the others if you want early signal.
SEASONS = {"Summer"}
# SEASONS = {"Summer", "Fall", "Winter", "Spring"}  # uncomment for everything

# ---------------------------------------------------------------------------
# Classification. A role is KEPT only if it hits a hardware token below, then
# routed to a lane. Order matters: verification is checked before design so a
# "Design Verification" role lands in Verification, not Design.
#
# Tokens are matched case-insensitively as whole-ish phrases against the title.
# ---------------------------------------------------------------------------
VERIFICATION_KEYWORDS = [
    "design verification", "dv engineer", "verification engineer",
    "functional verification", "formal verification", "rtl verification",
    "uvm", "post-silicon", "post silicon", "silicon validation",
    "hardware validation", "soc verification", "asic verification",
    "verification intern", "validation engineer",
    # bare tokens (guarded by the hardware gate below)
    "verification", "validation", "emulation", "dft",
]

DESIGN_KEYWORDS = [
    "rtl design", "asic design", "cpu design", "soc design", "chip design",
    "physical design", "logic design", "digital design", "silicon design",
    "vlsi", "microarchitect", "micro-architect", "microarchitecture",
    "place and route", "synthesis", "static timing", "timing closure",
    "clock domain", "design engineer", "hardware engineer",
    # bare tokens (guarded by the hardware gate below)
    "rtl", "asic", "soc", "fpga", "silicon",
]

# DIGITAL ONLY. Drop analog / RF / mixed-signal design roles even when they
# match a design keyword above — these are outside the RTL/ASIC/CPU skill set.
# (This only touches the Design lane; Verification is unaffected.)
DESIGN_EXCLUDE = [
    "analog", "mixed-signal", "mixed signal", "rfic", "rf design",
    "radio frequency", "photonic", "pmic", "power management ic", "pcb",
]

# The "gate": bare/ambiguous tokens (validation, design engineer, fpga...) only
# count if the role also looks hardware. A title matching a STRONG token skips
# the gate. This keeps out things like "Software Engineer Intern, Maps
# Validation" while keeping "Silicon Validation Intern".
STRONG_TOKENS = [
    "rtl", "asic", "vlsi", "uvm", "microarchitect", "micro-architect",
    "physical design", "design verification", "silicon", "soc design",
    "post-silicon", "post silicon", "dft", "chip design",
]
HARDWARE_GATE = [
    "hardware", "silicon", "chip", "asic", "rtl", "soc", "vlsi", "fpga",
    "semiconductor", "analog", "verilog", "systemverilog", "gpu", "cpu",
    "processor", "circuit", "ic ", "microelectronic", "firmware", "embedded",
]

AMBIGUOUS = {"validation", "design engineer", "verification", "emulation",
             "fpga", "hardware engineer", "synthesis", "dft"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def matched(text: str, keywords) -> list:
    hits = []
    for kw in keywords:
        # word-ish boundary so "dv" doesn't match "advanced"
        pat = r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])"
        if re.search(pat, text):
            hits.append(kw)
    return hits


def passes_gate(text: str, hits: list) -> bool:
    # If any strong token matched, we're confident it's hardware.
    if any(h in STRONG_TOKENS for h in hits):
        return True
    # Otherwise, only keep if the match wasn't purely ambiguous OR the title
    # independently reads hardware.
    if any(h not in AMBIGUOUS for h in hits):
        return True
    return any(g in text for g in HARDWARE_GATE)


def classify(title: str):
    """Return (lane, matched_keywords) or (None, [])."""
    t = norm(title)
    v_hits = matched(t, VERIFICATION_KEYWORDS)
    d_hits = matched(t, DESIGN_KEYWORDS)

    if v_hits and passes_gate(t, v_hits):
        return "verification", sorted(set(v_hits))
    if d_hits and passes_gate(t, d_hits):
        if any(x in t for x in DESIGN_EXCLUDE):
            return None, []  # digital design only — skip analog/RF/mixed-signal
        return "design", sorted(set(d_hits))
    return None, []


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "silicon-intern-tracker"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    seen = {}
    source_status = []
    for url in SOURCES:
        try:
            data = fetch(url)
            source_status.append({"url": url, "ok": True, "count": len(data)})
        except Exception as e:  # noqa: BLE001
            source_status.append({"url": url, "ok": False, "error": str(e)})
            print(f"WARN: source failed {url}: {e}", file=sys.stderr)
            continue

        for item in data:
            if not item.get("active", True) or not item.get("is_visible", True):
                continue
            if item.get("season") not in SEASONS:
                continue
            lane, hits = classify(item.get("title", ""))
            if lane is None:
                continue
            rid = item.get("id") or item.get("url")
            if rid in seen:
                continue
            seen[rid] = {
                "id": rid,
                "company": item.get("company_name", "").strip(),
                "title": item.get("title", "").strip(),
                "url": item.get("url", ""),
                "locations": item.get("locations") or [],
                "season": item.get("season", ""),
                "sponsorship": item.get("sponsorship", ""),
                "posted": item.get("date_posted") or item.get("date_updated") or 0,
                "lane": lane,
                "keywords": hits,
            }

    roles = sorted(seen.values(), key=lambda r: r["posted"], reverse=True)
    design = [r for r in roles if r["lane"] == "design"]
    verif = [r for r in roles if r["lane"] == "verification"]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seasons": sorted(SEASONS),
        "counts": {"total": len(roles), "design": len(design),
                   "verification": len(verif)},
        "sources": source_status,
        "roles": roles,
    }

    out_path = Path(__file__).resolve().parents[2] / "data.json"
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Wrote {out_path} — {len(roles)} roles "
          f"({len(design)} design / {len(verif)} verification)")


if __name__ == "__main__":
    main()
