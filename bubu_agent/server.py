import json
import mimetypes
import os
import sqlite3
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from agent import agent, DB_PATH as AGENT_DB_PATH


APP_DIR = Path(__file__).parent
WEB_DIR = APP_DIR / "react_app"
PLOTS_DIR = APP_DIR / "plots"
ASSETS_DIR = APP_DIR / "assets"
CONV_DB_PATH = AGENT_DB_PATH


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


_init_conversation_tables()


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

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/chat", "/api/chat/stream"}:
            self._send_json(404, {"error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            message = str(payload.get("message", "")).strip()
            history = payload.get("history", [])
            if not message:
                self._send_json(400, {"error": "Message is required."})
                return

            if parsed.path == "/api/chat/stream":
                self._send_stream_headers()

                conversation_id = str(payload.get("conversationId") or "").strip()
                is_new = not conversation_id
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
                for event in agent.respond_stream(message, history):
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

            response = agent.respond(message, history)
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


def run(host: str = "0.0.0.0", port: int = 8502):
    server = ThreadingHTTPServer((host, port), BubuRequestHandler)
    print(f"React ABB Driver Analysis Copilot running at http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
