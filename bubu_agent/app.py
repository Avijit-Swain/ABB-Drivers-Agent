import base64
from pathlib import Path

import streamlit as st

from agent import agent, set_trace_callback


st.set_page_config(page_title="ABB Executive Assistant", page_icon="A", layout="wide")


APP_DIR = Path(__file__).parent
ABB_LOGO_PATH = APP_DIR / "assets" / "abb-logo.svg"
ABB_LOGO_DATA_URL = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(ABB_LOGO_PATH.read_bytes()).decode("utf-8")
)

if "messages" not in st.session_state:
    st.session_state.messages = []

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

if "agent_steps" not in st.session_state:
    st.session_state.agent_steps = []

is_processing = st.session_state.pending_prompt is not None
message_count = max(1, len(st.session_state.messages) + (1 if is_processing else 0))
chat_height = min(800, 390 + message_count * 50)
panel_height = chat_height + 104


def render_processing_indicator():
    st.markdown(
        """
        <div class="abb-thinking">
            <span class="abb-thinking-ring"></span>
            <strong>Working on it</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_agent_steps():
    if not st.session_state.agent_steps:
        st.markdown(
            """<p class="abb-panel-empty">No steps yet.</p>""",
            unsafe_allow_html=True,
        )
        return

    lines = []
    for index, step in enumerate(st.session_state.agent_steps[-12:], start=1):
        detail = step.get("detail", "")
        if detail:
            lines.append(f"{index}. **{step['title']}**: {detail}")
        else:
            lines.append(f"{index}. **{step['title']}**")
    st.markdown("\n".join(lines))


def render_message_content(content):
    lines = content.splitlines()
    visible_lines = []
    closing_lines = []
    plot_paths = []
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
            if Path(plot_path).exists():
                plot_paths.append(plot_path)
            in_sql_block = False
            continue

        if stripped == "Do you have any more requests?":
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

    visible_text = "\n".join(visible_lines).strip()
    if visible_text:
        st.markdown(visible_text)

    for plot_path in plot_paths:
        st.image(plot_path, width="stretch")

    if closing_lines:
        st.markdown("\n".join(closing_lines))


st.markdown(
    """
    <style>
        :root {
            --abb-red: #ff000f;
            --ink: #101828;
            --muted: #667085;
            --line: #d9dde7;
            --panel: #ffffff;
            --surface: #f5f6f8;
            --panel-height: __PANEL_HEIGHT__px;
        }

        .stApp {
            background: var(--surface);
            color: var(--ink);
        }

        .block-container {
            width: min(96vw, 1680px);
            max-width: 1680px;
            padding: 1.1rem 1.5rem 5.5rem;
        }

        header[data-testid="stHeader"] {
            background: transparent;
        }

        .abb-topbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
            padding: 0.9rem 1rem;
            margin-bottom: 0.9rem;
            background: #ffffff;
            border: 1px solid var(--line);
            border-top: 4px solid var(--abb-red);
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06);
        }

        .abb-brand {
            display: flex;
            align-items: center;
            gap: 0.9rem;
        }

        .abb-brand img {
            width: 96px;
            height: auto;
            display: block;
        }

        .abb-brand-text {
            display: flex;
            flex-direction: column;
            gap: 0.1rem;
        }

        .abb-brand-text strong {
            font-size: 1rem;
            line-height: 1.2;
        }

        .abb-brand-text span,
        .abb-env span,
        .abb-eyebrow,
        .abb-card span,
        .abb-chat-label,
        .abb-footnote {
            color: var(--muted);
        }

        .abb-brand-text span,
        .abb-env span {
            font-size: 0.82rem;
        }

        .abb-env {
            display: flex;
            align-items: center;
            gap: 0.55rem;
            white-space: nowrap;
        }

        .abb-dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background: var(--abb-red);
            box-shadow: 0 0 0 5px rgba(255, 0, 15, 0.10);
        }

        .abb-intro,
        .abb-side-panel {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 8px;
            box-shadow: 0 10px 28px rgba(16, 24, 40, 0.06);
            padding: 1.15rem;
            position: sticky;
            top: 1rem;
            min-height: var(--panel-height);
            display: flex;
            flex-direction: column;
        }

        .abb-side-panel {
            gap: 1rem;
        }

        .abb-eyebrow {
            margin: 0 0 0.55rem;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0;
            text-transform: uppercase;
        }

        .abb-intro h1 {
            margin: 0;
            max-width: 20rem;
            font-size: 1.38rem;
            line-height: 1.12;
            letter-spacing: 0;
        }

        .abb-intro p {
            margin: 0.65rem 0 0;
            max-width: 21rem;
            color: var(--muted);
            font-size: 0.88rem;
            line-height: 1.48;
        }

        .abb-card-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.5rem;
            margin-top: 0.9rem;
        }

        .abb-card {
            padding: 0.68rem 0.72rem;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: #fbfcfd;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 0.75rem;
        }

        .abb-card strong {
            display: block;
            margin: 0;
            font-size: 0.95rem;
        }

        .abb-card span {
            display: block;
            font-size: 0.82rem;
            line-height: 1.35;
            text-align: right;
        }

        .abb-brief,
        .abb-side-section {
            margin-top: 0;
            padding-top: 0;
        }

        .abb-side-section + .abb-side-section,
        .abb-brief {
            margin-top: 0.95rem;
            padding-top: 0.95rem;
            border-top: 1px solid var(--line);
        }

        .abb-brief h3,
        .abb-side-section h3 {
            margin: 0 0 0.45rem;
            font-size: 0.92rem;
            letter-spacing: 0;
        }

        .abb-brief ul,
        .abb-side-section ul {
            margin: 0;
            padding-left: 1rem;
            color: var(--muted);
            font-size: 0.8rem;
            line-height: 1.4;
        }

        .abb-brief li + li,
        .abb-side-section li + li {
            margin-top: 0.32rem;
        }

        .abb-panel {
            margin-top: 0;
            padding-top: 0;
        }

        .abb-panel h3 {
            margin: 0 0 0.45rem;
            font-size: 0.92rem;
            letter-spacing: 0;
        }

        .abb-panel-empty {
            color: var(--muted);
            font-size: 0.8rem;
            line-height: 1.4;
            margin: 0.45rem 0 0;
        }

        .abb-chat-header {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: flex-start;
            padding: 0 0 0.5rem;
            border-bottom: 1px solid var(--line);
            margin-bottom: 0.5rem;
        }

        .abb-chat-actions {
            display: flex;
            flex-direction: column;
            gap: 0.45rem;
            align-items: flex-end;
        }

        .abb-chat-label {
            margin: 0;
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0;
        }

        .abb-chat-header h2 {
            margin: 0.25rem 0 0;
            font-size: 1.25rem;
            letter-spacing: 0;
        }

        .abb-mode {
            border: 1px solid #ffd0d3;
            background: #fff6f6;
            color: #b0000a;
            border-radius: 999px;
            padding: 0.35rem 0.65rem;
            font-size: 0.78rem;
            font-weight: 700;
            white-space: nowrap;
        }

        .abb-samples {
            margin-top: 0;
            padding: 0;
            background: transparent;
            border: 0;
            border-radius: 0;
            box-shadow: none;
        }

        .abb-samples h3 {
            margin: 0 0 0.45rem;
            font-size: 0.92rem;
            letter-spacing: 0;
        }

        .abb-sample-list {
            margin: 0;
            padding-left: 1rem;
            color: var(--muted);
            font-size: 0.8rem;
            line-height: 1.43;
        }

        .abb-sample-list li + li {
            margin-top: 0.42rem;
        }

        .abb-footnote {
            margin: auto 0 0;
            font-size: 0.78rem;
            line-height: 1.4;
        }

        .abb-divider {
            height: 3px;
            width: 56px;
            margin-top: 0.9rem;
            background: var(--abb-red);
            border-radius: 999px;
        }

        .abb-microcopy {
            margin: 0.3rem 0 0;
            color: var(--muted);
            font-size: 0.78rem;
        }

        .abb-chat-footer {
            margin: 0.65rem 0 0;
            color: var(--muted);
            font-size: 0.76rem;
            text-align: right;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: #ffffff;
            border-color: var(--line);
            border-radius: 8px;
            box-shadow: 0 10px 28px rgba(16, 24, 40, 0.06);
            min-height: var(--panel-height);
        }

        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            padding: 1rem;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stVerticalBlockBorderWrapper"] {
            box-shadow: none;
            background: #fafbfc;
            border-color: #edf0f5;
            min-height: auto;
        }

        [data-testid="stChatMessage"] {
            border: 1px solid #e6e9f0;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: none;
            margin-bottom: 0.55rem;
        }

        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
            background: #fafbfc;
        }

        .abb-thinking {
            display: flex;
            align-items: center;
            gap: 0.7rem;
            min-height: 42px;
            color: var(--ink);
        }

        .abb-thinking-ring {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            border: 3px solid #ffd0d3;
            border-top-color: var(--abb-red);
            animation: abb-spin 0.85s linear infinite;
            flex: 0 0 auto;
        }

        .abb-thinking strong {
            display: block;
        }

        .abb-thinking strong {
            font-size: 0.9rem;
            line-height: 1.2;
        }

        @keyframes abb-spin {
            to {
                transform: rotate(360deg);
            }
        }

        [data-testid="stChatInput"] {
            max-width: min(96vw, 1680px);
            margin: 0 auto;
        }

        [data-testid="stChatInput"] textarea {
            border-radius: 8px;
            border-color: var(--line);
            min-height: 48px;
        }

        @media (max-width: 900px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }

            .abb-card-grid {
                grid-template-columns: 1fr;
            }

            .abb-topbar,
            .abb-chat-header {
                flex-direction: column;
                align-items: flex-start;
            }

            .abb-chat-actions {
                align-items: flex-start;
            }

            .abb-intro {
                position: static;
                min-height: auto;
            }

            .abb-side-panel {
                position: static;
                min-height: auto;
            }

        }
    </style>
    """.replace("__PANEL_HEIGHT__", str(panel_height)),
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="abb-topbar">
        <div class="abb-brand">
            <img src="{ABB_LOGO_DATA_URL}" alt="ABB logo" />
            <div class="abb-brand-text">
                <strong>ABB Executive Assistant</strong>
                <span>Leadership briefing and support interface</span>
            </div>
        </div>
        <div class="abb-env">
            <span class="abb-dot"></span>
            <span>Prototype online</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

left, center, right = st.columns([0.82, 1.9, 0.82], gap="medium")

with left:
    st.markdown(
        """
        <section class="abb-intro">
            <p class="abb-eyebrow">ABB Assistant</p>
            <h1>Executive conversation prototype.</h1>
            <p>
                A focused leadership interface for validating the assistant experience.
            </p>
            <div class="abb-card-grid">
                <div class="abb-card">
                    <strong>Status</strong>
                    <span>Online</span>
                </div>
                <div class="abb-card">
                    <strong>Mode</strong>
                    <span>Agent</span>
                </div>
            </div>
            <div class="abb-brief">
                <h3>Available context</h3>
                <ul>
                    <li><strong>Workbook DB:</strong> three SQLite tables from the notebook prototype.</li>
                    <li><strong>Text-to-SQL:</strong> first routes to the right table, then generates SQL from that table only.</li>
                    <li><strong>KPI text:</strong> unstructured PDF definitions extracted into one text file.</li>
                    <li><strong>Simulator:</strong> what-if growth simulation for ELSP and ELSB driver values.</li>
                </ul>
            </div>
            <div class="abb-side-section abb-samples">
                <h3>Sample questions</h3>
                <ul class="abb-sample-list">
                    <li>Give me the bear, base, and bull forecast for ELSP.</li>
                    <li>What is the actual growth for ELSB?</li>
                    <li>Which drivers are selected for ELSP?</li>
                    <li>Find the elasticity and driver name in ELSB with the highest contribution.</li>
                    <li>Show the recommended range for Data Center / Hyperscaler.</li>
                    <li>Get me the data for Data Center and orders for ELSP.</li>
                    <li>Plot this as a line chart.</li>
                    <li>What does China IIP mean?</li>
                    <li>Simulate ELSP if Data Center growth is 30.</li>
                    <li>Simulate ELSB if Copper Price is -5 and US Utility Capex is 30.</li>
                </ul>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

