"""Send approved RFQs and parse the replies.

Two hard rules, both enforced in code rather than by convention:

  1. send() refuses anything whose status is not 'approved'. There is no
     parameter that overrides this and no other function that sends.
  2. Replies are matched to a request by the reference code in the subject,
     never by guessing from the sender address.
"""

from __future__ import annotations

import email
import imaplib
import re
import smtplib
from email.message import EmailMessage
from typing import Any

from app import llm
from app.config import get_settings
from app.db.database import session
from app.sources.models import ExtractedOffer

REF_PATTERN = re.compile(r"\[?(SD-[0-9A-F]{6})\]?", re.IGNORECASE)


class NotApproved(RuntimeError):
    """Raised when something tries to send an unapproved draft."""


def send(rfq_id: int, to_address: str | None = None, *, dry_run: bool = False) -> dict:
    settings = get_settings()

    with session() as conn:
        row = conn.execute(
            "SELECT q.*, s.name AS supplier, s.contact_email FROM rfqs q "
            "LEFT JOIN suppliers s ON s.id = q.supplier_id WHERE q.id = ?",
            (rfq_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"no rfq #{rfq_id}")

    # The gate. Not a warning, not a flag -- a refusal.
    if row["status"] != "approved":
        raise NotApproved(
            f"rfq #{rfq_id} is '{row['status']}', not 'approved'. "
            "Every email is reviewed before it reaches a supplier."
        )

    recipient = to_address or row["contact_email"]
    if not recipient:
        raise ValueError(
            f"no email address for {row['supplier']}. "
            f"Set one with: rfq.py address <supplier_id> <email>"
        )

    message = EmailMessage()
    message["From"] = settings.email_address
    message["To"] = recipient
    message["Subject"] = row["subject"]
    message.set_content(row["body"])

    if dry_run:
        return {"rfq_id": rfq_id, "to": recipient, "sent": False,
                "note": "dry run, nothing left this machine"}

    if not settings.email_address or not settings.email_password:
        raise RuntimeError("EMAIL_ADDRESS / EMAIL_PASSWORD not set in .env")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.email_address, settings.email_password)
        server.send_message(message)

    with session() as conn:
        conn.execute(
            "UPDATE rfqs SET status = 'sent', sent_at = datetime('now') WHERE id = ?",
            (rfq_id,),
        )
    return {"rfq_id": rfq_id, "to": recipient, "sent": True}


# ------------------------------------------------------------- replies ------

PARSE_SYSTEM = """You extract a supplier quotation from an email reply.

The supplier is usually writing in a second language and may state terms in
passing ("FOB Shenzhen, T/T 30% deposit, 15 days"). Prices may be per unit or
per lot; report per unit and note it if you had to divide.

Report the currency the supplier used. Never convert. Use -1 for numbers they
did not state and '' for text they did not state. Set confidence honestly --
a clearly stated price at our quantity is high, an inferred one is low."""


def parse_reply(body: str, mpn: str, quantity: int) -> ExtractedOffer | None:
    try:
        offer = llm.structured(
            f"Part: {mpn}\nOur quantity: {quantity}\n\n"
            f"--- supplier reply ---\n{body[:8000]}\n--- end ---",
            ExtractedOffer,
            system=PARSE_SYSTEM,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ! reply parse failed: {str(exc)[:120]}")
        return None
    return offer if offer.is_offer else None


def fetch_replies(folder: str = "INBOX", limit: int = 25) -> list[dict[str, Any]]:
    """Read unseen mail, match by reference code, parse into offers."""
    settings = get_settings()
    if not settings.email_address or not settings.email_password:
        raise RuntimeError("EMAIL_ADDRESS / EMAIL_PASSWORD not set in .env")

    results: list[dict[str, Any]] = []

    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.email_address, settings.email_password)
        imap.select(folder)
        _, data = imap.search(None, "UNSEEN")
        ids = data[0].split()[-limit:]

        for message_id in ids:
            _, payload = imap.fetch(message_id, "(RFC822)")
            message = email.message_from_bytes(payload[0][1])
            subject = str(email.header.make_header(
                email.header.decode_header(message.get("Subject", ""))
            ))

            match = REF_PATTERN.search(subject)
            if not match:
                continue  # not a reply to one of ours
            ref_code = match.group(1).upper()

            body = _plain_text(message)
            results.append(_record_reply(ref_code, body, message.get("From", "")))

    return [r for r in results if r]


def _plain_text(message) -> str:
    if not message.is_multipart():
        return message.get_payload(decode=True).decode("utf-8", errors="replace")
    for part in message.walk():
        if part.get_content_type() == "text/plain":
            return part.get_payload(decode=True).decode("utf-8", errors="replace")
    return ""


def _record_reply(ref_code: str, body: str, sender: str) -> dict[str, Any] | None:
    with session() as conn:
        rfq = conn.execute(
            "SELECT q.*, r.quantity, p.mpn, p.id AS part_id FROM rfqs q "
            "JOIN requests r ON r.id = q.request_id "
            "LEFT JOIN parts p ON p.id = r.part_id WHERE q.ref_code = ?",
            (ref_code,),
        ).fetchone()
    if rfq is None:
        return None

    offer = parse_reply(body, rfq["mpn"] or "", rfq["quantity"] or 1)
    if offer is None:
        with session() as conn:
            conn.execute(
                "UPDATE rfqs SET status = 'replied', replied_at = datetime('now') "
                "WHERE id = ?", (rfq["id"],),
            )
        return {"ref_code": ref_code, "from": sender, "parsed": False}

    price = offer.unit_price if offer.unit_price >= 0 else None
    with session() as conn:
        offer_id = conn.execute(
            """
            INSERT INTO offers (request_id, supplier_id, part_id, quantity,
                unit_price, currency, unit_price_original, currency_original,
                incoterm, moq, lead_days, warranty_months, url, source_kind,
                rung, confidence, state)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'email','rfq',?,?)
            """,
            (
                rfq["request_id"], rfq["supplier_id"], rfq["part_id"],
                rfq["quantity"], price, (offer.currency or "USD").upper() if price else None,
                price, (offer.currency or "USD").upper() if price else None,
                offer.incoterm or None,
                offer.moq if offer.moq > 0 else None,
                offer.lead_days if offer.lead_days > 0 else None,
                offer.warranty_months if getattr(offer, "warranty_months", -1) > 0 else None,
                f"email reply {ref_code}",
                offer.confidence,
                # A quote addressed to us at our quantity is better evidence
                # than a public listing.
                "verified" if price else "needs_rfq",
            ),
        ).lastrowid

        conn.execute(
            "UPDATE rfqs SET status = 'replied', replied_at = datetime('now') "
            "WHERE id = ?", (rfq["id"],),
        )

        if offer.weight_kg > 0 and rfq["part_id"]:
            conn.execute(
                "UPDATE parts SET weight_kg = ?, weight_source = 'supplier' "
                "WHERE id = ?", (offer.weight_kg, rfq["part_id"]),
            )

    return {
        "ref_code": ref_code, "from": sender, "parsed": True,
        "offer_id": offer_id, "supplier": rfq["supplier_id"],
        "unit_price": offer.unit_price, "currency": offer.currency,
        "incoterm": offer.incoterm, "lead_days": offer.lead_days,
        "weight_kg": offer.weight_kg, "confidence": offer.confidence,
    }
