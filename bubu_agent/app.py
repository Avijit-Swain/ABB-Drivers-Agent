import base64
import time
from pathlib import Path

import streamlit as st

from agent import agent, set_trace_callback


st.set_page_config(
    page_title="ABB Decision Insights Copilot",
    page_icon="assets/abb-logo.svg",
    layout="wide",
    initial_sidebar_state="collapsed",
)


APP_DIR = Path(__file__).parent
PLOTS_DIR = APP_DIR / "plots"
ABB_LOGO_PATH = APP_DIR / "assets" / "abb-logo.svg"
ABB_LOGO_DATA_URL = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(ABB_LOGO_PATH.read_bytes()).decode("utf-8")
)


def _html(markup: str) -> str:
    """Collapse indentation/blank lines so Streamlit doesn't render HTML as a code block."""
    return "".join(line.strip() for line in markup.splitlines() if line.strip())


# Fallback height; CSS overrides this to fill the true remaining viewport height.
CHAT_HEIGHT = 600


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

if "streaming_response" not in st.session_state:
    st.session_state.streaming_response = None

is_processing = st.session_state.pending_prompt is not None


def submit_prompt(prompt: str):
    """Queue a user prompt for the agent (shared by chat input and chips)."""
    if not prompt or is_processing:
        return
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.pending_prompt = prompt


# ---------------------------------------------------------------------------
# Dummy dashboard data (placeholder until wired to real metrics)
# ---------------------------------------------------------------------------
SPARK_TREND = (
    "4,22 14,18 24,24 34,16 44,20 54,12 64,18 74,26 84,22 94,30 104,28 116,34"
)

SUGGESTED_QUESTIONS = [
    ("📉", "Why did ELSP orders decline in the last 6 months?"),
    ("⚖️", "Which drivers are selected for ELSP?"),
    ("🔀", "Compare ELSP and ELSB performance"),
    ("📈", "Give me the bear, base, and bull forecast for ELSP"),
    ("🧪", "Simulate ELSP if Data Center growth is 30"),
]

RECENT_CONVERSATIONS = [
    ("Why did ELSP orders decline?", "10:24 AM"),
    ("Compare ELSP and ELSB", "Yesterday"),
    ("Top drivers in NWC this month", "Yesterday"),
    ("Price elasticity for DC products", "May 29"),
    ("Impact of FX on FCF", "May 28"),
]


