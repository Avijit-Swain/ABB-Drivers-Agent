import base64
import json
import mimetypes
import os
import smtplib
import sqlite3
import tempfile
import uuid
from io import BytesIO
from email import encoders as email_encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

from agent import agent, DB_PATH as AGENT_DB_PATH

load_dotenv()

APP_DIR = Path(__file__).parent
WEB_DIR = APP_DIR / "react_app"
PLOTS_DIR = APP_DIR / "plots"
ASSETS_DIR = APP_DIR / "assets"
CONV_DB_PATH = AGENT_DB_PATH

EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
PROFILE_NAME = os.getenv("PROFILE_NAME", "")
PROFILE_TITLE = os.getenv("PROFILE_TITLE", "")


def _parse_agent_content(content: str) -> dict:
    lines = content.splitlines()
    visible_lines = []
    closing_lines = []
    plot_urls = []
    technical_prefixes = (
        "Plot generated at:",
        "Latest plot copy:",
        "Chart type:",
        "Color palette:",
        "SQL used:",
        "SELECT ",
    )

    in_sql_block = False
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("Plot generated at:"):
            plot_path = stripped.replace("Plot generated at:", "", 1).strip()
            path = Path(plot_path)
            if path.exists() and path.parent == PLOTS_DIR:
                plot_urls.append(f"/plots/{path.name}")
            in_sql_block = False
            continue

        if stripped == "Do you have any more requests?" or stripped == "Can I help you with anything else?":
            closing_lines.append(stripped)
            in_sql_block = False
            continue

        if stripped.startswith("SQL used:"):
            in_sql_block = True
            continue

        if in_sql_block:
            if stripped.endswith(";") or not stripped:
                in_sql_block = False
            continue

        if any(stripped.startswith(prefix) for prefix in technical_prefixes):
            continue

        visible_lines.append(line)

    return {
        "content": content,
        "visibleText": "\n".join(visible_lines).strip(),
        "closingText": "\n".join(closing_lines).strip(),
        "plotUrls": plot_urls,
    }


def _safe_file(base_dir: Path, requested_path: str) -> Optional[Path]:
    requested_path = unquote(requested_path).lstrip("/")
    if not requested_path:
        requested_path = "index.html"
    path = (base_dir / requested_path).resolve()
    try:
        path.relative_to(base_dir.resolve())
    except ValueError:
        return None
    if path.is_dir():
        path = path / "index.html"
    return path if path.exists() else None


# ---------------------------------------------------------------------------
# Conversation persistence
# ---------------------------------------------------------------------------

def _db():
    conn = sqlite3.connect(CONV_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_conversation_tables():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                visible_text TEXT,
                closing_text TEXT,
                plot_urls TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        conn.commit()


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _create_conversation() -> str:
    cid = str(uuid.uuid4())
    now = _now_iso()
    with _db() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (cid, None, now, now),
        )
        conn.commit()
    return cid


def _update_conversation_title(cid: str, title: str):
    with _db() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now_iso(), cid),
        )
        conn.commit()


def _touch_conversation(cid: str):
    with _db() as conn:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now_iso(), cid))
        conn.commit()


def _conversation_exists(cid: str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (cid,)).fetchone()
    return row is not None


