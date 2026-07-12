#!/usr/bin/env python3
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[2] / "data.json"

COMMUNITY_SOURCES = [
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
]
SEASONS = {"Summer"}

ATS_SOURCES = [
    {"provider": "greenhouse", "token": "etchedai", "label": "Etched"},
]

VERIFICATION_KEYWORDS = [
    "design verification", "dv engineer", "verification engineer",
    "functional verification", "formal verification", "rtl verification",
    "uvm", "post-silicon", "post silicon", "silicon validation",
    "hardware validation", "soc verification", "asic verification",
    "verification intern", "validation engineer",
    "verification", "validation", "emulation", "dft",
]
DESIGN_KEYWORDS = [
    "rtl design", "asic design", "cpu design", "soc design", "chip design",
    "physical design", "logic design", "digital design", "silicon design",
    "vlsi", "microarchitect", "micro-architect", "microarchitecture",
    "place and route", "synthesis", "static timing", "timing closure",
    "clock domain", "design engineer", "hardware engineer",
    "rtl", "asic", "soc", "fpga", "silicon",
]
DESIGN_EXCLUDE = [
    "analog", "mixed-signal", "mixed signal", "rfic", "rf design",
    "radio frequency", "photonic", "pmic", "power management ic", "pcb",
]
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

INTERN_RE = re.compile(r"\bintern(ship)?s?\b|\bco[- ]?op\b", re.I)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def matched(text, keywords):
    hits = []
    for kw in keywords:
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", text):
            hits.append(kw)
    return hits


def passes_gate(text, hits):
    if any(h in STRONG_TOKENS for h in hits):
        return True
    if any(h not in AMBIGUOUS for h in hits):
        return True
    return any(g in text for g in HARDWARE_GATE)


def classify(title):
    t = norm(title)
    v_hits = matched(t, VERIFICATION_KEYWORDS)
    d_hits = matched(t, DESIGN_KEYWORDS)
    if v_hits and passes_gate(t, v_hits):
        return "verification", sorted(set(v_hits))
    if d_hits and passes_gate(t, d_hits):
        if any(x in t for x in DESIGN_EXCLUDE):
            return None, []
        return "design", sorted(set(d_hits))
    return None, []


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "silicon-intern-tracker"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _to_unix(iso):
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def fetch_community(url):
    out = []
    for item in _get(url):
        if not item.get("active", True) or not item.get("is_visible", True):
            continue
        if item.get("season") not in SEASONS:
            continue
        out.append({
            "id": item.get("id") or item.get("url"),
            "company_name": item.get("company_name", ""),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "locations": item.get("locations") or [],
            "season": item.get("season", ""),
            "sponsorship": item.get("sponsorship", ""),
            "date_posted": item.get("date_posted") or item.get("date_updated") or 0,
            "source": "community",
        })
    return out


def fetch_greenhouse(token, label):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if not INTERN_RE.search(title):
            continue
        loc = (j.get("location") or {}).get("name", "")
        out.append({
            "id": f"{token}-{j.get('id')}",
            "company_name": label,
            "title": title,
            "url": j.get("absolute_url", ""),
            "locations": [loc] if loc else [],
            "season": "Summer",
            "sponsorship": "",
            "date_posted": _to_unix(j.get("updated_at")),
            "source": "etched" if token == "etchedai" else token,
        })
    return out


def fetch_lever(token, label):
    data = _get(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in data:
        title = j.get("text", "")
        if not INTERN_RE.search(title):
            continue
        cats = j.get("categories", {}) or {}
        loc = cats.get("location", "")
        out.append({
            "id": f"{token}-{j.get('id')}",
            "company_name": label,
            "title": title,
            "url": j.get("hostedUrl", ""),
            "locations": [loc] if loc else [],
            "season": "Summer",
            "sponsorship": "",
            "date_posted": int((j.get("createdAt") or 0) / 1000),
            "source": token,
        })
    return out


def fetch_ashby(token, label):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if not INTERN_RE.search(title):
            continue
        loc = j.get("location", "")
        out.append({
            "id": f"{token}-{j.get('id')}",
            "company_name": label,
            "title": title,
            "url": j.get("jobUrl", ""),
            "locations": [loc] if loc else [],
            "season": "Summer",
            "sponsorship": "",
            "date_posted": _to_unix(j.get("publishedAt") or j.get("updatedAt")),
            "source": token,
        })
    return out


ATS_FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever,
                "ashby": fetch_ashby}


def build_roles(raw):
    seen = {}
    for item in raw:
        lane, hits = classify(item["title"])
        if lane is None:
            continue
        rid = item["id"]
        if rid in seen:
            continue
        seen[rid] = {
            "id": rid,
            "company": (item["company_name"] or "").strip(),
            "title": (item["title"] or "").strip(),
            "url": item["url"],
            "locations": item["locations"],
            "season": item["season"],
            "sponsorship": item["sponsorship"],
            "posted": item["date_posted"],
            "lane": lane,
            "source": item["source"],
            "keywords": hits,
        }
    return sorted(seen.values(), key=lambda r: r["posted"], reverse=True)


def signature(roles):
    return sorted(
        (r["id"], r["lane"], r["company"], r["title"], r["url"],
         tuple(r["locations"]), r["sponsorship"])
        for r in roles
    )


def main():
    raw, failures = [], []
    for url in COMMUNITY_SOURCES:
        try:
            raw += fetch_community(url)
        except Exception as e:
            failures.append(f"community {url}: {e}")
    for s in ATS_SOURCES:
        try:
            raw += ATS_FETCHERS[s["provider"]](s["token"], s["label"])
        except Exception as e:
            failures.append(f"{s['label']} ({s['provider']}): {e}")

    if failures:
        for f in failures:
            print(f"WARN: source failed — {f}", file=sys.stderr)
        print("A source failed; preserving last-good data.json (no write).")
        return

    roles = build_roles(raw)

    old = {}
    if DATA_PATH.exists():
        try:
            old = json.loads(DATA_PATH.read_text())
        except Exception:
            old = {}
    if DATA_PATH.exists() and signature(roles) == signature(old.get("roles", [])):
        print(f"No role change ({len(roles)} roles). Leaving data.json untouched.")
        return

    design = [r for r in roles if r["lane"] == "design"]
    verif = [r for r in roles if r["lane"] == "verification"]
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seasons": sorted(SEASONS),
        "counts": {"total": len(roles), "design": len(design),
                   "verification": len(verif)},
        "roles": roles,
    }
    DATA_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Roles changed — wrote {len(roles)} "
          f"({len(design)} design / {len(verif)} verification)")


if __name__ == "__main__":
    main()
