#!/usr/bin/env python3
"""Barnett party alert — new federal cases naming a watched person, nationwide.

Watches CourtListener for newly created federal district dockets (all 94
districts) and keeps any case whose caption names a person listed in
watch_config.json (default: Christopher Barnett / Christopher M. Barnett /
Christopher M Barnett), on EITHER side of the caption. Civil, criminal, and
other case types are all included. Emails one digest per run.

Modes:
  python poll_barnett_cases.py bootstrap --hours=48   # first run / recovery: seed cursor,
                                                      # dry-run detection over a lookback window
  python poll_barnett_cases.py poll                   # incremental: everything since last cursor
  python poll_barnett_cases.py poll --live            # also send the email digest
  python poll_barnett_cases.py test-patterns          # regression-check watch_config.json

Detection notes:
- Matching is CASE-CAPTION based. The name is matched against the whole
  `case_name` string, so it catches the watched person whether they are the
  plaintiff or the defendant ("USA v. Christopher Barnett", "Christopher
  Barnett v. Acme Corp", etc.).
- CAVEAT — common name: "Christopher Barnett" is not unique. Across 94
  districts, unrelated people share the name. Every hit is a triage lead to
  confirm against the official docket, NOT a positive identification.
- CAVEAT — co-parties hidden by "et al.": a captured caption only shows the
  lead parties. If Barnett is, say, the third co-defendant in "USA v. Smith,
  et al.", the caption will not contain the name and the case is missed. The
  full party list is only in the PACER docket report (not fetched here).
- "New case" means: docket newly created on CL (id cursor) AND date_filed
  within recency_days. CL back-adds old cases constantly; those are skipped.
- Dockets with no date_filed yet are held in a pending queue and re-checked
  each run; after pending_max_days they alert anyway with a "date pending" label.

Data source: CourtListener v4 API (token via CL_TOKEN or COURTLISTENER_TOKEN
env var). No PACER purchases are ever made; PACER links are for manual follow-up.
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from email_utils import send_email, subject_barnett_alert, body_barnett_digest

HERE = Path(__file__).parent
CONFIG_FILE = HERE / "watch_config.json"
STATE_FILE = HERE / "scan_state.json"
ARCHIVE_FILE = HERE / "matched_cases.jsonl"
LOG_FILE = HERE / "poll_log.txt"

API_BASE = "https://www.courtlistener.com/api/rest/v4"
TOKEN = os.environ.get("CL_TOKEN", "") or os.environ.get("COURTLISTENER_TOKEN", "")

ET = timezone(timedelta(hours=-4))  # EDT (UTC-4)

PAGE_CAP_POLL = 100        # ~2,000 dockets; normal 30-min poll is 5-10 pages
PAGE_CAP_BOOTSTRAP = 500   # 48h lookback is ~300 pages

# ── Logging ──────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ── API helpers ──────────────────────────────────────────────────────────────

def api_get(url, retries=3):
    for attempt in range(retries + 1):
        try:
            out = subprocess.run(
                ["curl", "-s", "-H", f"Authorization: Token {TOKEN}",
                 "-H", "User-Agent: Newsroom court monitor (CourtListener API user)",
                 "--max-time", "30", url],
                capture_output=True, text=True, timeout=35,
            )
            if out.returncode != 0:
                raise RuntimeError(f"curl exit {out.returncode}")
            data = json.loads(out.stdout)
            if isinstance(data, dict) and "429" in str(data.get("detail", "")):
                if attempt < retries:
                    wait = 15 * (attempt + 1)
                    log(f"  rate-limited, sleeping {wait}s")
                    time.sleep(wait)
                    continue
            return data
        except (json.JSONDecodeError, RuntimeError) as e:
            if attempt < retries:
                time.sleep(5)
                continue
            raise

# ── Config / matching ────────────────────────────────────────────────────────

def load_config():
    cfg = json.loads(CONFIG_FILE.read_text())
    cfg["_names"] = [(re.compile(p["pattern"], re.I), p["label"]) for p in cfg["names"]]
    cfg["_exclude"] = [re.compile(p, re.I) for p in cfg.get("exclude", [])]
    return cfg

VS_RE = re.compile(r"\s+vs?\.?\s+", re.I)
HTML_RE = re.compile(r"<[^>]+>")

CASE_TYPE_RE = [
    (re.compile(r"\bcv\b", re.I), "civil"),
    (re.compile(r"\bcr\b", re.I), "criminal"),
    (re.compile(r"\bmj\b", re.I), "magistrate"),
    (re.compile(r"\bmc\b", re.I), "misc"),
]

def case_type(docket_number):
    for rx, label in CASE_TYPE_RE:
        if docket_number and rx.search(docket_number):
            return label
    return "other"

def which_side(case_name, rx):
    """'plaintiff' if the name is left of the first ' v. ', 'defendant' if right,
    'caption' if the caption has no ' v. ' split."""
    clean = HTML_RE.sub("", case_name).strip()
    parts = VS_RE.split(clean, maxsplit=1)
    if len(parts) < 2:
        return "caption"
    if rx.search(parts[0]):
        return "plaintiff"
    if rx.search(parts[1]):
        return "defendant"
    return "caption"

def classify(case_name, cfg):
    """Return {'name_label':..., 'side':...} if a watched name appears in the
    caption and no exclusion matches, else None."""
    if not case_name:
        return None
    clean = HTML_RE.sub("", case_name).strip()
    for rx in cfg["_exclude"]:
        if rx.search(clean):
            return None
    for rx, label in cfg["_names"]:
        if rx.search(clean):
            return {"name_label": label, "side": which_side(case_name, rx)}
    return None

# ── State ────────────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1))

# ── Records ──────────────────────────────────────────────────────────────────

def build_record(d, hit, extra_labels=None):
    case_name = HTML_RE.sub("", d.get("case_name") or "").strip()
    court_id = d.get("court_id") or ""
    docket_number = d.get("docket_number") or ""
    pacer_case_id = d.get("pacer_case_id") or ""
    pacer_url = ""
    if pacer_case_id and court_id:
        pacer_url = f"https://ecf.{court_id}.uscourts.gov/cgi-bin/DktRpt.pl?{pacer_case_id}"
    labels = list(extra_labels or [])
    return {
        "docket_id": d["id"],
        "case_name": case_name,
        "docket_number": docket_number,
        "case_type": case_type(docket_number),
        "court_id": court_id,
        "date_filed": d.get("date_filed"),
        "name_label": hit["name_label"],
        "side": hit["side"],
        "labels": labels,
        "cl_url": f"https://www.courtlistener.com{d.get('absolute_url', '')}",
        "pacer_url": pacer_url,
        "captured_at": datetime.now(ET).isoformat(timespec="seconds"),
    }

def append_archive(records):
    with open(ARCHIVE_FILE, "a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

# ── Evaluation ───────────────────────────────────────────────────────────────

def evaluate(d, cfg, today):
    """Classify one docket dict. Returns (status, hit) where status is
    'match' | 'pending' | 'skip'."""
    hit = classify(d.get("case_name") or "", cfg)
    if not hit:
        return "skip", None
    date_filed = d.get("date_filed")
    if date_filed is None:
        return "pending", hit
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=cfg["recency_days"])).strftime("%Y-%m-%d")
    if date_filed >= cutoff:
        return "match", hit
    return "skip", None  # old case newly back-added to CL — not a new filing

def process_pending(state, cfg, today):
    """Re-fetch pending dockets; return newly matched records."""
    pending = state.get("pending", {})
    if not pending:
        return []
    log(f"  re-checking {len(pending)} pending docket(s)")
    matched = []
    still_pending = {}
    for docket_id, info in pending.items():
        try:
            d = api_get(f"{API_BASE}/dockets/{docket_id}/?format=json")
            time.sleep(0.3)
        except Exception as e:
            log(f"  pending re-fetch failed for {docket_id}: {e}")
            still_pending[docket_id] = info
            continue
        if not isinstance(d, dict) or "id" not in d:
            log(f"  pending {docket_id}: unexpected response, keeping")
            still_pending[docket_id] = info
            continue
        status, hit = evaluate(d, cfg, today)
        if status == "match":
            matched.append(build_record(d, hit))
            log(f"  pending resolved -> MATCH: {d.get('case_name','')[:70]}")
        elif status == "pending":
            age = (datetime.strptime(today, "%Y-%m-%d")
                   - datetime.strptime(info["first_seen"], "%Y-%m-%d")).days
            if age >= cfg["pending_max_days"]:
                matched.append(build_record(d, hit, extra_labels=["date_filed pending — metadata not yet populated"]))
                log(f"  pending aged out -> ALERT anyway: {d.get('case_name','')[:70]}")
            else:
                still_pending[docket_id] = info
        else:
            log(f"  pending dropped (old or no longer matches): {d.get('case_name','')[:70]}")
    state["pending"] = still_pending
    return matched

# ── Cursor walk ──────────────────────────────────────────────────────────────

def walk_dockets(url, cfg, state, today, page_cap):
    """Walk a dockets listing, evaluating each. Returns (matches, max_seen_id, capped)."""
    alerted = state.setdefault("alerted_ids", {})
    pending = state.setdefault("pending", {})
    matches = []
    max_seen_id = state.get("last_max_id", 0)
    pages = total = 0
    capped = False

    while url:
        data = api_get(url)
        pages += 1
        results = data.get("results", [])
        if not results:
            break
        for d in results:
            total += 1
            max_seen_id = max(max_seen_id, d["id"])
            key = str(d["id"])
            if key in alerted or key in pending:
                continue
            status, hit = evaluate(d, cfg, today)
            if status == "match":
                matches.append(build_record(d, hit))
                log(f"  + MATCH: {d.get('court_id')} | {d.get('docket_number')} | {HTML_RE.sub('', d.get('case_name') or '')[:70]}")
            elif status == "pending":
                pending[key] = {
                    "first_seen": today,
                    "case_name": HTML_RE.sub("", d.get("case_name") or "")[:120],
                    "court_id": d.get("court_id") or "",
                }
                log(f"  ? PENDING (no date_filed yet): {d.get('court_id')} | {d.get('case_name','')[:70]}")
        url = data.get("next")
        if url and pages >= page_cap:
            capped = True
            log(f"  CURSOR CAP hit at {pages} pages — backlog resumes next run")
            break
        time.sleep(0.5)

    log(f"  walked {pages} pages, {total} dockets, {len(matches)} matches, {len(pending)} pending")
    return matches, max_seen_id, capped

# ── Digest ───────────────────────────────────────────────────────────────────

def send_digest(records, live):
    if not records:
        log("  no new matches — no email")
        return
    subject = subject_barnett_alert(records)
    if not live:
        log(f"  [dry-run] would email: {subject}")
        return
    try:
        send_email(subject, body_barnett_digest(records), log_fn=log)
        log(f"  digest email sent ({len(records)} case(s))")
    except Exception as e:
        log(f"  EMAIL FAILED (cases are archived in matched_cases.jsonl): {e}")

# ── Modes ────────────────────────────────────────────────────────────────────

def finish_run(state, matches, max_seen_id, live, today):
    alerted = state.setdefault("alerted_ids", {})
    # Prune alerted_ids older than 14 days
    prune_cutoff = (datetime.now(ET) - timedelta(days=14)).strftime("%Y-%m-%d")
    state["alerted_ids"] = {k: v for k, v in alerted.items() if v >= prune_cutoff}
    for r in matches:
        state["alerted_ids"][str(r["docket_id"])] = today
    state["last_max_id"] = max_seen_id
    state["last_run"] = datetime.now(ET).isoformat(timespec="seconds")
    if matches:
        append_archive(matches)
    # State saved BEFORE email: an SMTP failure loses at most one digest,
    # never causes duplicate-alert storms on the next run.
    save_state(state)
    send_digest(matches, live)

def run_poll(live):
    cfg = load_config()
    state = load_state()
    if not state.get("last_max_id"):
        log("ERROR: no last_max_id in scan_state.json — run bootstrap first:")
        log("  python poll_barnett_cases.py bootstrap --hours=48")
        sys.exit(1)
    today = datetime.now(ET).strftime("%Y-%m-%d")
    log(f"=== Poll start (cursor id>{state['last_max_id']}, live={live}) ===")

    matches = process_pending(state, cfg, today)
    url = (f"{API_BASE}/dockets/?court__jurisdiction=FD&id__gt={state['last_max_id']}"
           f"&order_by=id&page_size=20&format=json")
    walked, max_seen_id, _ = walk_dockets(url, cfg, state, today, PAGE_CAP_POLL)
    matches.extend(walked)

    finish_run(state, matches, max_seen_id, live, today)
    log(f"=== Poll done. {len(matches)} new matched case(s). ===\n")

def run_bootstrap(hours, live):
    cfg = load_config()
    state = load_state()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:00Z")
    log(f"=== Bootstrap start (date_created >= {since}, live={live}) ===")

    url = (f"{API_BASE}/dockets/?court__jurisdiction=FD&date_created__gte={since}"
           f"&order_by=id&page_size=20&format=json")
    matches, max_seen_id, capped = walk_dockets(url, cfg, state, today, PAGE_CAP_BOOTSTRAP)
    if capped:
        log("  WARNING: bootstrap hit page cap — reduce --hours or raise PAGE_CAP_BOOTSTRAP")

    finish_run(state, matches, max_seen_id, live, today)
    log(f"=== Bootstrap done. {len(matches)} match(es), cursor set to {max_seen_id}. ===\n")

# ── Pattern regression fixtures ──────────────────────────────────────────────

FIXTURES = [
    # (case_name, docket_number, expect_match, expect_side)
    ("USA v. Christopher Barnett", "2:26-cr-00123", True, "defendant"),
    ("United States of America v. Christopher M. Barnett", "1:26-cr-00044", True, "defendant"),
    ("Christopher Barnett v. City of Philadelphia", "2:26-cv-04706", True, "plaintiff"),
    ("Christopher M Barnett v. Warden", "3:26-cv-00099", True, "plaintiff"),
    ("Barnett, Christopher M. v. Acme Corp", "1:26-cv-00350", True, "plaintiff"),
    ("Smith v. Christopher M. Barnett, et al.", "1:26-cv-00351", True, "defendant"),
    ("In re Christopher Barnett", "1:26-mc-00007", True, "caption"),
    # Must NOT match:
    ("USA v. Christopher Barnes", "1:26-cr-00005", False, None),        # Barnes, not Barnett
    ("Michelle Barnett v. Landlord LLC", "1:26-cv-00006", False, None), # wrong first name
    ("Christopher Barnett-Smith Trust v. Bank", "1:26-cv-00008", True, "plaintiff"),  # hyphen after Barnett is a word-boundary; still Christopher Barnett
    ("Christina Barnett v. Employer", "1:26-cv-00009", False, None),    # Christina, not Christopher
]

def run_test_patterns():
    cfg = load_config()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    failures = 0
    for case_name, dn, expect_match, expect_side in FIXTURES:
        d = {"id": 0, "case_name": case_name, "docket_number": dn, "date_filed": today}
        status, hit = evaluate(d, cfg, today)
        got_match = status == "match"
        got_side = hit["side"] if hit else None
        ok = got_match == expect_match and (not expect_match or got_side == expect_side)
        print(f"{'PASS' if ok else 'FAIL'}  match={got_match!s:5}  side={str(got_side):10}  {case_name[:60]}")
        if not ok:
            failures += 1
    print(f"\n{len(FIXTURES) - failures}/{len(FIXTURES)} fixtures passed")
    sys.exit(1 if failures else 0)

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    mode = args[0] if args else "poll"
    live = "--live" in args

    if mode == "test-patterns":
        run_test_patterns()
        return

    if not TOKEN:
        log("ERROR: CL_TOKEN (or COURTLISTENER_TOKEN) not set")
        sys.exit(1)

    if mode == "poll":
        run_poll(live)
    elif mode == "bootstrap":
        hours = 48
        for a in args:
            if a.startswith("--hours="):
                hours = int(a.split("=", 1)[1])
        run_bootstrap(hours, live)
    else:
        print(f"Unknown mode: {mode}. Use 'poll', 'bootstrap' or 'test-patterns'.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
