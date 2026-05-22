#!/usr/bin/env python3
"""Send EOD analyze summary via Gmail SMTP (App Password). Stdlib only."""

from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _load_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "runs": [],
            "failure_count": 1,
            "note": f"Summary file missing: {path}",
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_lines(lines: list[Any] | None, limit: int = 6) -> str:
    if not lines:
        return "—"
    out = []
    for line in lines[:limit]:
        s = str(line).strip()
        if s:
            out.append(s)
    if len(lines) > limit:
        out.append(f"… (+{len(lines) - limit} more)")
    return "\n".join(out) if out else "—"


def _build_bodies(summary: dict[str, Any], workflow_status: str) -> tuple[str, str, str]:
    runs: list[dict[str, Any]] = list(summary.get("runs") or [])
    failure_count = int(summary.get("failure_count") or 0)
    n_fail = sum(1 for r in runs if r.get("status") not in ("ok", None) or r.get("trigger_ok") is False)
    if failure_count:
        n_fail = max(n_fail, failure_count)

    dates = [r.get("session_date") for r in runs if r.get("session_date")]
    session_hint = dates[0] if dates else "—"

    if workflow_status != "success" or n_fail > 0:
        subject_status = f"FAILED ({n_fail})" if n_fail else "WORKFLOW FAILED"
    else:
        subject_status = "OK"

    subject = f"[Stock EOD] {subject_status} — session {session_hint}"

    repo = _env("GITHUB_REPOSITORY")
    run_id = _env("GITHUB_RUN_ID")
    server = _env("GITHUB_SERVER_URL", "https://github.com")
    workflow_link = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""

    started = summary.get("started_at", "—")
    finished = summary.get("finished_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    api_url = summary.get("api_url", "—")

    text_parts = [
        "Paper sandbox — EOD analyze report",
        "",
        f"Workflow: {workflow_status}",
        f"API: {api_url}",
        f"Started (UTC): {started}",
        f"Finished (UTC): {finished}",
        "",
    ]
    if workflow_link:
        text_parts.extend([f"GitHub run: {workflow_link}", ""])

    html_rows = []
    for r in runs:
        slug = r.get("slug", "?")
        status = r.get("status", "unknown")
        skipped = r.get("skipped")
        session = r.get("session_date") or "—"
        equity = r.get("equity")
        line_count = r.get("line_count")
        err = r.get("error_message") or r.get("error") or ""
        sample = _fmt_lines(r.get("sample_lines"))

        skip_txt = "yes" if skipped else ("no" if skipped is False else "—")
        eq_txt = f"{equity:,.2f}" if isinstance(equity, (int, float)) else (str(equity) if equity else "—")

        text_parts.append(f"## {slug}")
        text_parts.append(f"  status: {status}")
        text_parts.append(f"  session_date: {session}")
        text_parts.append(f"  skipped: {skip_txt}")
        text_parts.append(f"  equity: {eq_txt}")
        if line_count is not None:
            text_parts.append(f"  log lines: {line_count}")
        if err:
            text_parts.append(f"  error: {err}")
        if sample != "—":
            text_parts.append("  excerpt:")
            for ln in sample.split("\n"):
                text_parts.append(f"    {ln}")
        text_parts.append("")

        status_color = "#15803d" if status == "ok" else "#b91c1c"
        html_rows.append(
            f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #e5e7eb;"><strong>{slug}</strong></td>
              <td style="padding:8px;border-bottom:1px solid #e5e7eb;color:{status_color}">{status}</td>
              <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{session}</td>
              <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{skip_txt}</td>
              <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{eq_txt}</td>
            </tr>
            <tr>
              <td colspan="5" style="padding:8px 8px 16px;border-bottom:1px solid #e5e7eb;">
                <pre style="margin:0;font-size:12px;white-space:pre-wrap;background:#f9fafb;padding:8px;border-radius:6px;">{sample}</pre>
                {f'<p style="color:#b91c1c;font-size:12px;">{err}</p>' if err else ''}
              </td>
            </tr>"""
        )

    note = summary.get("note")
    if note:
        text_parts.append(f"Note: {note}")

    text_body = "\n".join(text_parts)

    html_body = f"""
    <html><body style="font-family:system-ui,sans-serif;color:#111827;max-width:720px;">
      <h2 style="margin:0 0 8px;">EOD analyze — {subject_status}</h2>
      <p style="color:#4b5563;margin:0 0 16px;">
        Workflow: <strong>{workflow_status}</strong><br>
        API: {api_url}<br>
        UTC: {started} → {finished}
      </p>
      {f'<p><a href="{workflow_link}">View GitHub Actions run</a></p>' if workflow_link else ''}
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="background:#f3f4f6;text-align:left;">
            <th style="padding:8px;">Algo</th>
            <th style="padding:8px;">Status</th>
            <th style="padding:8px;">Session</th>
            <th style="padding:8px;">Skipped</th>
            <th style="padding:8px;">Equity</th>
          </tr>
        </thead>
        <tbody>
          {''.join(html_rows) if html_rows else '<tr><td colspan="5" style="padding:12px;">No run details recorded.</td></tr>'}
        </tbody>
      </table>
      <p style="color:#6b7280;font-size:12px;margin-top:24px;">Automated message from stock EOD cron.</p>
    </body></html>
    """

    return subject, text_body, html_body


def main() -> int:
    gmail_user = _env("GMAIL_USER")
    gmail_pass = _env("GMAIL_APP_PASSWORD")
    mail_to = _env("MAIL_TO") or gmail_user

    if not gmail_user or not gmail_pass:
        print("GMAIL_USER / GMAIL_APP_PASSWORD not set; skipping email.", file=sys.stderr)
        return 0

    if not mail_to:
        print("MAIL_TO not set; skipping email.", file=sys.stderr)
        return 0

    summary_path = Path(_env("EOD_SUMMARY_FILE", "eod-summary.json"))
    workflow_status = _env("WORKFLOW_STATUS", "unknown")
    summary = _load_summary(summary_path)
    summary.setdefault("finished_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    subject, text_body, html_body = _build_bodies(summary, workflow_status)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = mail_to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    print(f"Sending email to {mail_to} …")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(gmail_user, gmail_pass)
        smtp.sendmail(gmail_user, [a.strip() for a in mail_to.split(",") if a.strip()], msg.as_string())

    print("Email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