def _conversation_has_messages(cid: str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ?", (cid,)).fetchone()
    return bool(row and row[0] > 0)


def _get_conversation_title(cid: str) -> str:
    with _db() as conn:
        row = conn.execute("SELECT title FROM conversations WHERE id = ?", (cid,)).fetchone()
    return row[0] if row and row[0] else ""


def _save_message(cid: str, role: str, content: str, visible_text: str, closing_text: str, plot_urls: list):
    with _db() as conn:
        conn.execute(
            "INSERT INTO conversation_messages (id, conversation_id, role, content, visible_text, closing_text, plot_urls, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), cid, role, content, visible_text, closing_text, json.dumps(plot_urls), _now_iso()),
        )
        conn.commit()


def _get_conversations() -> list:
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE title IS NOT NULL ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
    return [dict(r) for r in rows]


def _delete_conversation(cid: str):
    with _db() as conn:
        conn.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (cid,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        conn.commit()


def _get_conversation_messages(cid: str) -> list:
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, role, content, visible_text, closing_text, plot_urls, created_at FROM conversation_messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (cid,),
        ).fetchall()
    result = []
    for r in rows:
        msg = dict(r)
        msg["plot_urls"] = json.loads(msg.get("plot_urls") or "[]")
        result.append(msg)
    return result


def _generate_title(user_message: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return user_message[:50]
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        llm = ChatOpenAI(model="gpt-4o-mini", api_key=api_key, temperature=0, max_tokens=15)
        response = llm.invoke([
            SystemMessage(content="Generate a short title of at most 6 words for a conversation that starts with the following user message. Return only the title, no quotes, no punctuation at the end."),
            HumanMessage(content=user_message[:300]),
        ])
        return response.content.strip()[:80]
    except Exception:
        return user_message[:50]


def _init_recipients_table():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recipients (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                email TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(recipients)").fetchall()]
        if "name" not in columns:
            conn.execute("ALTER TABLE recipients ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _get_recipients() -> list:
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, email, created_at FROM recipients ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def _add_recipient(email: str, name: str = "") -> dict:
    rid = str(uuid.uuid4())
    now = _now_iso()
    email = email.strip().lower()
    name = name.strip()
    with _db() as conn:
        conn.execute(
            "INSERT INTO recipients (id, name, email, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET name = excluded.name",
            (rid, name, email, now),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, name, email, created_at FROM recipients WHERE email = ?", (email,)
        ).fetchone()
    return dict(row) if row else {"id": rid, "name": name, "email": email, "created_at": now}


def _delete_recipient(rid: str):
    with _db() as conn:
        conn.execute("DELETE FROM recipients WHERE id = ?", (rid,))
        conn.commit()


def _generate_pdf(cid: str) -> bytes:
    import re
    import html as _html
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Image as RLImage,
        HRFlowable, Table, TableStyle, KeepTogether,
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ── data ──────────────────────────────────────────────────────────────
    with _db() as conn:
        conv = conn.execute("SELECT title FROM conversations WHERE id = ?", (cid,)).fetchone()
    title = conv[0] if conv and conv[0] else "Conversation"
    messages = _get_conversation_messages(cid)

    # ── fonts ─────────────────────────────────────────────────────────────
    # Verdana ships as separate .ttf files (not .ttc) — clean WinAnsi
    # encoding, no glyph-mapping bugs. Falls back to built-in Helvetica.
    BODY = "Helvetica"
    BOLD = "Helvetica-Bold"

    def _try_register(name, paths):
        try:
            pdfmetrics.getFont(name)
            return name
        except KeyError:
            pass
        for path in paths:
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                return name
            except Exception:
                continue
        return None

    _body_ttf = _try_register("_Body", [
        "/System/Library/Fonts/Supplemental/Verdana.ttf",
        "/System/Library/Fonts/Supplemental/Trebuchet MS.ttf",
    ])
    _bold_ttf = _try_register("_BodyBold", [
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
        "/System/Library/Fonts/Supplemental/Trebuchet MS Bold.ttf",
    ])
    if _body_ttf:
        BODY = _body_ttf
    if _bold_ttf:
        BOLD = _bold_ttf

    # ── colors ────────────────────────────────────────────────────────────
    RED        = HexColor("#FF000F")
    DARK       = HexColor("#19202C")
    MID        = HexColor("#48556A")
    SUBTLE     = HexColor("#9CA3AF")
    USER_BG    = HexColor("#EFF6FF")
    BOT_BG     = HexColor("#FFF5F5")
    USER_ACC   = HexColor("#2563EB")
    TOPIC_BG   = HexColor("#F1F3F5")
    TOPIC_TEXT = HexColor("#19202C")

    # ── styles ────────────────────────────────────────────────────────────
    def ps(name, **kw):
        kw.setdefault("fontName", BODY)
        kw.setdefault("fontSize", 11)
        kw.setdefault("leading", 17)
        kw.setdefault("textColor", DARK)
        kw.setdefault("alignment", TA_LEFT)
        return ParagraphStyle(name, **kw)

    S_LOGO    = ps("logo",    fontName=BOLD, fontSize=28, textColor=RED,        leading=34)
    S_TITLE   = ps("title",   fontName=BOLD, fontSize=17, textColor=DARK,       leading=22)
    S_TOPIC   = ps("topic",   fontName=BOLD, fontSize=11, textColor=TOPIC_TEXT, leading=16)
    S_DATE    = ps("date",    fontSize=8.5,  textColor=MID,                     leading=12)
    S_ROLE_U  = ps("role_u",  fontName=BOLD, fontSize=9,  textColor=USER_ACC,   leading=13)
    S_ROLE_B  = ps("role_b",  fontName=BOLD, fontSize=9,  textColor=RED,        leading=13)
    S_BODY    = ps("body",    fontSize=10.5, leading=16.5, spaceAfter=0)
    S_BULLET  = ps("bullet",  fontSize=10.5, leading=16.5, leftIndent=10)

    # ── helpers ───────────────────────────────────────────────────────────
    page_w, _ = A4
    avail_w = page_w - 2 * 20 * mm

    def to_rl(raw: str) -> str:
        s = _html.escape(raw)
        s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s, flags=re.DOTALL)
        s = re.sub(r'[*_`~]', '', s)
        return s

    def parse_text(text: str):
        items = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                items.append(Spacer(1, 4))
                continue
            s = re.sub(r'^#{1,6}\s*', '', s)
            s = re.sub(r'^&gt;\s*', '', s)
            if re.match(r'^[-•]\s+', s):
                items.append(Paragraph(f"•  {to_rl(s[2:])}", S_BULLET))
            elif re.match(r'^\d+\.\s+', s):
                num, rest = re.match(r'^(\d+)\.\s+(.*)', s).groups()
                items.append(Paragraph(f"{num}.  {to_rl(rest)}", S_BULLET))
            else:
                items.append(Paragraph(to_rl(s), S_BODY))
        return items or [Spacer(1, 4)]

    def msg_card(role: str, text: str, plot_urls: list):
        is_user = role == "user"
        label   = "You" if is_user else "Decision Insights Copilot"
        bg      = USER_BG if is_user else BOT_BG
        acc     = USER_ACC if is_user else RED
        s_role  = S_ROLE_U if is_user else S_ROLE_B

        header = Table([[Paragraph(label, s_role)]], colWidths=[avail_w])
        header.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), bg),
            ("TOPPADDING",    (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING",   (0, 0), (-1, -1), 10),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
            ("LINEABOVE",     (0, 0), (-1,  0), 2, acc),
        ]))

        body_rows = parse_text(text)
        body = Table([[item] for item in body_rows], colWidths=[avail_w])
        body.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 16),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
            ("LINEBEFORE",    (0, 0), ( 0, -1), 3, acc),
        ]))

        card = [KeepTogether([header, body])]

        for url in (plot_urls or []):
            plot_name = url.lstrip("/").replace("plots/", "", 1)
            plot_path = PLOTS_DIR / plot_name
            if not plot_path.exists():
                continue
            try:
                from PIL import Image as _PILImg
                with _PILImg.open(str(plot_path)) as pim:
                    iw, ih = pim.size
                scale = min(1.0, avail_w / iw)
                card.append(Spacer(1, 3 * mm))
                card.append(RLImage(str(plot_path), width=iw * scale, height=ih * scale))
            except Exception:
                continue

        card.append(Spacer(1, 7 * mm))
        return card

    def _page_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(BODY, 8)
        canvas.setFillColor(SUBTLE)
        y_foot = 12 * mm
        canvas.drawString(20 * mm, y_foot, "Decision Insights Copilot")
        canvas.drawRightString(page_w - 20 * mm, y_foot, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    # ── build ─────────────────────────────────────────────────────────────
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=title,
        author="Decision Insights Copilot",
    )

    story = []

    # Header
    hdr = Table(
        [[Paragraph("ABB", S_LOGO), Paragraph("Decision Insights Copilot", S_TITLE)]],
        colWidths=[28 * mm, avail_w - 28 * mm],
    )
    hdr.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width="100%", thickness=2, color=RED, spaceAfter=0))

    # Topic band — full-width gray background, prominent title + date
    from datetime import datetime as _dt
    generated = _dt.now().strftime("%-d %b %Y · %-I:%M %p")
    topic_block = Table(
        [
            [Paragraph(_html.escape(title), S_TOPIC)],
            [Paragraph(f"Generated {generated}", S_DATE)],
        ],
        colWidths=[avail_w],
    )
    topic_block.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), TOPIC_BG),
        ("TOPPADDING",    (0, 0), (0,  0),  10),
        ("BOTTOMPADDING", (0, 0), (-1, -1),  8),
        ("TOPPADDING",    (0, 1), (0,  1),   2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    story.append(topic_block)
    story.append(Spacer(1, 8 * mm))

    # Messages
    for msg in messages:
        text = msg.get("visible_text") or ""
        if msg.get("closing_text"):
            text += "\n" + msg["closing_text"]
        if not text.strip():
            text = msg.get("content") or ""
        story.extend(msg_card(msg["role"], text.strip(), msg.get("plot_urls") or []))

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue()


def _send_gmail(to_email: str, recipient_name: str, pdf_bytes: bytes, conversation_title: str):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = f"Decision Insights Copilot: {conversation_title}"
    greeting_name = recipient_name.strip() or "there"
    msg.attach(MIMEText(
        f"Hi {greeting_name},\n\n"
        f"Please find the conversation '{conversation_title}' attached as a PDF.\n\n"
        f"Regards,\n{PROFILE_NAME}\n{PROFILE_TITLE}",
        "plain",
    ))
    attachment = MIMEBase("application", "pdf")
    attachment.set_payload(pdf_bytes)
    email_encoders.encode_base64(attachment)
    safe_name = conversation_title[:50].replace('"', "'")
    attachment.add_header("Content-Disposition", "attachment", filename=f"{safe_name}.pdf")
    msg.attach(attachment)

    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)


