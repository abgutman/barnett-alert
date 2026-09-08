# barnett-alert

Email alerts for **new federal cases naming a watched person as a party**, across all 94 federal district courts. Default watch name: **Christopher Barnett** (also *Christopher M. Barnett* / *Christopher M Barnett*). Email-only — no dashboard, no Pages.

Built with Claude (Anthropic AI) for a newsroom court-monitoring workflow.

## How it works

1. A GitHub Actions cron runs `poll_barnett_cases.py poll --live` on a schedule (below).
2. The script asks CourtListener's v4 API for every federal district docket created since the last run, using an `id` cursor (`/dockets/?court__jurisdiction=FD&id__gt=<last_max_id>&order_by=id`).
3. Each docket is kept if:
   - the **case caption** (`case_name`) matches a name pattern in `watch_config.json` and no exclusion pattern; and
   - `date_filed` is within `recency_days` (default 7) — CourtListener constantly back-adds old cases, and those are not new filings.
   - **All case types are included** — civil, criminal, magistrate, misc. Each match is labeled with its type and with which side of the caption the name is on (plaintiff / defendant).
4. Dockets that match but have **no `date_filed` yet** go into a pending queue (`scan_state.json`) and are re-checked each run; after `pending_max_days` (default 3) they alert anyway, labeled "date_filed pending."
5. Matches are appended to `matched_cases.jsonl` (provenance archive) and emailed as **one digest per run**. No matches → no email.

State (`scan_state.json`), the archive, and the run log are committed back to this repo by the workflow so state persists across runs.

## Accuracy notes — read before reporting

Detection is **case-caption based**, which has two important limits:

- **Common name.** "Christopher Barnett" is not unique. Across 94 districts, unrelated people share the name. **Every hit is a triage lead to confirm against the official docket, not a positive identification.** Add a district or other narrowing detail to `watch_config.json` if the noise is high.
- **Co-parties hidden by "et al."** A captured caption shows only the lead parties. If the watched person is, say, the third co-defendant in "USA v. Smith, et al.", the name is not in the caption and the case is missed. The full party list is only in the PACER docket report, which this tool does not purchase.

**Always confirm details against the official docket before reporting.**

## Schedule (GitHub Actions, UTC crons)

- `19,49 11-23 * * *` — every 30 minutes, 7:19 a.m.–7:49 p.m. ET
- `19 1,3,5,7,9 * * *` — every 2 hours overnight (9:19 p.m., 11:19 p.m., 1:19 a.m., 3:19 a.m., 5:19 a.m. ET)

**DST caveat:** crons are tuned for EDT (UTC-4). When EST returns in November, everything lands one hour later in ET; edit the cron hours by -1 if that matters. GitHub also delays crons by 5–20 minutes under load — harmless here, the id cursor never loses cases to timing.

## Tuning the watch list

Edit `watch_config.json` — no code changes needed:

- `names`: case-insensitive regexes + labels for people/entities to watch. Add more names to watch several people with one poller.
- `exclude`: checked **first**; hard-rejects known lookalikes.
- `recency_days` / `pending_max_days`: window definitions.

After editing, run the regression check (also useful in CI):

```
python poll_barnett_cases.py test-patterns
```

It verifies true positives (USA v. Christopher Barnett; Barnett as plaintiff; "Barnett, Christopher"; buried in "et al."), the wrong-first-name and wrong-surname rejections, and side detection. Nonzero exit on any failure.

## Running locally

```
export CL_TOKEN=...                              # courtlistener.com API token
python poll_barnett_cases.py bootstrap --hours=48   # first run: seeds the id cursor, dry-run
python poll_barnett_cases.py poll                   # incremental, log-only
python poll_barnett_cases.py poll --live            # incremental + email digest
```

`poll` refuses to run without a cursor — recover a lost/corrupt `scan_state.json` with `bootstrap`. Email needs `GMAIL_USER`, `GMAIL_APP_PASSWORD` (Gmail app password), and `ALERT_EMAIL_TO` (recipient, comma-separated) in the environment. No recipient address is stored in source — set `ALERT_EMAIL_TO`; without it the digest is logged, not sent.

## Repo secrets (Settings → Secrets and variables → Actions)

| Secret | Purpose |
|---|---|
| `CL_TOKEN` | CourtListener API auth |
| `GMAIL_USER` | Sending Gmail account |
| `GMAIL_APP_PASSWORD` | Gmail app password (not the account password) |
| `ALERT_EMAIL_TO` | Recipient(s), comma-separated (required — no address is stored in source) |

## Files

| File | Role |
|---|---|
| `poll_barnett_cases.py` | Poller — cursor walk, pending queue, digest (stdlib + curl only) |
| `watch_config.json` | Editable name-matching config |
| `email_utils.py` | Gmail SMTP sender + digest template |
| `scan_state.json` | Cursor, pending queue, alerted ids (committed by the workflow) |
| `matched_cases.jsonl` | Append-only archive of every alerted case |
| `poll_log.txt` | Run log |
| `.github/workflows/barnett-poll.yml` | Cron + manual dispatch (poll / bootstrap) |

## Failure modes

- **CourtListener outage / curl failure:** the run fails red and the cursor doesn't advance; the next run replays the gap. `alerted_ids` (14-day window) prevents duplicate emails on replay.
- **Long outage backlog:** cursor walks are capped at 100 pages/run (~2,000 dockets); the log notes `CURSOR CAP` and the backlog drains across subsequent runs.
- **SMTP failure:** state is saved *before* the email attempt, so a failed send loses at most one digest — the cases are still in `matched_cases.jsonl` and the log.
- **Lost state file:** `poll` exits with instructions to `bootstrap`.

## Sources

Data: [CourtListener](https://www.courtlistener.com) (Free Law Project) v4 REST API, which ingests the federal courts' public PACER RSS feeds and case-number probes. No PACER purchases are made; PACER links in alerts are for manual follow-up (login and fees apply).
