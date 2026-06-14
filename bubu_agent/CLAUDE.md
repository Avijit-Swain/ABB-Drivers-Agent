# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A prototype "Decision Insights Copilot" with two separate frontends sharing a single agent backend. A LangGraph ReAct-style agent (`agent.py`) answers business questions over a small SQLite knowledge base, explains KPI definitions, runs what-if simulations, and generates Matplotlib plots.

## Commands

```bash
pip install -r requirements.txt

# Streamlit frontend (default)
streamlit run app.py

# React frontend (alternative, port 8500)
python server.py
```

There is no test suite, linter, or build step. Validation is done by running the app and exercising it manually.

### Required environment (`.env`)

`agent.py` instantiates the agent at import time and **raises `RuntimeError` if `OPENAI_API_KEY` is missing** — the app will not start without it. `OPENAI_MODEL` defaults to `gpt-4.1`. The Azure keys present in `.env` are not currently wired into `agent.py` (it only uses `ChatOpenAI`).

## Two frontends, one agent

- **`app.py`** — Streamlit UI. Imports `agent` at module load, wires the live-trace panel via `set_trace_callback`, drives a `pending_prompt` state machine so Streamlit can show a spinner before `agent.respond` blocks.
- **`server.py`** — stdlib `ThreadingHTTPServer` that serves the static React app from `react_app/` (port 8500) and exposes `POST /api/chat`. Does **not** wire `set_trace_callback`; tracing is a Streamlit-only feature.
- **`react_app/`** — `index.html` / `app.js` / `styles.css`. The compiled static assets for the React frontend; served directly by `server.py`.
- **`app_backup_pre_redesign.py`** — earlier Streamlit UI before the current design; not the active app.

## Architecture

### Single-graph agent with four tools (`agent.py`)

`BubuAgent` compiles a LangGraph `StateGraph` over `MessagesState`: `assistant` (LLM with bound tools) → conditional router → `ToolNode` → `tool_result_trace` → back to `assistant`, until the LLM returns no tool calls (`recursion_limit: 12`). Entry point is `agent.respond(user_message, history)`. The four tools are the only way the agent touches data:

- **`structured_data_tool`** — two-stage routing: `_select_tables` asks the LLM to pick among the three SQLite tables (`Forecast_KB`, `Driver_Contribution_KB`, `Monthly_Data`), then `_generate_sql` generates a SELECT using only the chosen table's schema + sample rows. Generated SQL passes through `_validate_select_sql` (SELECT-only, single statement, blocks DDL/DML). When the LLM errors, keyword-based `_fallback_table` / `_fallback_sql` take over. Results are cached in the module-global `LAST_STRUCTURED_RESULT` for follow-up plotting.
- **`unstructured_kpi_tool`** — answers KPI definition/meaning questions from `data/kpi_definitions_unstructured.txt` (falls back to `KPI_DEFINITIONS_FALLBACK` baked into `agent.py` if the file is missing).
- **`simulation_tool`** — pure-Python what-if calc, no LLM, no DB. Applies `final_growth = alpha + Σ(weight × driver_growth)` using hardcoded weights and aliases for divisions ELSP and ELSB. **Alpha is intentionally never surfaced to the user** (enforced by the system prompt).
- **`plot_tool`** — LLM generates Matplotlib code from recent messages, then `_run_generated_plot_code` runs it via `exec` in a **restricted `safe_globals`** (whitelisted builtins + `pd`/`plt` only) with `plt.show` monkeypatched to save PNGs to `plots/` (+ `plots/latest_plot.png`). Two fallback layers: if the LLM produces no code or the code throws, `_fallback_plot_plan_from_cache` + `_generate_code_from_fallback_plan` build deterministic code from the cached structured result.

### Plot fast-path bypasses the graph

In `BubuAgent.respond`, if there is cached structured data and `_is_followup_plot_request` matches (e.g. "plot this", "line chart"), `plot_tool` is invoked **directly**, skipping the LLM graph entirely. Otherwise the cached structured result is injected as a `SystemMessage` so the in-graph LLM can choose to plot it. Keep this dual path in mind when changing plotting behavior.

### Behavior lives in prompts

The agent's routing rules, tone, response formatting, and the "Can I help you with anything else?" closing are all defined in `build_assistant_system_prompt()`. The per-table routing/SQL rules live in `TABLE_DESCRIPTIONS` and the prompts inside `_select_tables` / `_generate_sql`. Change behavior there, not in control flow.

### UI ↔ agent contract — magic prefixes (both frontends)

`plot_tool` returns plain text lines like `Plot generated at: <path>`. Both `app.py` (`parse_message_content`) and `server.py` (`_parse_agent_content`) parse these prefixes, hide the technical lines, and render the plot inline. **Changing the tool's return format requires updating the parser in both files.**

The same two-file coupling applies to the `SQL used:` block stripping and `Latest plot copy:` lines.

## Data layout — important

There are **two unrelated SQLite databases**; only one is live:

- **Active runtime DB**: `new artifacts/driver_analysis_chatbot.db` (referenced by `DB_PATH` in `agent.py`). Tables: `Forecast_KB`, `Driver_Contribution_KB`, `Monthly_Data`. This is what the running app queries.
- **Legacy/unused at runtime**: `import_workbook.py` builds `data/driver_analysis_workbook.db` from an Excel workbook; `generate_sample_assets.py` builds `data/abb_sample.db`. These are earlier-iteration artifacts; the live agent does **not** read them.

If you change the structured tables, edit `new artifacts/driver_analysis_chatbot.db` and keep `VALID_TABLES` + `TABLE_DESCRIPTIONS` in `agent.py` in sync — the router and SQL generator rely on those descriptions matching the real columns.

`plots/` and `data/long_term_memory.txt` are gitignored generated output. `data/long_term_memory.txt` is **not currently used** by the agent.
