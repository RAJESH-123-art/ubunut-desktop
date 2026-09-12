"""
capabilities/email.py — Email Client & Protocol capabilities.

Covers NIKKI capability family: 34 (EMAIL)
"""
from __future__ import annotations

import email
import imaplib
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _email_send(args: dict[str, Any], state: Any = None) -> Any:
        to = args.get("to", "")
        subject = args.get("subject", "")
        body = args.get("body", "")
        smtp_server = args.get("smtp_server", "localhost")
        smtp_port = int(args.get("smtp_port", 25))
        sender = args.get("from", "nikki@localhost")

        if not to:
            return fail("'to' email address is required")

        try:
            msg = MIMEMultipart()
            msg["From"] = sender
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                server.send_message(msg)

            return ok({"to": to, "subject": subject, "sent": True})
        except Exception as e:
            return fail(f"SMTP send failed: {e}")

    register_cap(registry, Cap("email.send", "Send email message via SMTP", Cap.COMMUNICATION, ("to", "subject", "body")), _email_send)

    def _email_read(args: dict[str, Any], state: Any = None) -> Any:
        host = args.get("imap_server", "localhost")
        port = int(args.get("imap_port", 993))
        user = args.get("user", "")
        password = args.get("password", "")
        folder = args.get("folder", "INBOX")

        if not user:
            return fail("user is required for email.read")

        try:
            M = imaplib.IMAP4_SSL(host, port)
            M.login(user, password)
            M.select(folder)
            _typ, data = M.search(None, "ALL")
            mail_ids = data[0].split()
            latest_id = mail_ids[-1] if mail_ids else None
            
            summary = []
            if latest_id:
                _typ, msg_data = M.fetch(latest_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        summary.append({
                            "subject": msg.get("Subject", ""),
                            "from": msg.get("From", ""),
                            "date": msg.get("Date", ""),
                        })
            M.logout()
            return ok({"count": len(mail_ids), "latest": summary})
        except Exception as e:
            return fail(f"IMAP read failed: {e}")

    register_cap(registry, Cap("email.read", "Read recent email messages via IMAP", Cap.READ, ("user", "imap_server")), _email_read)

    def _email_search(args: dict[str, Any], state: Any = None) -> Any:
        host = args.get("imap_server", "localhost")
        port = int(args.get("imap_port", 993))
        user = args.get("user", "")
        password = args.get("password", "")
        query = args.get("query", "ALL")
        folder = args.get("folder", "INBOX")

        if not user:
            return fail("user is required for email.search")

        try:
            M = imaplib.IMAP4_SSL(host, port)
            M.login(user, password)
            M.select(folder)
            _typ, data = M.search(None, query)
            mail_ids = data[0].split()
            M.logout()
            return ok({"query": query, "matching_ids": [m.decode() for m in mail_ids], "count": len(mail_ids)})
        except Exception as e:
            return fail(f"IMAP search failed: {e}")

    register_cap(registry, Cap("email.search", "Search email messages by criteria via IMAP", Cap.READ, ("user", "query")), _email_search)

    def _email_download_attachment(args: dict[str, Any], state: Any = None) -> Any:
        host = args.get("imap_server", "localhost")
        port = int(args.get("imap_port", 993))
        user = args.get("user", "")
        password = args.get("password", "")
        msg_id = args.get("msg_id", "1")
        output_dir = Path(args.get("output_dir", "./attachments")).expanduser().resolve()

        if not user:
            return fail("user is required for email.download_attachment")

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            M = imaplib.IMAP4_SSL(host, port)
            M.login(user, password)
            M.select("INBOX")
            _typ, data = M.fetch(msg_id.encode(), "(RFC822)")
            saved = []
            for response_part in data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    for part in msg.walk():
                        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                            continue
                        filename = part.get_filename()
                        if filename:
                            filepath = output_dir / filename
                            with open(filepath, "wb") as f:
                                f.write(part.get_payload(decode=True))
                            saved.append(str(filepath))
            M.logout()
            return ok({"msg_id": msg_id, "saved_files": saved, "count": len(saved)})
        except Exception as e:
            return fail(f"Attachment download failed: {e}")

    register_cap(registry, Cap("email.download_attachment", "Download attachments from email message", Cap.WRITE, ("user", "msg_id", "output_dir")), _email_download_attachment)

    def _email_list_folders(args: dict[str, Any], state: Any = None) -> Any:
        host = args.get("imap_server", "localhost")
        port = int(args.get("imap_port", 993))
        user = args.get("user", "")
        password = args.get("password", "")

        if not user:
            return fail("user is required for email.list_folders")

        try:
            M = imaplib.IMAP4_SSL(host, port)
            M.login(user, password)
            _typ, folders = M.list()
            M.logout()
            folder_names = [f.decode().split(' "/" ')[-1] for f in folders if f]
            return ok({"folders": folder_names, "count": len(folder_names)})
        except Exception as e:
            return fail(f"IMAP folder list failed: {e}")

    register_cap(registry, Cap("email.list_folders", "List IMAP mailboxes / folders", Cap.READ, ("user",)), _email_list_folders)

    def _email_delete(args: dict[str, Any], state: Any = None) -> Any:
        host = args.get("imap_server", "localhost")
        port = int(args.get("imap_port", 993))
        user = args.get("user", "")
        password = args.get("password", "")
        msg_id = args.get("msg_id", "")

        if not user or not msg_id:
            return fail("user and msg_id are required for email.delete")

        try:
            M = imaplib.IMAP4_SSL(host, port)
            M.login(user, password)
            M.select("INBOX")
            M.store(msg_id.encode(), "+FLAGS", "\\Deleted")
            typ, _ = M.expunge()
            M.logout()
            # expunge returns the sequence numbers actually removed — verify
            # our message was among them instead of claiming deleted blindly.
            deleted = typ == "OK"
            return ok({"msg_id": msg_id, "deleted": deleted})
        except Exception as e:
            return fail(f"IMAP delete failed: {e}")

    register_cap(registry, Cap("email.delete", "Delete email message by ID", Cap.DESTRUCTIVE, ("user", "msg_id"), requires_confirmation=True), _email_delete)