def _transcribe_audio(audio_b64: str, mime_type: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    audio_bytes = base64.b64decode(audio_b64)

    ext = ".webm"
    if "mp4" in mime_type:
        ext = ".mp4"
    elif "ogg" in mime_type:
        ext = ".ogg"
    elif "wav" in mime_type:
        ext = ".wav"

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        with open(tmp_path, "rb") as f:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=f)
        return transcript.text
    finally:
        os.unlink(tmp_path)


_init_conversation_tables()
_init_recipients_table()


class BubuRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def _write_stream_event(self, payload: dict):
        body = (json.dumps(payload, default=str) + "\n").encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def _send_file(self, path: Path):
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_pdf(self, pdf_bytes: bytes, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        safe = filename.replace('"', "'")
        self.send_header("Content-Disposition", f'attachment; filename="{safe}.pdf"')
        self.send_header("Content-Length", str(len(pdf_bytes)))
        self.end_headers()
        self.wfile.write(pdf_bytes)

    def do_POST(self):
        parsed = urlparse(self.path)
        is_send_email = (
            parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/send-email")
        )
        if parsed.path not in {"/api/chat", "/api/chat/stream", "/api/transcribe", "/api/recipients"} and not is_send_email:
            self._send_json(404, {"error": "Not found"})
            return

        if parsed.path == "/api/recipients":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                recipient = _add_recipient(payload.get("email", ""), payload.get("name", ""))
                self._send_json(200, recipient)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if is_send_email:
            try:
                parts = parsed.path.strip("/").split("/")
                cid = parts[2]
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                to_email = payload.get("email", "").strip()
                recipient_name = payload.get("name", "").strip()
                if not to_email:
                    self._send_json(400, {"error": "email required"})
                    return
                if not _conversation_has_messages(cid):
                    self._send_json(400, {"error": "conversation has no messages"})
                    return
                title = _get_conversation_title(cid) or "Conversation"
                pdf_bytes = _generate_pdf(cid)
                _send_gmail(to_email, recipient_name, pdf_bytes, title)
                self._send_json(200, {"ok": True})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/transcribe":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                text = _transcribe_audio(payload["audio"], payload.get("mimeType", "audio/webm"))
                self._send_json(200, {"text": text})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            message = str(payload.get("message", "")).strip()
            history = payload.get("history", [])
            if not message:
                self._send_json(400, {"error": "Message is required."})
                return

            conversation_id = str(payload.get("conversationId") or "").strip()

            if parsed.path == "/api/chat/stream":
                self._send_stream_headers()

                conversation_id = str(payload.get("conversationId") or "").strip()
                is_new = not conversation_id or not _conversation_exists(conversation_id)
                if is_new:
                    conversation_id = _create_conversation()
                    title = _generate_title(message)
                    _update_conversation_title(conversation_id, title)
                else:
                    title = _get_conversation_title(conversation_id)

                self._write_stream_event({
                    "type": "conversation",
                    "id": conversation_id,
                    "title": title,
                    "is_new": is_new,
                })
                _save_message(conversation_id, "user", message, message, "", [])

                final_content = ""
                parsed_result = None
                for event in agent.respond_stream(message, history, conversation_id=conversation_id):
                    if event.get("type") == "final":
                        final_content = event.get("content", "")
                        parsed_result = _parse_agent_content(final_content)
                        self._write_stream_event({"type": "parsed_final", **parsed_result})
                    else:
                        self._write_stream_event(event)

                if parsed_result is None:
                    parsed_result = {
                        "content": "",
                        "visibleText": "I could not complete that request.",
                        "closingText": "",
                        "plotUrls": [],
                    }
                    self._write_stream_event({"type": "parsed_final", **parsed_result})

                _save_message(
                    conversation_id,
                    "assistant",
                    final_content or parsed_result["visibleText"],
                    parsed_result.get("visibleText", ""),
                    parsed_result.get("closingText", ""),
                    parsed_result.get("plotUrls", []),
                )
                _touch_conversation(conversation_id)
                return

            response = agent.respond(message, history, conversation_id=conversation_id)
            self._send_json(200, _parse_agent_content(response))
        except Exception as exc:
            if parsed.path == "/api/chat/stream":
                try:
                    self._write_stream_event(
                        {
                            "type": "error",
                            "message": f"I ran into an error while processing that request. Details: {exc}",
                        }
                    )
                except Exception:
                    pass
                return
            self._send_json(
                500,
                {
                    "content": "",
                    "visibleText": f"I ran into an error while processing that request. Details: {exc}",
                    "closingText": "",
                    "plotUrls": [],
                },
            )

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/conversations":
            self._send_json(200, {"conversations": _get_conversations()})
            return

        if parsed.path == "/api/recipients":
            self._send_json(200, {"recipients": _get_recipients()})
            return

        if parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/pdf"):
            parts = parsed.path.split("/")
            if len(parts) == 5:
                cid = parts[3]
                try:
                    if not _conversation_has_messages(cid):
                        self._send_json(400, {"error": "conversation has no messages"})
                        return
                    title = _get_conversation_title(cid) or "Conversation"
                    pdf_bytes = _generate_pdf(cid)
                    self._send_pdf(pdf_bytes, title)
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
            else:
                self._send_json(404, {"error": "Not found"})
            return

        if parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/messages"):
            parts = parsed.path.split("/")
            if len(parts) == 5:
                self._send_json(200, {"messages": _get_conversation_messages(parts[3])})
            else:
                self._send_json(404, {"error": "Not found"})
            return

        if parsed.path.startswith("/plots/"):
            plot_name = unquote(parsed.path.replace("/plots/", "", 1))
            path = (PLOTS_DIR / plot_name).resolve()
            try:
                path.relative_to(PLOTS_DIR.resolve())
            except ValueError:
                self.send_error(404)
                return
            if path.exists():
                self._send_file(path)
                return
            self.send_error(404)
            return

        if parsed.path.startswith("/assets/"):
            asset_name = unquote(parsed.path.replace("/assets/", "", 1))
            path = (ASSETS_DIR / asset_name).resolve()
            try:
                path.relative_to(ASSETS_DIR.resolve())
            except ValueError:
                self.send_error(404)
                return
            if path.exists():
                self._send_file(path)
                return
            self.send_error(404)
            return

        path = _safe_file(WEB_DIR, parsed.path)
        if path is None:
            path = WEB_DIR / "index.html"
        self._send_file(path)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "recipients":
            _delete_recipient(parts[2])
            self._send_json(200, {"ok": True})
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "conversations":
            _delete_conversation(parts[2])
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "Not found"})


def run(host: str = "0.0.0.0", port: int = 8500):
    server = ThreadingHTTPServer((host, port), BubuRequestHandler)
    print(f"React Decision Insights Copilot running at http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