with right:
    with st.container(border=True):
        st.markdown(
            """
            <div class="abb-panel">
                <p class="abb-eyebrow">Live trace</p>
                <h3>Assistant steps</h3>
            </div>
            """,
            unsafe_allow_html=True,
        )
        steps_placeholder = st.empty()
        with steps_placeholder.container():
            render_agent_steps()

with center:
    with st.container(border=True):
        header_label_col, header_action_col = st.columns([0.78, 0.22], vertical_alignment="center")
        with header_label_col:
            st.markdown(
                """
                <div class="abb-chat-header">
                    <p class="abb-chat-label">Assistant workspace</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with header_action_col:
            if st.button("Clear", use_container_width=True):
                st.session_state.messages = []
                st.session_state.pending_prompt = None
                st.rerun()

        with st.container(height=chat_height, border=True):
            if not st.session_state.messages:
                with st.chat_message("assistant"):
                    st.markdown("Hello. Send a message to test the ABB assistant prototype.")

            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    render_message_content(message["content"])

            if is_processing:
                with st.chat_message("assistant"):
                    render_processing_indicator()

        st.markdown(
            f"""<p class="abb-chat-footer">{len(st.session_state.messages)} messages</p>""",
            unsafe_allow_html=True,
        )

prompt = st.chat_input("Message ABB Executive Assistant", disabled=is_processing)

if prompt and not is_processing:
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.agent_steps = []
    st.session_state.pending_prompt = prompt
    st.rerun()

if st.session_state.pending_prompt:
    pending_prompt = st.session_state.pending_prompt
    history = st.session_state.messages
    if history and history[-1]["role"] == "user" and history[-1]["content"] == pending_prompt:
        history = history[:-1]

    def trace_callback(title, detail=""):
        st.session_state.agent_steps.append({"title": title, "detail": detail})
        with steps_placeholder.container():
            render_agent_steps()

    set_trace_callback(trace_callback)
    try:
        response = agent.respond(pending_prompt, history)
    except Exception as exc:
        response = (
            "I ran into an error while processing that request. "
            f"Details: {exc}"
        )
    finally:
        set_trace_callback(None)

    st.session_state.messages.append({"role": "assistant", "content": response})
    st.session_state.pending_prompt = None
    st.rerun()
