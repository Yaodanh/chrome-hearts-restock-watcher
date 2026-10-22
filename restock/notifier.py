"""Sends alerts by email."""
import logging
import smtplib
from email.message import EmailMessage
from typing import List, Protocol

from .models import Alert, Event

log = logging.getLogger(__name__)

# Most urgent first: a category going live means a pop-up just opened.
ORDER = [Event.CATEGORY_LIVE, Event.NEW_PRODUCT, Event.RESTOCK]


class Notifier(Protocol):
    def send(self, alerts: List[Alert]) -> None:
        ...


def build_message(alerts: List[Alert], sender: str, recipient: str) -> EmailMessage:
    events = [e for e in ORDER if any(a.event is e for a in alerts)]
    headline = events[0].value
    names = ", ".join(a.item.name for a in alerts if a.item.kind == "product") or \
        ", ".join(a.item.name for a in alerts)

    msg = EmailMessage()
    msg["Subject"] = f"[Chrome Hearts] {headline}: {names}"[:150]
    msg["From"] = sender
    msg["To"] = recipient

    lines = []
    for event in events:
        lines += [f"{event.value}:", ""]
        for a in (a for a in alerts if a.event is event):
            price = f" ({a.item.price})" if a.item.price else ""
            lines.append(f"- {a.item.name}{price}")
            if a.item.url:
                lines.append(f"  {a.item.url}")
        lines.append("")
    lines.append("Sent by restock-watcher.")
    msg.set_content("\n".join(lines))
    return msg


class EmailNotifier:
    """SMTP over SSL. Works with Gmail (app password) or 163 mail (authorization code)."""

    def __init__(self, host: str, port: int, username: str, password: str,
                 sender: str, recipient: str, smtp_factory=smtplib.SMTP_SSL):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.sender, self.recipient = sender, recipient
        self.smtp_factory = smtp_factory

    def send(self, alerts: List[Alert]) -> None:
        if not alerts:
            return
        msg = build_message(alerts, self.sender, self.recipient)
        with self.smtp_factory(self.host, self.port, timeout=30) as smtp:
            smtp.login(self.username, self.password)
            smtp.send_message(msg)
        log.info("Emailed %d alert(s) to %s", len(alerts), self.recipient)


class ConsoleNotifier:
    """Prints the email instead of sending it (dry run / demo)."""

    def send(self, alerts: List[Alert]) -> None:
        if not alerts:
            return
        print(build_message(alerts, "restock-watcher", "you").as_string())
