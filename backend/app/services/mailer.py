"""Builds and sends the emails queued in notifications_outbox (AWS SES).

Interview emails carry an iCalendar invite (METHOD:REQUEST / CANCEL) with a stable UID and an
increasing SEQUENCE, so Gmail/Outlook update or remove the same calendar entry on reschedule or
cancellation instead of creating duplicates. All user-provided text is HTML-escaped.
"""
import asyncio
import html
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from ..config import get_settings

_client = None

STATUS_COPY = {
    "SHORTLISTED": ("You've been shortlisted", "Good news: {company} shortlisted you for {job}. They'll be in touch about next steps."),
    "HIRED": ("Welcome aboard", "Congratulations! Your offer for {job} at {company} is confirmed."),
    "REJECTED": ("Update on your application", "Thank you for applying for {job} at {company}. They've decided not to move forward. "
                 "Your dashboard has a breakdown of the skills this role looked for and how to build them."),
}


def _ses():
    global _client
    if _client is None:
        import boto3
        s = get_settings()
        kwargs = {}
        if s.aws_access_key_id and s.aws_secret_access_key:
            kwargs = {"aws_access_key_id": s.aws_access_key_id, "aws_secret_access_key": s.aws_secret_access_key}
        _client = boto3.client("sesv2", region_name=s.aws_region, **kwargs)
    return _client


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ics(row: dict, cancelled: bool) -> bytes:
    from icalendar import Calendar, Event, vCalAddress, vText
    s = get_settings()
    p = row["payload"]
    cal = Calendar()
    cal.add("prodid", "-//Shortlist//Interviews//EN")
    cal.add("version", "2.0")
    cal.add("method", "CANCEL" if cancelled else "REQUEST")
    ev = Event()
    ev.add("uid", f"interview-{row['interview_id']}@shortlist")
    ev.add("sequence", int(p.get("sequence", 0)))
    ev.add("dtstamp", datetime.now().astimezone())
    ev.add("dtstart", _parse_ts(p["scheduled_at"]))
    ev.add("dtend", _parse_ts(p["ends_at"]))
    ev.add("summary", f"Interview: {p.get('job_title')} at {p.get('company_name')}")
    ev.add("description", f"{(p.get('interview_type') or '').title()} interview with {p.get('interviewer_name') or 'the hiring team'}.")
    if p.get("meeting_link"):
        ev.add("location", p["meeting_link"])
    ev.add("status", "CANCELLED" if cancelled else "CONFIRMED")
    organizer = vCalAddress(f"mailto:{s.ses_from_email}")
    organizer.params["cn"] = vText(p.get("company_name") or "Shortlist")
    ev["organizer"] = organizer
    attendee = vCalAddress(f"mailto:{row['recipient_email']}")
    attendee.params["cn"] = vText(p.get("applicant_name") or "")
    attendee.params["role"] = vText("REQ-PARTICIPANT")
    attendee.params["rsvp"] = vText("TRUE")
    ev.add("attendee", attendee, encode=0)
    cal.add_component(ev)
    return cal.to_ical()


def build_message(row: dict) -> MIMEMultipart:
    s = get_settings()
    p = row["payload"]
    e = html.escape
    job, company, name = p.get("job_title", ""), p.get("company_name", ""), p.get("applicant_name", "")
    event = row["event_type"]
    ics: bytes | None = None
    method = "REQUEST"

    if event == "APPLICATION_STATUS_CHANGED":
        subject, body = STATUS_COPY.get(p.get("new_status"), ("Application update", "Your application for {job} at {company} was updated."))
        body = body.format(job=job, company=company)
    elif event in ("INTERVIEW_SCHEDULED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"):
        start = _parse_ts(p["scheduled_at"])
        when = start.strftime("%A %d %B %Y, %H:%M UTC")
        if event == "INTERVIEW_CANCELLED":
            subject, body, method = f"Interview cancelled: {job}", f"Your interview for {job} at {company} on {when} has been cancelled.", "CANCEL"
        else:
            verb = "rescheduled" if event == "INTERVIEW_RESCHEDULED" else "scheduled"
            subject = f"Interview {verb}: {job} at {company}"
            body = f"Your {p.get('duration_minutes')}-minute interview for {job} at {company} is {verb} for {when}."
            if p.get("meeting_link"):
                body += f" Join link: {p['meeting_link']}"
        ics = _ics(row, cancelled=event == "INTERVIEW_CANCELLED")
    elif event == "OFFER_ISSUED":
        subject = f"Offer from {company}: {job}"
        body = (f"{company} has made you an offer for {job}. Sign in to review the details and respond before "
                f"{_parse_ts(p['expires_at']).strftime('%d %B %Y')}.")
    elif event == "REVIEW_RESOLVED":
        outcome_copy = {
            "UPHELD": "The reviewer looked at your request and the decision stands.",
            "RESCREENED": "Your application was screened again using your updated profile.",
            "OVERRIDDEN": "A reviewer adjusted the assessment of your application.",
            "REINSTATED": "Your application is back in the hiring process.",
        }
        subject = f"Your review request: {job} at {company}"
        body = outcome_copy.get(p.get("outcome"), "Your review request was resolved.")
        if p.get("note"):
            body += f" Reviewer's note: {p['note']}"
    elif event == "FEEDBACK_READY":
        subject, body = f"Feedback on your {job} application", "Personalised suggestions for this role are ready in your dashboard."
    else:
        raise ValueError(f"unknown event {event}")

    greeting = f"Hi {name}," if name else "Hi,"
    text = f"{greeting}\n\n{body}\n\n- {company or 'Shortlist'}"
    markup = (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;line-height:1.6;color:#1c1c1c">'
              f"<p>{e(greeting)}</p><p>{e(body)}</p><p style=\"color:#666\">- {e(company or 'Shortlist')}</p></div>")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Shortlist", s.ses_from_email))
    msg["To"] = row["recipient_email"]
    msg["Message-ID"] = make_msgid(domain="shortlist")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text, "plain", "utf-8"))
    alt.attach(MIMEText(markup, "html", "utf-8"))
    if ics:
        cal_part = MIMEText(ics.decode(), "calendar", "utf-8")
        cal_part.set_param("method", method)
        alt.attach(cal_part)
    msg.attach(alt)
    if ics:
        attachment = MIMEApplication(ics, "ics")
        attachment.add_header("Content-Disposition", "attachment", filename="invite.ics")
        msg.attach(attachment)
    return msg


async def send(row: dict) -> None:
    s = get_settings()
    raw = build_message(row).as_bytes()
    kwargs = {"Content": {"Raw": {"Data": raw}}}
    if s.ses_configuration_set:
        kwargs["ConfigurationSetName"] = s.ses_configuration_set
    await asyncio.to_thread(_ses().send_email, **kwargs)
