# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A prototype "Decision Insights Copilot" with two separate frontends sharing a single agent backend. A LangGraph ReAct-style agent (`agent.py`) answers business questions over a small SQLite knowledge base, explains KPI definitions, runs what-if simulations, and generates Plotly charts.

## Commands

```bash
# One-time setup after cloning (creates .venv and installs all deps)
./setup.sh

# Streamlit frontend (default)
./run_app.sh

# React frontend (port 8500 by default, pass a port to override)
./run_server.sh
./run_server.sh 8503
```

There is no test suite, linter, or build step. Validation is done by running the app and exercising it manually.

**Always use the shell scripts** — they activate the `.venv` created by `setup.sh`. Running `python server.py` or `streamlit run app.py` directly may pick up the wrong system Python (e.g. macOS Xcode Python 3.9) which is missing `reportlab`, `kaleido`, and other deps.

### Required environment (`.env`)

`agent.py` instantiates the agent at import time and **raises `RuntimeError` if `OPENAI_API_KEY` is missing** — the app will not start without it. `OPENAI_MODEL` defaults to `gpt-4.1`. Optional email keys (`EMAIL_USER`, `EMAIL_PASSWORD`) are used by `server.py` for an email-report feature. The Azure keys in `.env` are not wired into `agent.py`.

For Gmail SMTP on networks where STARTTLS port 587 is reset, use SSL port 465:

```env
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=465
EMAIL_USE_SSL=true
EMAIL_USER=...
EMAIL_PASSWORD=...
```

## Two frontends, one agent

- **`app.py`** — Streamlit UI. Imports `agent` at module load, wires the live-trace panel via `set_trace_callback`, drives a `pending_prompt` state machine so Streamlit can show a spinner before `agent.respond` blocks.
- **`server.py`** — stdlib `ThreadingHTTPServer` serving the static React app from `react_app/` (port 8500); exposes `POST /api/chat` (calls `agent.respond_stream`) and an email-report endpoint. Does **not** wire `set_trace_callback`.
- **`react_app/`** — `index.html` / `app.js` / `styles.css`. Static assets served directly by `server.py`.
- **`app_backup_pre_redesign.py`** — earlier Streamlit UI; not the active app.

## Architecture

### Five-node graph (`agent.py`)

`BubuAgent` compiles a LangGraph `StateGraph` over `MessagesState` with these nodes:

```
START → assistant → (tools | summarization)
                     ↓
                tool_result_trace → auto_plot_check → assistant
                summarization → END
```

- **`assistant`** — LLM with bound tools; routes to `tools` if tool calls are present, else to `summarization`.
- **`tools`** — `ToolNode` executes the called tool.
- **`tool_result_trace`** — emits a trace step with row counts; returns no new messages.
- **`auto_plot_check`** — runs `_run_plotly_pipeline` automatically **after every `structured_data_tool` call** (when `_THIS_TURN_STRUCTURED["ran"]` is True). This is the primary plot path — `plot_tool` is only invoked directly for follow-up or explicit chart requests.
- **`summarization`** — dedicated LLM call using `build_summarization_prompt()` that formats the final answer with bold numbers, bullet points, and specific markdown rules. This is the only node that produces the user-facing response.

Entry points: `agent.respond(user_message, history, conversation_id)` (blocking) and `agent.respond_stream(...)` (generator yielding event dicts with `type`, `node`, `content` keys — used by `server.py`).

`recursion_limit` is 12.

### Four tools

- **`structured_data_tool`** — two-stage routing: `_select_tables` asks the LLM to pick among `Forecast_KB`, `Driver_Contribution_KB`, `Monthly_Data`; then `_generate_sql` generates a SELECT using only the chosen table's schema + sample rows. SQL passes through `_validate_select_sql` (SELECT-only, blocks DDL/DML, single statement). Results are cached in `LAST_STRUCTURED_RESULT` and also saved to a per-conversation CSV under `data/structured_results/` (keyed by `conversation_id + timestamp`) for durable follow-up plotting across turns.
- **`unstructured_kpi_tool`** — answers KPI definition/meaning questions and **driver selection reasoning** (why a driver was or wasn't chosen) from `data/kpi_definitions_unstructured.txt`. Falls back to `KPI_DEFINITIONS_FALLBACK` in `agent.py` if the file is missing.
- **`simulation_tool`** — pure-Python what-if calc. `final_growth = alpha + Σ(weight × driver_growth)`. Hardcoded weights and aliases for ELSP and ELSB only. **Alpha is intentionally never surfaced to the user** (enforced by the system prompt).
- **`plot_tool`** — invokes `_run_plotly_pipeline` directly. Only used for explicit follow-up chart requests; the in-graph `auto_plot_check` node handles the automatic post-query plot.

### Plotly pipeline (`_run_plotly_pipeline`)

Plot generation uses structured LLM output, not `exec`:

1. `_get_plot_spec(plot_request, df)` — LLM returns a `PlotSpec` Pydantic model (`chart_type`, `x_col`, `y_cols`, `title`, `x_label`, `y_label`, optional `y2_label`/`colors`) using `with_structured_output(PlotSpec)`.
2. A dedicated `_plot_*` function renders the Plotly figure using the spec: `_plot_line`, `_plot_bar`, `_plot_bar_horizontal`, `_plot_bar_colored`, `_plot_bar_stacked`, `_plot_waterfall`, `_plot_dual_axis_line`.
3. `_abb_layout` applies consistent ABB styling (`_ABB_COLORS = ["#FF000F", ...]`).
4. Figure is saved via `kaleido` to `plots/<timestamp>_plotly.png` and copied to `plots/latest_plot.png`.
5. Long-format DataFrames with multiple `division` values are auto-pivoted to wide format before spec generation.

### Plot fast-path bypasses the graph

In `BubuAgent.respond` and `respond_stream`, if a CSV path exists (from current turn or restored from `data/structured_results/`) and `_is_followup_plot_request` matches, `plot_tool` is invoked **directly**, skipping the graph entirely.

### Behavior lives in prompts

`build_assistant_system_prompt()` controls routing rules, tone, and response behavior. `build_summarization_prompt()` controls final answer formatting. Per-table routing/SQL rules live in `TABLE_DESCRIPTIONS` and the prompts inside `_select_tables` / `_generate_sql`.

### UI ↔ agent contract — magic prefixes (both frontends)

`plot_tool` and `_run_plotly_pipeline` return plain text with `Plot generated at: <path>`. Both `app.py` (`parse_message_content`) and `server.py` (`_parse_agent_content`) parse these prefixes, hide them from the user, and render the plot inline. **Changing the tool's return format requires updating the parser in both files.** Same applies to `SQL used:` block stripping and `Latest plot copy:` lines.

## Data layout — important

Two unrelated SQLite databases exist; only one is live:

- **Active runtime DB**: `new artifacts/driver_analysis_chatbot.db` (referenced by `DB_PATH` in `agent.py`). Tables: `Forecast_KB`, `Driver_Contribution_KB`, `Monthly_Data`.
- **Legacy/unused**: `data/driver_analysis_workbook.db` (built by `import_workbook.py`) and `data/abb_sample.db` (built by `generate_sample_assets.py`). The live agent does **not** read them.

If you change the structured tables, edit `new artifacts/driver_analysis_chatbot.db` and keep `VALID_TABLES` + `TABLE_DESCRIPTIONS` in `agent.py` in sync.

`plots/`, `data/structured_results/`, and `data/long_term_memory.txt` are gitignored generated output. `data/long_term_memory.txt` is **not currently used** by the agent.