@st.cache_data(show_spinner=False)
def get_showcase_plot() -> str:
    """Render the dummy 'Driver Contribution' chart once and cache the path."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    PLOTS_DIR.mkdir(exist_ok=True)
    path = PLOTS_DIR / "showcase_contribution.png"

    labels = [
        "Data Center\nDemand",
        "Pricing /\nRealization",
        "FX Impact",
        "Inventory\nAdjustment",
        "Enterprise\nDemand",
        "Total\nChange",
    ]
    values = [-7.2, -5.6, -3.1, -2.4, 2.3, -18.7]

    abb_red = "#ff000f"
    pos_green = "#15803d"
    neutral = "#9aa3b2"

    starts = []
    running = 0.0
    for v in values[:-1]:
        starts.append(running)
        running += v
    starts.append(0.0)

    colors = [abb_red if v < 0 else pos_green for v in values[:-1]] + [neutral]

    fig, ax = plt.subplots(figsize=(7.2, 3.05), dpi=150)
    for i, (val, start, color) in enumerate(zip(values, starts, colors)):
        ax.bar(i, val, bottom=start, color=color, width=0.62, zorder=3)
        ytext = start + val + (0.7 if val >= 0 else -1.3)
        ax.text(
            i, ytext, f"{val:+.1f}%",
            ha="center", va="bottom" if val >= 0 else "top",
            fontsize=8.5, color="#1a1d26", fontweight="bold",
        )

    ax.axhline(0, color="#cfd4de", linewidth=1, zorder=2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=8, color="#475063")
    ax.set_ylabel("Impact (%)", fontsize=9, color="#475063")
    ax.tick_params(axis="y", labelsize=8, colors="#475063")
    ax.grid(axis="y", color="#eef0f4", linewidth=1, zorder=0)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#cfd4de")
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(path)


# ---------------------------------------------------------------------------
# Message rendering (keeps the agent's plot magic-prefix contract)
# ---------------------------------------------------------------------------
def parse_message_content(content):
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

    return "\n".join(visible_lines).strip(), plot_paths, "\n".join(closing_lines).strip()


def stream_text(text: str):
    words = text.split(" ")
    for index, word in enumerate(words):
        suffix = " " if index < len(words) - 1 else ""
        yield word + suffix
        time.sleep(0.012)


def render_message_content(content):
    visible_text, plot_paths, closing_text = parse_message_content(content)
    if visible_text:
        st.markdown(visible_text)

    for plot_path in plot_paths:
        st.image(plot_path, use_container_width=True)

    if closing_text:
        st.markdown(closing_text)


def render_streaming_response(content):
    visible_text, plot_paths, closing_text = parse_message_content(content)
    if visible_text:
        st.write_stream(stream_text(visible_text))

    for plot_path in plot_paths:
        st.image(plot_path, use_container_width=True)

    if closing_text:
        st.write_stream(stream_text(closing_text))


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        :root {
            --abb-red: #ff000f;
            --abb-red-dark: #c8000c;
            --abb-red-soft: #fff3f4;
            --abb-red-tint: #ffe3e5;
            --ink: #1a1d26;
            --muted: #6b7280;
            --faint: #9aa3b2;
            --line: #e8eaf0;
            --surface: #f6f7f9;
            --panel: #ffffff;
            --pos: #15803d;
        }

        html, body, .stApp {
            height: 100%;
            overflow: hidden;
            background: var(--surface);
            color: var(--ink);
        }
        .block-container {
            height: calc(100vh - 0.6rem);
            padding: 0.45rem 1.35rem 0.35rem;
            max-width: 1800px;
            overflow: hidden;
        }
        /* reclaim the empty band the default header reserves at the top */
        header[data-testid="stHeader"] { display: none; height: 0; }
        [data-testid="stToolbar"] { display: none; }
        #MainMenu, footer { visibility: hidden; }
        /* keep the whole page fixed; only the chat container scrolls */
        [data-testid="stMain"], [data-testid="stAppViewContainer"] { overflow: hidden; }
        /* hide the (now unused) collapsible sidebar + its toggle */
        section[data-testid="stSidebar"] { display: none; }
        div[data-testid="stSidebarCollapsedControl"] { display: none; }

        /* ---------- Left panel (two stacked boxes) ---------- */
        .abb-card-box {
            background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
            box-shadow: 0 4px 12px rgba(16,24,40,0.04);
        }
        .abb-left-brand { padding: 0.85rem 1rem; margin-bottom: 0.55rem; }
        .abb-left-recent { padding: 0.8rem 1rem 0.55rem; margin: 0.55rem 0; }
        .abb-left-profile { padding: 0.7rem 0.9rem; }
        .abb-sq-label { margin-top: 0.1rem !important; }
        .abb-side-brand { display: flex; align-items: center; gap: 0.9rem; }
        .abb-side-brand img { width: 88px; height: auto; }
        .abb-side-brand .abb-side-title {
            font-size: 1.34rem; color: var(--ink); line-height: 1.04;
            font-weight: 700;
        }
        .abb-side-brand .abb-side-title strong { font-weight: 800; }
        .abb-side-label {
            font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.04em; color: var(--faint); margin: 0.2rem 0 0.6rem;
        }
        .abb-convo {
            padding: 0.5rem 0.55rem; border-radius: 8px; border: 1px solid transparent;
            display: flex; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.15rem;
        }
        .abb-convo:hover { background: var(--abb-red-soft); border-color: var(--abb-red-tint); }
        .abb-convo .abb-convo-title {
            font-size: 0.83rem; color: var(--ink);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .abb-convo .abb-convo-time { font-size: 0.72rem; color: var(--faint); white-space: nowrap; }
        .abb-side-link { color: var(--abb-red); font-size: 0.8rem; font-weight: 600; margin-top: 0.5rem; }
        .abb-profile { display: flex; align-items: center; gap: 0.65rem; }
        .abb-avatar {
            width: 36px; height: 36px; border-radius: 50%; background: var(--abb-red); color: #fff;
            display: flex; align-items: center; justify-content: center;
            font-size: 0.82rem; font-weight: 700; flex: 0 0 auto;
        }
        .abb-profile .abb-profile-name { font-size: 0.85rem; font-weight: 600; line-height: 1.15; }
        .abb-profile .abb-profile-role { font-size: 0.74rem; color: var(--muted); }

        [data-testid="stVerticalBlock"]:has(.abb-left-brand) {
            min-height: calc(100vh - 1rem);
            display: flex;
            flex-direction: column;
        }
        [data-testid="stVerticalBlock"]:has(.abb-left-brand) .abb-left-profile { margin-top: auto; }

        /* ---------- KPI cards ---------- */
        .abb-kpi-row {
            display: grid; grid-template-columns: 1.25fr 1.25fr 1fr 1fr 1fr;
            gap: 0.8rem; margin-bottom: 0.7rem;
        }
        .abb-kpi {
            background: var(--panel); border: 1px solid var(--line); border-radius: 11px;
            padding: 0.8rem 0.85rem; box-shadow: 0 6px 18px rgba(16,24,40,0.055);
            display: flex; flex-direction: column; gap: 0.16rem; min-height: 126px;
        }
        .abb-kpi-head { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.1rem; }
        .abb-kpi-icon {
            width: 26px; height: 26px; border-radius: 8px; background: var(--abb-red-soft);
            color: var(--abb-red); display: flex; align-items: center; justify-content: center;
            font-size: 0.82rem; flex: 0 0 auto;
        }
        .abb-kpi-title { font-size: 0.8rem; font-weight: 750; line-height: 1.05; }
        .abb-kpi-sub { font-size: 0.68rem; color: var(--faint); }
        .abb-kpi-strong { font-weight: 750; color: var(--ink); font-size: 0.84rem; }
        .abb-kpi-metric { font-size: 1.65rem; font-weight: 800; letter-spacing: 0; line-height: 1.12; }
        .abb-kpi-metric.sm { font-size: 1.45rem; }
        .abb-kpi-metric.neg { color: var(--abb-red); }
        .abb-kpi-metric.pos { color: var(--pos); }
        .abb-kpi-foot { font-size: 0.68rem; color: var(--muted); }
        .abb-pill {
            display: inline-block; margin-top: auto; padding: 0.22rem 0.5rem; border-radius: 8px;
            font-size: 0.66rem; font-weight: 650; background: var(--abb-red-soft); color: var(--abb-red-dark);
        }
        .abb-kpi-link { font-size: 0.7rem; font-weight: 600; color: var(--abb-red); margin-top: auto; }

        /* ---------- Section label ---------- */
        .abb-section-label {
            font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.04em; color: var(--muted); margin: 0.2rem 0 0.55rem;
        }

        /* ---------- Suggested question chips (targeted by key) ---------- */
        div[class*="st-key-chip_"] { margin-bottom: 0.4rem; }
        div[class*="st-key-chip_"] button {
            width: 100%; text-align: left; white-space: normal; height: auto;
            background: var(--panel); color: var(--ink); border: 1px solid var(--line);
            border-radius: 10px; padding: 0.5rem 0.65rem; font-weight: 500;
            line-height: 1.25; min-height: 0; box-shadow: 0 2px 6px rgba(16,24,40,0.03);
        }
        div[class*="st-key-chip_"] button:hover {
            border-color: var(--abb-red); color: var(--abb-red-dark); background: var(--abb-red-soft);
        }
        div[class*="st-key-chip_"] button p { font-size: 0.78rem; }

        /* ---------- Conversation ---------- */
        .abb-copilot-head { display: flex; align-items: center; gap: 0.6rem; margin: 0.25rem 0 0.45rem; }
        .abb-copilot-badge {
            width: 30px; height: 30px; border-radius: 8px;
            background: linear-gradient(135deg, var(--abb-red), var(--abb-red-dark));
            color: #fff; display: flex; align-items: center; justify-content: center; font-size: 0.9rem;
        }
        .abb-copilot-name { font-weight: 700; font-size: 0.95rem; }

        [data-testid="stChatMessage"] {
            border: 1px solid var(--line); border-radius: 12px; background: var(--panel);
            box-shadow: 0 4px 14px rgba(16,24,40,0.03); margin-bottom: 0.7rem;
        }
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) { background: #fcfcfd; }
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
            background: #f8fbff;
            border-color: #dce8ff;
        }
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) [data-testid="stMarkdownContainer"] p {
            font-weight: 600;
            color: #172554;
        }
        [data-testid="stChatMessage"] img {
            width: 100% !important;
            max-height: 420px;
            object-fit: contain;
            border: 1px solid var(--line);
            border-radius: 10px;
            background: #fff;
            padding: 0.35rem;
        }

        .abb-card {
            background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
            padding: 1rem 1.1rem; box-shadow: 0 6px 18px rgba(16,24,40,0.04);
        }
        .abb-card h4 { margin: 0 0 0.7rem; font-size: 0.92rem; font-weight: 700; }
        .abb-card .abb-card-sub { font-size: 0.74rem; color: var(--faint); font-weight: 500; }
        .abb-takeaway { display: flex; gap: 0.55rem; margin-bottom: 0.8rem; }
        .abb-takeaway .abb-tk-icon {
            width: 22px; height: 22px; border-radius: 6px; flex: 0 0 auto; background: var(--abb-red-soft);
            color: var(--abb-red); display: flex; align-items: center; justify-content: center; font-size: 0.72rem;
        }
        .abb-takeaway p { margin: 0; font-size: 0.82rem; color: var(--ink); line-height: 1.4; }

        .abb-thinking { display: flex; align-items: center; gap: 0.7rem; min-height: 38px; }
        .abb-thinking-ring {
            width: 22px; height: 22px; border-radius: 50%; border: 3px solid var(--abb-red-tint);
            border-top-color: var(--abb-red); animation: abb-spin 0.85s linear infinite; flex: 0 0 auto;
        }
        @keyframes abb-spin { to { transform: rotate(360deg); } }

        /* ---------- Suggested questions box ---------- */
        [class*="st-key-sq_box"] {
            background: var(--panel) !important;
            border: 1px solid var(--line) !important;
            border-radius: 14px !important;
            padding: 0.55rem 0.55rem 0.35rem !important;
            box-shadow: 0 4px 12px rgba(16,24,40,0.04) !important;
            margin-bottom: 0.7rem !important;
        }
        [class*="st-key-sq_box"] div[class*="st-key-chip_"] { margin-bottom: 0.28rem !important; }

        /* ---------- Conversation fills leftover viewport ---------- */
        /* Streamlit renders st.container(height=...) as stVerticalBlockBorderWrapper with inline height.
           Borderless containers use a different wrapper, so target both paths. */
        [class*="st-key-abb_chatbox"] {
            height: calc(100vh - 305px) !important;
            max-height: none !important;
            overflow-y: auto !important;
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 0.75rem 0.85rem 1rem !important;
            box-shadow: 0 4px 14px rgba(16,24,40,0.03);
        }
        [class*="st-key-abb_chatbox"] [data-testid="stVerticalBlockBorderWrapper"] {
            height: calc(100vh - 305px) !important;
            max-height: none !important;
            overflow-y: auto !important;
        }
        [class*="st-key-abb_chatbox"] [data-testid="stVerticalBlock"] {
            min-height: 100% !important;
        }
        [class*="st-key-abb_chatbox"] [data-testid="stChatMessage"] {
            box-shadow: none;
            border-color: #edf0f5;
        }

        /* ---------- Bottom-docked input aligned to the main column ---------- */
        [data-testid="stBottom"] { background: var(--surface); }
        [data-testid="stBottom"] > div,
        [data-testid="stBottomBlockContainer"] {
            max-width: 1800px !important;
            margin: 0 auto !important;
            padding-left: calc(20% + 2.15rem) !important;
            padding-right: 1.35rem !important;
            padding-bottom: 0.55rem !important;
        }

        /* ---------- Input: rectangular, white, mic icon inside ---------- */
        [data-testid="stChatInput"] {
            position: relative; background: #ffffff;
            border: 1px solid var(--line); border-radius: 12px;
            box-shadow: 0 6px 18px rgba(16,24,40,0.06);
            max-width: 100%;
            margin: 0 auto;
        }
        [data-testid="stChatInput"] > div { background: #ffffff; border-radius: 12px; }
        [data-testid="stChatInput"] textarea {
            background: #ffffff; border-radius: 12px;
            min-height: 56px; font-size: 0.92rem; padding-right: 92px;
            color: var(--ink);
        }
        [data-testid="stChatInput"] textarea::placeholder { color: #5f6673; opacity: 1; }
        /* dummy mic icon sitting just left of the send button */
        [data-testid="stChatInput"]::after {
            content: ""; position: absolute; right: 56px; top: 50%; transform: translateY(-50%);
            width: 20px; height: 20px; pointer-events: none; opacity: 0.6;
            background: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%236b7280' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='9' y='2' width='6' height='11' rx='3'/><path d='M5 10a7 7 0 0 0 14 0'/><line x1='12' y1='19' x2='12' y2='22'/></svg>") no-repeat center;
            background-size: contain;
        }
        /* send button -> ABB red */
        [data-testid="stChatInputSubmitButton"] svg { fill: var(--abb-red); color: var(--abb-red); }

        @media (max-width: 1100px) { .abb-kpi-row { grid-template-columns: 1fr 1fr; } }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Page layout: fixed left panel + main area
# ---------------------------------------------------------------------------
left_col, main_col = st.columns([0.2, 0.8], gap="medium")


# ---------------------------------------------------------------------------
# Left panel  (ABB logo + Recent Conversations + profile)
# ---------------------------------------------------------------------------
convo_html = "".join(
    f'<div class="abb-convo"><span class="abb-convo-title">{title}</span>'
    f'<span class="abb-convo-time">{time}</span></div>'
    for title, time in RECENT_CONVERSATIONS
)
with left_col:
    st.markdown(
        _html(
            f"""
            <div class="abb-card-box abb-left-brand">
                <div class="abb-side-brand">
                    <img src="{ABB_LOGO_DATA_URL}" alt="ABB" />
                    <div class="abb-side-title">Decision Insights <strong>Copilot</strong></div>
                </div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

    # Suggested questions — wrapped in a card box
    with st.container(key="sq_box"):
        st.markdown('<p class="abb-side-label abb-sq-label" style="margin:0.1rem 0 0.5rem">Suggested questions</p>', unsafe_allow_html=True)
        for idx, (icon, question) in enumerate(SUGGESTED_QUESTIONS):
            if st.button(f"{icon}  {question}", key=f"chip_{idx}", use_container_width=True, disabled=is_processing):
                submit_prompt(question)
                st.rerun()

    # Recent conversations + profile
    st.markdown(
        _html(
            f"""
            <div class="abb-card-box abb-left-recent">
                <p class="abb-side-label">Recent conversations</p>
                {convo_html}
            </div>
            <div class="abb-card-box abb-left-profile">
                <div class="abb-profile">
                    <div class="abb-avatar">AM</div>
                    <div>
                        <div class="abb-profile-name">Anisha Mahanty</div>
                        <div class="abb-profile-role">Data Scientist</div>
                    </div>
                </div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
with main_col:
    # ---- Top KPI cards (F–J, dummy data) ----
    st.markdown(
        _html(
            f"""
            <div class="abb-kpi-row">
                <div class="abb-kpi">
                    <div class="abb-kpi-head">
                        <div class="abb-kpi-icon">▦</div>
                        <div>
                            <div class="abb-kpi-title">Orders Growth (ELSP)</div>
                            <div class="abb-kpi-sub">vs previous 6 months</div>
                        </div>
                    </div>
                    <div class="abb-kpi-metric neg">-18.7%</div>
                    <div class="abb-kpi-sub">Total Orders · <strong style="color:var(--ink);font-size:0.82rem;">$2.42B</strong></div>
                    <span class="abb-pill">↓ Declined vs previous period</span>
                </div>
                <div class="abb-kpi">
                    <div class="abb-kpi-head">
                        <div class="abb-kpi-icon">📈</div>
                        <div>
                            <div class="abb-kpi-title">Orders Trend</div>
                            <div class="abb-kpi-sub">Last 12 months</div>
                        </div>
                    </div>
                    <svg viewBox="0 0 120 44" width="100%" height="40" preserveAspectRatio="none"><polyline points="{SPARK_TREND}" fill="none" stroke="#ff000f" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    <div class="abb-kpi-foot">Trending down · <strong style="color:#ff000f;">-18.7%</strong></div>
                </div>
                <div class="abb-kpi">
                    <div class="abb-kpi-head">
                        <div class="abb-kpi-icon">▼</div>
                        <div>
                            <div class="abb-kpi-title">Top Negative Driver</div>
                            <div class="abb-kpi-sub">Impact %</div>
                        </div>
                    </div>
                    <div class="abb-kpi-strong">Data Center Demand</div>
                    <div class="abb-kpi-metric sm neg">-7.2%</div>
                    <div class="abb-kpi-sub">vs previous 6 months</div>
                </div>
                <div class="abb-kpi">
                    <div class="abb-kpi-head">
                        <div class="abb-kpi-icon">▲</div>
                        <div>
                            <div class="abb-kpi-title">Top Positive Driver</div>
                            <div class="abb-kpi-sub">Impact %</div>
                        </div>
                    </div>
                    <div class="abb-kpi-strong">Pricing / Realization</div>
                    <div class="abb-kpi-metric sm pos">+4.3%</div>
                    <div class="abb-kpi-sub">vs previous 6 months</div>
                </div>
                <div class="abb-kpi">
                    <div class="abb-kpi-head">
                        <div class="abb-kpi-icon">⚠</div>
                        <div>
                            <div class="abb-kpi-title">Largest Anomaly</div>
                            <div class="abb-kpi-sub">vs normal range</div>
                        </div>
                    </div>
                    <div class="abb-kpi-strong">Data Center Demand</div>
                    <div class="abb-kpi-metric sm neg">-7.2%</div>
                    <div class="abb-kpi-link">View details →</div>
                </div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

    # ---- Conversation ----
    st.markdown(
        _html(
            """
            <div class="abb-copilot-head">
                <div class="abb-copilot-badge">✦</div>
                <div class="abb-copilot-name">ABB Copilot</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

    conversation = st.container(height=CHAT_HEIGHT, border=False, key="abb_chatbox")
    with conversation:
        if not st.session_state.messages and not is_processing:
            # Showcase / empty state: dummy answer + merged plot + key takeaways
            with st.chat_message("assistant"):
                st.markdown(
                    "ELSP orders declined by **18.7%** in the last 6 months "
                    "(Jan – Jun 2024 vs Jul – Dec 2023). The decline was driven primarily by "
                    "weaker **Data Center demand**, partially offset by **pricing benefits**."
                )
                col_plot, col_takeaways = st.columns([1.5, 1], gap="medium")
                with col_plot:
                    st.markdown(
                        _html(
                            '<div class="abb-card"><h4>Driver Contribution '
                            '<span class="abb-card-sub">· Jan – Jun 2024 vs Jul – Dec 2023</span></h4></div>'
                        ),
                        unsafe_allow_html=True,
                    )
                    st.image(get_showcase_plot(), use_container_width=True)
                with col_takeaways:
                    st.markdown(
                        _html(
                            """
                            <div class="abb-card">
                                <h4>Key Takeaways</h4>
                                <div class="abb-takeaway"><div class="abb-tk-icon">↓</div>
                                    <p>Data Center Demand decline was the largest contributor (-7.2%).</p></div>
                                <div class="abb-takeaway"><div class="abb-tk-icon">↑</div>
                                    <p>Pricing gains (+4.3% overall) helped offset part of the decline.</p></div>
                                <div class="abb-takeaway"><div class="abb-tk-icon">i</div>
                                    <p>Enterprise Demand showed improvement in the period.</p></div>
                            </div>
                            """
                        ),
                        unsafe_allow_html=True,
                    )

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                render_message_content(message["content"])

        if st.session_state.streaming_response:
            response_to_stream = st.session_state.streaming_response
            with st.chat_message("assistant"):
                render_streaming_response(response_to_stream)
            st.session_state.messages.append({"role": "assistant", "content": response_to_stream})
            st.session_state.streaming_response = None

        if is_processing:
            with st.chat_message("assistant"):
                st.markdown(
                    _html(
                        """
                        <div class="abb-thinking">
                            <span class="abb-thinking-ring"></span>
                            <strong>Working on it…</strong>
                        </div>
                        """
                    ),
                    unsafe_allow_html=True,
                )

# ---- Input bar: docked at the very bottom (native Streamlit bottom dock), aligned to main column ----
prompt = st.chat_input("Ask anything about your financial data…", disabled=is_processing)
if prompt and not is_processing:
    submit_prompt(prompt)
    st.rerun()


# ---------------------------------------------------------------------------
# Run the agent for a queued prompt
# ---------------------------------------------------------------------------
if st.session_state.pending_prompt:
    pending_prompt = st.session_state.pending_prompt
    history = st.session_state.messages
    if history and history[-1]["role"] == "user" and history[-1]["content"] == pending_prompt:
        history = history[:-1]

    set_trace_callback(None)
    try:
        response = agent.respond(pending_prompt, history)
    except Exception as exc:
        response = (
            "I ran into an error while processing that request. "
            f"Details: {exc}"
        )

    st.session_state.streaming_response = response
    st.session_state.pending_prompt = None
    st.rerun()
