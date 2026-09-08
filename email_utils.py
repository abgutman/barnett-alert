#!/usr/bin/env python3
"""Email sender + templates for barnett-alert.

Trimmed vendored copy of the shared claude_sandbox email_utils.py (same
send_email / styling), carrying the Barnett party-alert digest templates.

TEMPLATES section: edit subject/body functions below to change email text.
Recipient list comes from the ALERT_EMAIL_TO env var (comma-separated) so this
public repo carries no personal address in source. Set it as an Actions secret.
"""
import os, smtplib, ssl
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

EMAIL_TO = [e.strip() for e in os.environ.get("ALERT_EMAIL_TO", "").split(",") if e.strip()]
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

ET = timezone(timedelta(hours=-4))  # EDT (UTC-4)

ACCENT = "#4a2e6b"  # deep indigo — distinct from the gov-plaintiff navy

CASE_TYPE_COLOR = {
    "civil": "#2c6e49",
    "criminal": "#9d2b2b",
    "magistrate": "#6b5b2e",
    "misc": "#3d5a80",
    "other": "#555a63",
}


# ── TEMPLATES ─────────────────────────────────────────────────────────────────

def subject_barnett_alert(records):
    n = len(records)
    first = records[0]["case_name"]
    if len(first) > 60:
        first = first[:57] + "…"
    extra = f" +{n - 1} more" if n > 1 else ""
    return f"\U0001f50e Barnett alert: {n} new federal case{'s' if n > 1 else ''}: {first}{extra}"  # 🔎


def _badge(text, bg="#8e6a00"):
    return (f'<span style="display:inline-block;background:{bg};color:white;'
            f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;'
            f'margin:2px 6px 2px 0;">{text}</span>')


def _case_block(r):
    links = f'<a href="{r["cl_url"]}" style="color:{ACCENT};font-weight:600;text-decoration:none;">CourtListener &rarr;</a>'
    if r.get("pacer_url"):
        links += (f' &nbsp;&middot;&nbsp; <a href="{r["pacer_url"]}" style="color:{ACCENT};'
                  f'font-weight:600;text-decoration:none;">PACER &rarr;</a>'
                  f' <span style="color:#adb5bd;font-size:11px;">(PACER login &amp; fees apply)</span>')
    ct = r.get("case_type", "other")
    side = r.get("side", "caption")
    badges = _badge(r["name_label"], bg=ACCENT)
    badges += _badge(ct.capitalize(), bg=CASE_TYPE_COLOR.get(ct, "#555a63"))
    if side in ("plaintiff", "defendant"):
        badges += _badge(f"named as {side}", bg="#495057")
    for lbl in r.get("labels", []):
        badges += _badge(lbl)
    date_filed = r.get("date_filed") or "not yet listed"
    return f"""
    <div style="border-top:1px solid #f0f0f0;padding:16px 0;">
      <p style="margin:0 0 6px;color:#1a1a2e;font-size:15px;font-weight:700;line-height:1.35;">{r['case_name']}</p>
      <p style="margin:0 0 8px;">{badges}</p>
      <p style="margin:0 0 4px;color:#495057;font-size:13.5px;">
        <strong>{r['court_id'].upper()}</strong> &nbsp;&middot;&nbsp; {r['docket_number']} &nbsp;&middot;&nbsp; filed {date_filed}
      </p>
      <p style="margin:6px 0 0;font-size:13.5px;">{links}</p>
    </div>"""


def body_barnett_digest(records):
    n = len(records)
    blocks = "\n".join(_case_block(r) for r in records)
    run_time = datetime.now(ET).strftime("%b %-d, %Y at %-I:%M %p ET")
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px 16px;background:#eef0f3;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Helvetica,Arial,sans-serif;">
<div style="max-width:580px;margin:0 auto;">

  <div style="background:{ACCENT};padding:28px 32px;border-radius:10px 10px 0 0;">
    <p style="margin:0 0 8px;color:rgba(255,255,255,0.6);font-size:11px;text-transform:uppercase;letter-spacing:1.5px;">\U0001f50e Barnett Party Alert</p>
    <h1 style="margin:0 0 8px;color:white;font-size:24px;font-weight:700;line-height:1.2;">{n} new federal case{'s' if n > 1 else ''}</h1>
    <p style="margin:0;color:rgba(255,255,255,0.9);font-size:15px;font-weight:500;">Christopher Barnett named as a party &middot; all districts &middot; {run_time}</p>
  </div>

  <div style="background:white;padding:24px 32px;">
    <p style="margin:0 0 12px;color:#495057;font-size:13.5px;line-height:1.7;padding:14px 18px;background:#f8f9fa;border-left:4px solid {ACCENT};border-radius:0 6px 6px 0;">
      This alert was generated automatically by <strong>Claude (Anthropic AI)</strong>, which
      monitors CourtListener for newly filed federal district cases whose caption names a
      watched person, at Av&#8217;s request. Matching is by name in the case caption &mdash;
      <strong>&ldquo;Christopher Barnett&rdquo; is a common name, so each hit is a lead to verify,
      not a confirmed identity.</strong> Check the official docket before reporting; a person
      buried under &ldquo;et al.&rdquo; may not appear in the caption at all.
    </p>
{blocks}
    <p style="margin:16px 0 0;font-size:13px;color:#868e96;">If you have any questions, comments, or concerns, reach out to Av.</p>
  </div>

  <div style="background:#f8f9fa;padding:16px 32px;border-top:1px solid #e9ecef;border-radius:0 0 10px 10px;">
    <p style="margin:0;font-size:12px;color:#adb5bd;line-height:1.6;">
      Newsroom court monitor &middot; Built with <a href="https://claude.ai" style="color:#adb5bd;">Claude</a> (Anthropic AI)<br>
      Source: CourtListener / Free Law Project &mdash; <a href='https://www.courtlistener.com' style='color:#adb5bd;'>courtlistener.com</a>
    </p>
  </div>

</div>
</body>
</html>"""


# ── Sender ────────────────────────────────────────────────────────────────────

def send_email(subject, body, log_fn=None, to=None):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        if log_fn:
            log_fn(f"⚠ No Gmail creds; would have sent: {subject}")
        return False
    recipients = to if to is not None else EMAIL_TO
    if isinstance(recipients, str):
        recipients = [recipients]
    if not recipients:
        if log_fn:
            log_fn(f"⚠ No recipients (set ALERT_EMAIL_TO); would have sent: {subject}")
        return False
    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_USER, recipients, msg.as_string())
    return True
