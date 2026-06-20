import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

import pandas as pd
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel


load_dotenv()

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "driver_analysis_chatbot.db"
PLOTS_DIR = APP_DIR / "plots"
STRUCTURED_RESULTS_DIR = DATA_DIR / "structured_results"
UNSTRUCTURED_TEXT_PATH = DATA_DIR / "kpi_definitions_unstructured.txt"

_tl = threading.local()  # stores: cid (set in respond() before graph invocation)

# Module-level shared state — visible across all threads (tool threads + graph threads)
_CURRENT_CID: dict = {"value": "unknown"}      # set in respond() before graph runs
_LAST_CSV_PATH: dict = {"path": ""}            # set by structured_data_tool after CSV save
_REQUEST_PLOT: dict = {"path": "", "latest": ""}  # set by _run_plotly_pipeline; cleared per request
_THIS_TURN_STRUCTURED: dict = {"ran": False}   # True only when structured_data_tool ran this turn

TRACE_CALLBACK = None
LAST_STRUCTURED_RESULT = {
    "question": "",
    "result": "",
}


class PlotSpec(BaseModel):
    chart_type: Literal["line", "bar", "bar_horizontal", "bar_colored", "bar_stacked", "dual_axis_line", "waterfall"]
    x_col: str
    y_cols: List[str]
    title: str
    x_label: str
    y_label: str
    y2_label: Optional[str] = None  # only for dual_axis_line
    colors: Optional[List[str]] = None  # user-specified colors; None = use defaults


class SummarizedResponse(BaseModel):
    summary_markdown: str
    details_markdown: str


def set_trace_callback(callback):
    global TRACE_CALLBACK
    TRACE_CALLBACK = callback


def _emit_step(title: str, detail: str = ""):
    if TRACE_CALLBACK is None:
        return
    try:
        TRACE_CALLBACK(title, detail)
    except Exception:
        pass


VALID_TABLES = ["Forecast_KB", "Driver_Contribution_KB", "Monthly_Data"]

TABLE_DESCRIPTIONS = {
    "Forecast_KB": """
Purpose:
Forecast_KB contains division-level forecast, baseline, actual growth, alpha,
and bear/base/bull forecast values with lower and upper bounds.

Use this table for:
- baseline orders
- actual orders
- actual growth percentage
- alpha percentage
- bear/base/bull point forecasts
- bear/base/bull lower and upper bounds
- forecast uncertainty ranges
- division-level forecast summary

Important columns:
- division: business division — ELSP, ELSB, or ELDS
- baseline_year: baseline year, usually 2024
- forecast_year: forecast year, usually 2025
- baseline_orders_musd: baseline orders in million USD
- actual_orders_musd: actual orders in million USD
- actual_growth_pct: actual order growth percentage
- alpha_pct: alpha/intercept percentage used in scenario planning
- bear_point_forecast_pct, bear_lower_bound_pct, bear_upper_bound_pct
- base_point_forecast_pct, base_lower_bound_pct, base_upper_bound_pct
- bull_point_forecast_pct, bull_lower_bound_pct, bull_upper_bound_pct
- usage_note: usage guidance for this row

Example questions:
- What is the baseline order value for ELSP?
- What is the actual growth for ELSB?
- Show bear, base, and bull forecasts for ELSP.
- What are the lower and upper bounds for the base forecast?
- What is the alpha value for ELDS?
""",
    "Driver_Contribution_KB": """
Purpose:
Driver_Contribution_KB contains selected driver information, elasticity,
scenario growth assumptions, recommended bounds, contribution, and impact outputs.

Use this table for:
- selected drivers
- driver elasticity
- pessimistic/current/optimistic driver growth assumptions
- recommended minimum and maximum growth range
- driver direction and rationale
- impact percentage and impact in million USD
- contribution percentage
- positive contributors and negative drags
- waterfall, contribution, bridge, or decomposition values
- which drivers are common across divisions

Important columns:
- division: business division — ELSP, ELSB, or ELDS
- selected_driver_name: selected driver/KPI name
- elasticity: estimated driver elasticity
- pessimistic_growth_pct, current_growth_pct, optimistic_growth_pct
- recommended_min_pct, recommended_max_pct
- driver_direction: expected direction of relationship
- rationale_short: short rationale for why the driver was selected
- baseline_orders_musd: baseline orders in million USD
- impact_pct_current: current impact percentage on orders
- impact_musd_current: current impact in million USD
- contribution_pct: absolute contribution or importance share
- contribution_note: whether the driver is a positive contributor or negative drag
- usage_note: guidance on how to use this row
- is_common_driver: whether this driver is shared across multiple divisions (Yes/No)

Example questions:
- What are the selected drivers for ELSP?
- Show elasticity of Data Center for ELSP.
- What is the recommended range for US Utility Capex?
- Which drivers are common across all divisions?
- Which driver contributed the most to ELDS orders?
- Show contribution impact in MUSD for ELSB.
- Which drivers are positive contributors and negative drags?
""",
    "Monthly_Data": """
Purpose:
Monthly_Data contains monthly historical values for orders and all candidate
external/internal drivers across divisions.

Use this table for:
- monthly orders
- historical trends
- movement over time
- monthly driver values
- comparing orders against a driver over time
- time-series data by division

Important columns:
- date: monthly date
- division: business division — ELSP, ELSB, or ELDS
- orders_received_net_musd: monthly Orders Received Net in million USD

Existing driver columns (unchanged):
- data_center_hyperscaler: monthly Data Center / Hyperscaler value
- us_utility_capex: monthly US Utility Capex value
- operational_sales_expenses: monthly Operational Sales Expenses value
- iron_steel_ppi: monthly Iron & Steel PPI value
- us_computer_products: monthly US Computer Products value
- copper_price: monthly Copper Price value
- us_gdp: monthly US GDP value
- china_iip: monthly China IIP value
- europe_producer_price_index: monthly Europe Producer Price Index value

New driver columns added:
- electrical_equipment_ppi: monthly Electrical Equipment PPI value
- general_industrial_production: monthly General Industrial Production index
- manufacturing_pmi: monthly Manufacturing PMI value
- consumer_confidence_index: monthly Consumer Confidence Index value
- residential_housing_starts: monthly Residential Housing Starts value
- crude_oil_price: monthly Crude Oil Price value
- cpi_all_items: monthly CPI All Items (inflation) value
- interest_rate: monthly Interest Rate value
- exchange_rate_index: monthly Exchange Rate Index value
- construction_spending_index: monthly Construction Spending Index value
- retail_sales_index: monthly Retail Sales Index value
- residential_building_permits: monthly Residential Building Permits value
- grid_investment_index: monthly Grid Investment Index value
- renewable_energy_capacity_additions: monthly Renewable Energy Capacity Additions value
- transformer_raw_material_index: monthly Transformer Raw Material Index value
- industrial_automation_capex: monthly Industrial Automation Capex value

Example questions:
- Show monthly orders for ELDS.
- How did Electrical Equipment PPI move over time for ELSP?
- Compare Grid Investment Index against ELDS orders.
- What was the monthly trend for ELSB orders?
- Show historical movement of Manufacturing PMI and orders.
""",
}


KPI_DEFINITIONS_FALLBACK = """
KPI Definitions for Decision Insights Copilot

Data Center / Hyperscaler
Category: Sector demand indicator
Definition: Measures demand, investment, or activity related to data centers and hyperscale infrastructure.
Business meaning: Higher data center activity may indicate stronger electrification, power, grid, automation, and infrastructure demand.
Possible synonyms / user terms: hyperscaler, data center growth, cloud infra, server infrastructure, AI data center demand

US GDP
Category: Macro indicator
Definition: Gross Domestic Product of the United States. It measures the total economic output of the US economy.
Business meaning: Higher GDP generally indicates stronger economic activity and can support business demand.

China GDP
Category: Macro indicator
Definition: Gross Domestic Product of China. It measures the total economic output of China.
Business meaning: Higher China GDP can indicate stronger economic activity and broader demand conditions in China.

China IIP
Category: Industrial indicator
Definition: Index of Industrial Production for China. It measures changes in industrial output such as manufacturing, mining, and utilities.
Business meaning: Higher IIP indicates stronger industrial activity and may increase demand for industrial products.

US Computer Products
Category: Sector/manufacturing indicator
Definition: Measures manufacturing activity for computer, electronic, and optical products in the US.

US Utility Capex
Category: Infrastructure investment indicator
Definition: Capital expenditure by utility companies in the US.
Business meaning: Higher utility capex may indicate more investment in power infrastructure, grid expansion, and electrification projects.

Copper Price
Category: Commodity indicator
Definition: Market price of copper, a key industrial metal used in electrical equipment, cables, and infrastructure.

Europe Producer Price Index
Category: Price/inflation indicator
Definition: Measures changes in prices received by producers for goods and services in Europe.
"""


def _build_llm():
    api_key = os.getenv("OPENAI_API_KEY")
    model_name = os.getenv("OPENAI_MODEL", "gpt-4.1")

    if not api_key:
        return None

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        temperature=0,
    )


def _parse_json_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    return json.loads(content)


def _validate_select_sql(sql: str):
    normalized = sql.strip().lower()
    if not normalized.startswith("select"):
        raise ValueError("Only SELECT queries are allowed.")
    blocked = ["insert ", "update ", "delete ", "drop ", "alter ", "create ", "pragma ", "attach "]
    if any(token in normalized for token in blocked):
        raise ValueError("Unsafe SQL statement rejected.")
    if ";" in normalized.rstrip(";"):
        raise ValueError("Only one SQL statement is allowed.")


def _get_all_table_descriptions() -> str:
    lines = []
    for table_name, description in TABLE_DESCRIPTIONS.items():
        lines.append(f"Table name: {table_name}")
        lines.append(description.strip())
        lines.append("\n" + "-" * 80 + "\n")
    return "\n".join(lines)


def _get_table_info(table_name: str) -> str:
    table_description = TABLE_DESCRIPTIONS.get(table_name, "")

    with sqlite3.connect(DB_PATH) as conn:
        columns = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        conn.row_factory = sqlite3.Row
        sample_rows = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 5').fetchall()
        sample_rows = [dict(row) for row in sample_rows]

    lines = [
        f"Table name: {table_name}",
        "",
        "Table description:",
        table_description.strip(),
        "",
        "Actual SQL columns:",
    ]

    for column in columns:
        lines.append(f"- {column[1]}: {column[2]}")

    lines.append("")
    lines.append("Sample rows:")
    lines.append(json.dumps(sample_rows, indent=2, default=str))
    return "\n".join(lines)


def _select_tables(question: str) -> dict:
    llm = _build_llm()
    fallback_table = _fallback_table(question)
    _emit_step("Structured router", "Selecting relevant table from three-table knowledge base.")

    if llm is None:
        return {"status": "answerable", "tables": [{"table_name": fallback_table, "question_part": question}]}

    prompt = f"""
You are selecting the correct SQL table(s) for a Decision Insights Copilot.

Your job:
Decide whether the user's question can be answered using the structured SQL tables.

Structured table descriptions:
{_get_all_table_descriptions()}

Return JSON only.

If answerable from structured data, return:
{{
  "status": "answerable",
  "tables": [
    {{
      "table_name": "Forecast_KB",
      "question_part": "specific part of the user question this table should answer"
    }}
  ]
}}

If the question needs multiple tables, return multiple table objects. If one
table can answer the full question, prefer one table.

If unclear or not answerable from structured data, return:
{{
  "status": "clarification_needed",
  "clarification_question": "short clarification question to ask the user"
}}

Available tables:
- Forecast_KB
- Driver_Contribution_KB
- Monthly_Data

Routing rules:
- Forecast_KB: baseline orders, actual orders, actual growth, alpha, bear/base/bull forecasts, forecast lower/upper bounds, forecast uncertainty.
- Driver_Contribution_KB: selected drivers, elasticity, pessimistic/current/optimistic growth, recommended range, driver rationale, contribution, impact, waterfall, bridge, decomposition.
- Monthly_Data: monthly orders, historical trends, movement over time, month-wise data, comparing orders against a driver over time, historical KPI movement.

Do not use structured tables for KPI definitions, KPI meanings, synonyms,
misspelled KPI interpretation, conceptual questions, plot-only requests,
simulation/what-if requests, or user feedback.
"""

    try:
        response = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=question)])
        selected = _parse_json_response(response.content)
    except Exception:
        selected = {"status": "answerable", "tables": [{"table_name": fallback_table, "question_part": question}]}

    for item in selected.get("tables", []):
        table_name = item.get("table_name")
        if table_name in VALID_TABLES:
            _emit_step("Table selected", table_name)
            break
    return selected


def _generate_sql(question_part: str, table_name: str) -> str:
    llm = _build_llm()
    table_info = _get_table_info(table_name)
    _emit_step("Text-to-SQL", f"Generating SQL using only {table_name}.")

    if llm is None:
        return _fallback_sql(question_part, table_name)

    prompt = f"""
You are a SQLite SQL generator for a driver-analysis chatbot.

Generate exactly one SQLite SELECT query for the user's question.

Use only this selected table information:
{table_info}

Return JSON only:
{{
  "sql": "SELECT ..."
}}

Rules:
- Use only table: {table_name}.
- Use only columns listed in the selected table information.
- Do not use any other table.
- Do not invent columns or table names.
- The SQL must be read-only SELECT.
- Add LIMIT 50 unless aggregating or grouping.
- If the user asks for all rows, still add LIMIT 50.
- Use LOWER(column_name) = LOWER('entity_value') for exact text filters.
- For driver/KPI names where formatting may differ, use lowercase LIKE only on the relevant column.
- Never drop a user-provided filter just because it may not match.
- For Monthly_Data time filtering or ordering, use date.
- If the user asks for highest, lowest, top, or bottom, ORDER BY the relevant numeric column.
- For plot data, return simple columns that can be plotted, ideally label/value or date/value.
- CRITICAL: If the user asks to compare BOTH divisions (mentions ELSP and ELSB together), write ONE single query with NO division WHERE filter. Always include the `division` column in SELECT so both divisions are returned together. Use LIMIT 120 for Monthly_Data comparisons. Example: SELECT date, division, orders_received_net_musd FROM Monthly_Data ORDER BY date LIMIT 120
- If only one division is mentioned, filter with LOWER(division) = LOWER('ELSP') or LOWER('ELSB') and LIMIT 50.
"""

    try:
        response = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=question_part)])
        sql = _parse_json_response(response.content)["sql"].strip()
        _validate_select_sql(sql)
        _emit_step("SQL generated", sql[:260])
        return sql
    except Exception:
        sql = _fallback_sql(question_part, table_name)
        _emit_step("SQL fallback", sql[:260])
        return sql


def _run_sql(sql: str) -> list[dict]:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")
    _emit_step("Structured data", "Running SQL and retrieving rows.")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
    return [dict(row) for row in rows]


def _cache_structured_result(question: str, result: str):
    LAST_STRUCTURED_RESULT["question"] = question
    LAST_STRUCTURED_RESULT["result"] = result


def _structured_cache_context() -> str:
    if not LAST_STRUCTURED_RESULT.get("result"):
        return ""

    return (
        "Latest structured data result available for plotting:\n"
        f"Original structured question: {LAST_STRUCTURED_RESULT.get('question', '')}\n"
        f"Structured result JSON:\n{LAST_STRUCTURED_RESULT.get('result', '')}"
    )


def _restore_csv_path(conversation_id: str) -> str:
    """Return the most recent CSV saved for this conversation (for follow-up plot requests)."""
    if not conversation_id or conversation_id == "unknown":
        return ""
    try:
        matches = sorted(
            STRUCTURED_RESULTS_DIR.glob(f"{conversation_id}_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(matches[0]) if matches else ""
    except Exception:
        return ""


def _fallback_table(question: str) -> str:
    normalized = question.lower()
    if any(term in normalized for term in ["monthly", "trend", "over time", "time series", "against", "versus", " vs ", "historical"]):
        return "Monthly_Data"
    if any(term in normalized for term in ["driver", "elasticity", "range", "contribution", "impact", "waterfall", "bridge", "decomposition"]):
        return "Driver_Contribution_KB"
    return "Forecast_KB"


def _fallback_sql(question: str, table_name: str) -> str:
    normalized = question.lower()
    has_elsp = "elsp" in normalized
    has_elsb = "elsb" in normalized
    has_elds = "elds" in normalized
    division_filter = ""
    if has_elsp and not has_elsb and not has_elds:
        division_filter = " WHERE LOWER(division) = LOWER('ELSP')"
    elif has_elsb and not has_elsp and not has_elds:
        division_filter = " WHERE LOWER(division) = LOWER('ELSB')"
    elif has_elds and not has_elsp and not has_elsb:
        division_filter = " WHERE LOWER(division) = LOWER('ELDS')"
    # multiple mentioned → no filter, return all divisions

    limit = 60 if division_filter else 180
    if table_name == "Monthly_Data":
        if "data center" in normalized or "hyperscaler" in normalized:
            return (
                "SELECT date, division, orders_received_net_musd, data_center_hyperscaler "
                f"FROM Monthly_Data{division_filter} ORDER BY date LIMIT {limit}"
            )
        return f"SELECT date, division, orders_received_net_musd FROM Monthly_Data{division_filter} ORDER BY date LIMIT {limit}"

    if table_name == "Driver_Contribution_KB":
        order = ""
        if "highest" in normalized or "top" in normalized or "most" in normalized:
            order = " ORDER BY ABS(impact_musd_current) DESC"
        return f"SELECT * FROM Driver_Contribution_KB{division_filter}{order} LIMIT 50"

    return f"SELECT * FROM Forecast_KB{division_filter} LIMIT 50"


@tool
def structured_data_tool(question: str) -> str:
    """Use for structured business-data questions from the three-table SQLite knowledge base.

    This is one structured tool. Internally it first routes the question to
    Forecast_KB, Driver_Contribution_KB, and/or Monthly_Data, then generates SQL
    using only the selected table schema and sample rows.

    Use for selected drivers, elasticities, recommended ranges, growth values,
    baseline orders, actual orders, bear/base/bull forecasts, contribution,
    impact, waterfall/decomposition values, monthly historical data, and
    structured comparisons across divisions, drivers, scenarios, or time.
    """
    try:
        selected = _select_tables(question)

        if selected.get("status") == "clarification_needed":
            return json.dumps(
                {
                    "status": "clarification_needed",
                    "question": selected.get("clarification_question", "Could you clarify the structured data request?"),
                },
                indent=2,
            )

        final_rows = {}
        sql_used = {}
        for item in selected.get("tables", []):
            table_name = item.get("table_name")
            question_part = item.get("question_part", question)
            if table_name not in VALID_TABLES:
                continue

            sql = _generate_sql(question_part, table_name)
            rows = _run_sql(sql)
            final_rows[table_name] = rows
            sql_used[table_name] = sql
            _emit_step("Structured data", f"Retrieved {len(rows)} row(s) from {table_name}.")

        if not final_rows:
            return json.dumps(
                {
                    "status": "no_result",
                    "message": "No result found. I could not identify the right structured table.",
                },
                indent=2,
            )

        if all(len(rows) == 0 for rows in final_rows.values()):
            return json.dumps(
                {
                    "status": "no_result",
                    "message": "No result found for the requested entity or filters in the structured data.",
                    "sql": sql_used,
                },
                indent=2,
            )

        _emit_step("Structured data", "Returned tool results to assistant.")
        _THIS_TURN_STRUCTURED["ran"] = True
        result = json.dumps({"status": "success", "sql": sql_used, "data": final_rows}, indent=2, default=str)
        _cache_structured_result(question, result)
        # Save result to CSV for Plotly pipeline
        try:
            STRUCTURED_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            cid = _CURRENT_CID.get("value", "unknown")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            csv_path = STRUCTURED_RESULTS_DIR / f"{cid}_{ts}.csv"
            rows_to_save = []
            for table_rows in final_rows.values():
                if isinstance(table_rows, list) and table_rows:
                    rows_to_save = table_rows
                    break
            if rows_to_save:
                pd.DataFrame(rows_to_save).to_csv(csv_path, index=False)
                _LAST_CSV_PATH["path"] = str(csv_path)
        except Exception:
            pass
        return result
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)


@tool
def unstructured_kpi_tool(question: str) -> str:
    """Use for KPI definitions, concepts, and driver selection reasoning questions.

    Use this for:
    - KPI meaning, definition, business meaning, category, synonyms
    - Misspelled or indirectly described KPI concepts
    - Why a driver was selected or not selected for a division
    - Why a driver was rejected or evaluated but not chosen
    - What outperformed a driver for a given division
    - Comparing two drivers for selection reasoning
    - Signal strength, business relevance, incremental fit, collinearity risk of a driver
    - Which drivers were evaluated but not selected for a division

    Do NOT use it for: selected driver lists with elasticities/ranges, forecasts,
    contribution percentages, impact MUSD, monthly data, or numeric structured lookups.
    """
    _emit_step("Unstructured KPI tool", "Reading KPI definition text.")
    content = KPI_DEFINITIONS_FALLBACK
    if UNSTRUCTURED_TEXT_PATH.exists():
        content = UNSTRUCTURED_TEXT_PATH.read_text(encoding="utf-8")

    llm = _build_llm()
    if llm is None:
        return _fallback_unstructured_answer(question, content)

    prompt = f"""
Answer the user's KPI-definition or concept question using only the KPI definition text.

Rules:
- Use the text as source of truth.
- Search exact KPI names, synonyms, definitions, and business meaning.
- If confidence is low, ask a short confirmation question.
- For driver evaluation or driver-selection feedback questions, use exact or very close driver-name matches from the evaluation section. Do not substitute related KPIs. For example, "China PPI" is not the same as "China IIP" or "Europe Producer Price Index", and "India GDP" is not the same as "US GDP" or "China GDP".
- If the user asks whether a driver was evaluated and that exact/very-close driver name is not present in the evaluation section for the requested division, return status "answer_not_found" and state that the driver was not found as an evaluated driver in the available knowledge.
- Do not answer selected drivers, elasticity, ranges, forecasts, contribution,
  monthly trends, or numeric lookup questions from this file.
- Return concise JSON:
{{
  "status": "success" | "answer_not_found" | "clarification_needed",
  "answer": "short answer"
}}

KPI definition text:
{content}
"""
    try:
        response = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=question)])
        return response.content
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)


def _normalise_driver_key(value: str) -> str:
    return " ".join(str(value or "").lower().replace("&", "and").replace("/", " ").split())


def _simulation_aliases(driver_names: List[str]) -> dict:
    aliases = {_normalise_driver_key(driver_name): driver_name for driver_name in driver_names}
    common_aliases = {
        "data center": "Data Center / Hyperscaler",
        "data centre": "Data Center / Hyperscaler",
        "data centres": "Data Center / Hyperscaler",
        "hyperscaler": "Data Center / Hyperscaler",
        "utility capex": "US Utility Capex",
        "us utility capex": "US Utility Capex",
        "utility spend": "US Utility Capex",
        "us utility spend": "US Utility Capex",
        "utility spending": "US Utility Capex",
        "us utility spending": "US Utility Capex",
        "power capex": "US Utility Capex",
        "grid capex": "US Utility Capex",
        "electrical equipment ppi": "Electrical Equipment PPI",
        "electrical ppi": "Electrical Equipment PPI",
        "equipment ppi": "Electrical Equipment PPI",
        "opex": "Operational Sales Expenses",
        "sales expenses": "Operational Sales Expenses",
        "operational sales": "Operational Sales Expenses",
        "iron and steel ppi": "Iron & Steel PPI",
        "iron steel ppi": "Iron & Steel PPI",
        "steel ppi": "Iron & Steel PPI",
        "us computer products": "US Computer Products",
        "computer products": "US Computer Products",
        "copper": "Copper Price",
        "copper price": "Copper Price",
        "china iip": "China IIP",
        "iip": "China IIP",
        "construction spending": "Construction Spending Index",
        "construction spending index": "Construction Spending Index",
        "europe ppi": "Europe Producer Price Index",
        "europe producer price index": "Europe Producer Price Index",
        "producer price index": "Europe Producer Price Index",
        "grid investment": "Grid Investment Index",
        "grid investment index": "Grid Investment Index",
        "renewable energy": "Renewable Energy Capacity Additions",
        "renewable energy capacity": "Renewable Energy Capacity Additions",
        "renewable energy capacity additions": "Renewable Energy Capacity Additions",
        "transformer raw material": "Transformer Raw Material Index",
        "transformer raw material index": "Transformer Raw Material Index",
        "industrial automation": "Industrial Automation Capex",
        "industrial automation capex": "Industrial Automation Capex",
    }
    for alias, canonical in common_aliases.items():
        if canonical in driver_names:
            aliases[_normalise_driver_key(alias)] = canonical
    return aliases


def _load_simulation_config(division: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        driver_rows = conn.execute(
            """
            SELECT
                selected_driver_name,
                current_growth_pct,
                elasticity
            FROM Driver_Contribution_KB
            WHERE division = ?
            ORDER BY contribution_pct DESC
            """,
            (division,),
        ).fetchall()
        forecast_row = conn.execute(
            """
            SELECT
                alpha_pct,
                base_point_forecast_pct
            FROM Forecast_KB
            WHERE division = ?
            LIMIT 1
            """,
            (division,),
        ).fetchone()

    return {
        "driver_values": {
            row["selected_driver_name"]: float(row["current_growth_pct"] or 0)
            for row in driver_rows
        },
        "driver_weights": {
            row["selected_driver_name"]: float(row["elasticity"] or 0)
            for row in driver_rows
        },
        "alpha_pct": float(forecast_row["alpha_pct"] or 0) if forecast_row else 0.0,
        "default_final_pct": float(forecast_row["base_point_forecast_pct"] or 0) if forecast_row else 0.0,
    }


def simulate_driver_growth(division: str, driver_values: Optional[dict] = None) -> dict:
    division = division.upper().strip()
    driver_values = driver_values or {}

    if division not in {"ELSP", "ELSB", "ELDS"}:
        return {"status": "error", "message": "Invalid division. Please use ELSP, ELSB, or ELDS."}

    config = _load_simulation_config(division)
    default_driver_values = config["driver_values"]
    driver_weights = config["driver_weights"]
    driver_aliases = _simulation_aliases(list(default_driver_values.keys()))

    if not default_driver_values:
        return {"status": "error", "message": f"No simulation drivers found for {division}."}

    if not driver_values:
        return {
            "status": "simulation_successful",
            "division": division,
            "final_growth_pct": round(config["default_final_pct"], 2),
            "message": "No driver values were changed, so the default simulation value was returned.",
            "driver_values_used": default_driver_values,
        }

    final_driver_values = default_driver_values.copy()
    changed_drivers = {}
    for input_driver_name, input_value in driver_values.items():
        driver_key = _normalise_driver_key(input_driver_name)
        if driver_key in driver_aliases:
            actual_driver_name = driver_aliases[driver_key]
        elif input_driver_name in final_driver_values:
            actual_driver_name = input_driver_name
        else:
            return {
                "status": "error",
                "message": f"Driver '{input_driver_name}' is not available for {division}.",
                "available_drivers": list(final_driver_values.keys()),
            }
        final_driver_values[actual_driver_name] = float(input_value)
        changed_drivers[actual_driver_name] = float(input_value)

    total_delta_impact = 0
    calculation_breakdown = []
    for driver_name, growth_value in final_driver_values.items():
        weight = driver_weights[driver_name]
        baseline_value = default_driver_values[driver_name]
        delta_value = growth_value - baseline_value
        impact = delta_value * weight
        total_delta_impact += impact
        calculation_breakdown.append(
            {
                "driver": driver_name,
                "baseline_growth_value_pct": baseline_value,
                "growth_value_pct": growth_value,
                "delta_growth_value_pct": round(delta_value, 4),
                "weight": weight,
                "incremental_impact_pct": round(impact, 4),
            }
        )

    final_growth_pct = config["default_final_pct"] + total_delta_impact
    return {
        "status": "simulation_successful",
        "division": division,
        "baseline_forecast_pct": round(config["default_final_pct"], 2),
        "changed_drivers": changed_drivers,
        "driver_values_used": final_driver_values,
        "calculation_breakdown": calculation_breakdown,
        "final_growth_pct": round(final_growth_pct, 2),
    }


@tool
def simulation_tool(division: str, driver_values: Optional[dict] = None) -> str:
    """Use for simulation, what-if analysis, or custom scenario planning.

    Call this when the user asks to simulate ELSP, ELSB, or ELDS growth by
    changing one or more selected driver growth values. Pass only changed
    drivers in driver_values; unchanged drivers keep their current database
    values. If the user says a driver goes down/up by X from current, pass the
    resulting absolute growth value after applying that change. Do not show
    alpha in the final answer; alpha is used internally.
    """
    _emit_step("Simulation tool", f"Running simulation for {division}.")
    result = simulate_driver_growth(division=division, driver_values=driver_values)
    return json.dumps(result, indent=2, default=str)


def _fallback_unstructured_answer(question: str, content: str) -> str:
    normalized_question = question.lower()
    blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
    scored = []
    for block in blocks:
        normalized_block = block.lower()
        score = sum(1 for term in normalized_question.split() if len(term) > 2 and term in normalized_block)
        if score:
            scored.append((score, block))
    if not scored:
        return json.dumps(
            {
                "status": "answer_not_found",
                "answer": "I could not confidently match that KPI in the definition file.",
            },
            indent=2,
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return json.dumps({"status": "success", "answer": scored[0][1][:1200]}, indent=2)


# --- Plotly plotting pipeline ---

_ABB_COLORS = ["#FF000F", "#19202C", "#2563EB", "#10B981", "#F59E0B", "#7C3AED"]


def _resolve_colors(spec_colors: Optional[List[str]], n: int) -> List[str]:
    if spec_colors and all(isinstance(c, str) and c.strip() for c in spec_colors):
        base = spec_colors
    else:
        base = _ABB_COLORS
    return [base[i % len(base)] for i in range(n)]


def _abb_layout(fig, title: str, x_label: str, y_label: str):
    DARK = "#111827"
    MID = "#1d2939"
    fig.update_layout(
        title=dict(
            text=f"<b>{title}</b>",
            font=dict(size=20, color=DARK, family="Arial"),
            x=0,
            xanchor="left",
        ),
        xaxis_title=x_label,
        yaxis_title=y_label,
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="Arial", size=14, color=MID),
        xaxis=dict(
            showgrid=False,
            linecolor="#D1D5DB",
            linewidth=1,
            tickfont=dict(size=13, color=MID, family="Arial"),
            title_font=dict(size=15, color=DARK, family="Arial"),
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="#F3F4F6",
            gridwidth=1,
            linecolor="#D1D5DB",
            tickfont=dict(size=13, color=MID, family="Arial"),
            title_font=dict(size=15, color=DARK, family="Arial"),
        ),
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#E5E7EB", borderwidth=1, font=dict(size=13, color=MID)),
        margin=dict(l=80, r=40, t=70, b=70),
        height=480,
    )


def _plot_line(df, x_col: str, y_cols: List[str], title: str, x_label: str, y_label: str, colors: Optional[List[str]] = None):
    import plotly.graph_objects as go
    resolved = _resolve_colors(colors, len(y_cols))
    fig = go.Figure()
    for i, col in enumerate(y_cols):
        fig.add_trace(go.Scatter(
            x=df[x_col], y=df[col],
            mode="lines+markers",
            name=col.replace("_", " ").title(),
            line=dict(color=resolved[i], width=2.5),
            marker=dict(size=5),
        ))
    _abb_layout(fig, title, x_label, y_label)
    return fig


def _plot_bar(df, x_col: str, y_cols: List[str], title: str, x_label: str, y_label: str, colors: Optional[List[str]] = None):
    import plotly.graph_objects as go
    resolved = _resolve_colors(colors, len(y_cols))
    fig = go.Figure()
    for i, col in enumerate(y_cols):
        vals = df[col]
        all_positive = vals.min() >= 0
        text_vals = [
            f"{v:,.1f}" if abs(v) < 1000 else f"{v:,.0f}"
            for v in vals
        ]
        fig.add_trace(go.Bar(
            x=df[x_col],
            y=vals,
            name=col.replace("_", " ").title(),
            marker=dict(
                color=resolved[i],
                opacity=0.9,
                line=dict(width=0),
            ),
            text=text_vals,
            textposition="outside" if all_positive else "auto",
            textfont=dict(size=12, color="#111827", family="Arial"),
            cliponaxis=False,
        ))
    max_val = df[y_cols].max().max()
    min_val = df[y_cols].min().min()
    y_top = max_val * 1.20 if max_val > 0 else max_val * 0.8
    y_bot = min_val * 1.15 if min_val < 0 else 0
    fig.update_layout(
        barmode="group",
        bargap=0.28,
        bargroupgap=0.06,
        yaxis=dict(range=[y_bot, y_top]),
    )
    _abb_layout(fig, title, x_label, y_label)
    return fig


def _plot_waterfall(df, x_col: str, value_col: str, title: str, x_label: str, y_label: str):
    import plotly.graph_objects as go
    n = len(df)
    if n < 2:
        return _plot_bar(df, x_col, [value_col], title, x_label, y_label)

    vals = df[value_col].tolist()
    labels = df[x_col].tolist()
    measure = ["relative"] * n
    text_vals = [f"+{v:,.1f}" if v >= 0 else f"{v:,.1f}" for v in vals]

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=measure,
        x=labels,
        y=vals,
        text=text_vals,
        textposition="outside",
        textfont=dict(size=12, color="#111827", family="Arial"),
        connector=dict(line=dict(color="#CBD5E1", width=1.5, dash="dot")),
        increasing=dict(marker=dict(color="#10B981", line=dict(width=0))),
        decreasing=dict(marker=dict(color="#EF4444", line=dict(width=0))),
        totals=dict(marker=dict(color="#1d2939", line=dict(width=0))),
        cliponaxis=False,
    ))
    _abb_layout(fig, title, x_label, y_label)
    return fig


def _plot_dual_axis_line(df, x_col: str, y_cols: List[str], title: str, x_label: str, y_label: str, y2_label: str = "", colors: Optional[List[str]] = None):
    import plotly.graph_objects as go
    resolved = _resolve_colors(colors, 2)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df[x_col], y=df[y_cols[0]],
        name=y_cols[0].replace("_", " ").title(),
        mode="lines+markers",
        line=dict(color=resolved[0], width=2.5),
        marker=dict(size=5),
        yaxis="y1",
    ))
    if len(y_cols) > 1:
        fig.add_trace(go.Scatter(
            x=df[x_col], y=df[y_cols[1]],
            name=y_cols[1].replace("_", " ").title(),
            mode="lines+markers",
            line=dict(color=resolved[1], width=2.5, dash="dot"),
            marker=dict(size=5),
            yaxis="y2",
        ))
    _abb_layout(fig, title, x_label, y_label)
    fig.update_layout(
        yaxis2=dict(
            title=dict(text=y2_label or (y_cols[1].replace("_", " ").title() if len(y_cols) > 1 else ""), font=dict(size=15, color=resolved[1], family="Arial")),
            overlaying="y",
            side="right",
            showgrid=False,
            tickfont=dict(size=13, color=resolved[1], family="Arial"),
        ),
        margin=dict(l=80, r=80, t=70, b=70),
    )
    return fig


def _plot_bar_horizontal(df, x_col: str, y_cols: List[str], title: str, x_label: str, y_label: str, colors: Optional[List[str]] = None):
    import plotly.graph_objects as go
    resolved = _resolve_colors(colors, 1)
    col = y_cols[0]
    df_sorted = df.sort_values(col).reset_index(drop=True)
    vals = df_sorted[col]
    text_vals = [f"{v:,.1f}" if abs(v) < 1000 else f"{v:,.0f}" for v in vals]
    fig = go.Figure(go.Bar(
        x=vals,
        y=df_sorted[x_col],
        orientation="h",
        marker=dict(color=resolved[0], opacity=0.9, line=dict(width=0)),
        text=text_vals,
        textposition="outside",
        textfont=dict(size=12, color="#111827", family="Arial"),
        cliponaxis=False,
    ))
    max_val = vals.max()
    fig.update_layout(
        xaxis=dict(range=[0, max_val * 1.22]),
        margin=dict(l=180, r=60, t=70, b=70),
    )
    _abb_layout(fig, title, y_label, x_label)
    fig.update_layout(xaxis_title=y_label, yaxis_title=x_label)
    return fig


def _plot_bar_colored(df, x_col: str, y_cols: List[str], title: str, x_label: str, y_label: str):
    import plotly.graph_objects as go
    col = y_cols[0]
    vals = df[col]
    colors = ["#10B981" if v >= 0 else "#EF4444" for v in vals]
    text_vals = [f"+{v:,.1f}" if v > 0 else f"{v:,.1f}" for v in vals]
    fig = go.Figure(go.Bar(
        x=df[x_col],
        y=vals,
        marker=dict(color=colors, opacity=0.9, line=dict(width=0)),
        text=text_vals,
        textposition="outside",
        textfont=dict(size=12, color="#111827", family="Arial"),
        cliponaxis=False,
    ))
    max_val = vals.max() if vals.max() > 0 else 0
    min_val = vals.min() if vals.min() < 0 else 0
    fig.update_layout(yaxis=dict(range=[min_val * 1.20, max_val * 1.20]))
    _abb_layout(fig, title, x_label, y_label)
    return fig


def _plot_bar_stacked(df, x_col: str, y_cols: List[str], title: str, x_label: str, y_label: str, colors: Optional[List[str]] = None):
    import plotly.graph_objects as go
    resolved = _resolve_colors(colors, len(y_cols))
    fig = go.Figure()
    for i, col in enumerate(y_cols):
        fig.add_trace(go.Bar(
            x=df[x_col],
            y=df[col],
            name=col.replace("_", " ").title(),
            marker=dict(color=resolved[i], opacity=0.9, line=dict(width=0)),
        ))
    fig.update_layout(barmode="stack", bargap=0.28)
    _abb_layout(fig, title, x_label, y_label)
    return fig


def _get_plot_spec(plot_request: str, df) -> PlotSpec:
    llm = _build_llm()
    if llm is None:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        return PlotSpec(
            chart_type="bar",
            x_col=df.columns[0],
            y_cols=numeric_cols[:1] if numeric_cols else [df.columns[-1]],
            title="Data Plot",
            x_label=df.columns[0],
            y_label="Value",
        )
    col_info = "\n".join(
        f"  - {col} ({df[col].dtype}): sample={df[col].iloc[0] if len(df) > 0 else 'N/A'}"
        for col in df.columns
    )
    prompt = f"""Choose the best Plotly chart type and column mappings.

Columns:
{col_info}

Sample (first 3 rows):
{df.head(3).to_string(index=False)}

User request: {plot_request}

Chart type rules — pick exactly one:
- line: one or more time-series on a single plot (same y-axis scale). Use when date/month column is present. y_cols can have multiple columns — each becomes a separate colored line.
- dual_axis_line: exactly two series with very different scales (e.g. orders in MUSD vs an index value). First series → left y-axis (y_label), second series → right y-axis (y2_label). y_cols must have exactly 2 columns.
- bar: grouped categorical comparison (multiple y_cols = grouped bars side by side). Use for rankings or comparing 2-3 divisions/scenarios.
- bar_horizontal: horizontal bar chart. Use when x-axis labels are long (driver names, division names) or when ranking many items. Single y_col only. Sorted ascending.
- bar_colored: vertical bar chart where positive values are green and negative values are red. Use for driver impacts, contributions, or any mix of positive/negative values.
- bar_stacked: stacked bar chart. Use when showing composition or parts of a whole across categories. Multiple y_cols stacked.
- waterfall: bridge/decomposition chart. Use for contribution breakdown (baseline → driver deltas → total). Needs >= 3 rows. Exactly one y_col.

Column mapping rules:
- x_col: category or time axis column name (must exist in columns above)
- y_cols: list of numeric column names to plot (must exist in columns above)
- For dual_axis_line: exactly 2 y_cols; set y2_label for the right axis label
- For waterfall / bar_horizontal / bar_colored: exactly 1 y_col
- title: descriptive business-friendly title
- x_label / y_label: clear axis labels

Colors rule:
- Leave `colors` as null UNLESS the user explicitly asks for specific colors (e.g. "use blue and green", "make it red"). When null, the system uses the default ABB color palette automatically.
- If the user does request colors, provide a list of valid CSS hex codes or color names matching the number of series."""
    structured_llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0,
    ).with_structured_output(PlotSpec)
    _emit_step("Plot spec", "LLM selecting chart type and axis columns.")
    return structured_llm.invoke(prompt)


def _run_plotly_pipeline(plot_request: str, csv_path: str) -> str:
    if not csv_path or not Path(csv_path).exists():
        return json.dumps({"status": "no_data_found", "message": "No structured data available. Run a data query first."})
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"Could not read data: {exc}"})
    if df.empty:
        return json.dumps({"status": "no_data_found", "message": "Data is empty, nothing to plot."})

    # Auto-pivot long format to wide when division column has multiple values
    # e.g. date|division|orders → date|ELSP|ELSB
    if "division" in df.columns and df["division"].nunique() > 1:
        date_cols = [c for c in df.columns if "date" in c.lower() or c.lower() in ("month", "year", "period")]
        value_cols = df.select_dtypes(include="number").columns.tolist()
        if date_cols and value_cols:
            pivot_col = date_cols[0]
            pivot_value = value_cols[0]
            try:
                df = df.pivot(index=pivot_col, columns="division", values=pivot_value).reset_index()
                df.columns.name = None
            except Exception:
                pass  # if pivot fails (e.g. duplicate dates), keep original

    try:
        spec = _get_plot_spec(plot_request, df)
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"Chart spec generation failed: {exc}"})
    # Validate columns exist
    if spec.x_col not in df.columns:
        spec.x_col = df.columns[0]
    spec.y_cols = [c for c in spec.y_cols if c in df.columns]
    if not spec.y_cols:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        spec.y_cols = [numeric_cols[0]] if numeric_cols else [df.columns[-1]]
    # Fallbacks
    if spec.chart_type == "waterfall" and len(df) < 3:
        spec.chart_type = "bar_colored"
    if spec.chart_type == "dual_axis_line" and len(spec.y_cols) < 2:
        spec.chart_type = "line"
    try:
        if spec.chart_type == "line":
            fig = _plot_line(df, spec.x_col, spec.y_cols, spec.title, spec.x_label, spec.y_label, spec.colors)
        elif spec.chart_type == "dual_axis_line":
            fig = _plot_dual_axis_line(df, spec.x_col, spec.y_cols, spec.title, spec.x_label, spec.y_label, spec.y2_label or "", spec.colors)
        elif spec.chart_type == "bar_horizontal":
            fig = _plot_bar_horizontal(df, spec.x_col, spec.y_cols, spec.title, spec.x_label, spec.y_label, spec.colors)
        elif spec.chart_type == "bar_colored":
            fig = _plot_bar_colored(df, spec.x_col, spec.y_cols, spec.title, spec.x_label, spec.y_label)
        elif spec.chart_type == "bar_stacked":
            fig = _plot_bar_stacked(df, spec.x_col, spec.y_cols, spec.title, spec.x_label, spec.y_label, spec.colors)
        elif spec.chart_type == "waterfall":
            fig = _plot_waterfall(df, spec.x_col, spec.y_cols[0], spec.title, spec.x_label, spec.y_label)
        else:
            fig = _plot_bar(df, spec.x_col, spec.y_cols, spec.title, spec.x_label, spec.y_label, spec.colors)
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"Plot rendering failed: {exc}"})
    PLOTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    plot_path = PLOTS_DIR / f"{ts}_plotly.png"
    latest_plot_path = PLOTS_DIR / "latest_plot.png"
    try:
        fig.write_image(str(plot_path), width=960, height=480, scale=3)
        shutil.copy(str(plot_path), str(latest_plot_path))
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"Plot save failed: {exc}"})
    _emit_step("Plot generated", f"Saved {plot_path.name} ({spec.chart_type}).")
    _REQUEST_PLOT["path"] = str(plot_path)
    _REQUEST_PLOT["latest"] = str(latest_plot_path)
    return (
        f"Plot generated at: {plot_path}\n"
        f"Latest plot copy: {latest_plot_path}\n"
        "Plot displayed successfully."
    )


def _is_followup_plot_request(plot_request: str) -> bool:
    normalized = plot_request.lower().strip()
    words = normalized.split()

    PLOT_TERMS = {"plot", "chart", "graph", "visual", "visualize", "visualise"}
    COLOR_TERMS = {"color", "colour", "blue", "red", "green", "yellow", "orange", "purple", "black", "grey", "gray", "pink", "teal", "cyan"}
    CHART_TYPES = {"line", "bar", "waterfall", "horizontal", "stacked", "dual"}
    CHANGE_VERBS = {"use", "change", "switch", "show", "make", "display", "convert", "try"}

    has_plot = any(t in normalized for t in PLOT_TERMS)
    has_color = any(t in normalized for t in COLOR_TERMS)
    has_type = any(t in normalized for t in CHART_TYPES)
    has_verb = any(t in normalized for t in CHANGE_VERBS)

    # Short messages with a plot term
    if len(words) <= 6 and has_plot:
        return True
    # Explicit plot + color request ("use blue and red to plot")
    if has_plot and has_color:
        return True
    # Color-change implies replot ("use blue and green", "change colors to red")
    if has_color and has_verb:
        return True
    # Chart type switch ("change to line chart", "make it a bar")
    if has_type and has_verb:
        return True

    return any(
        phrase in normalized
        for phrase in [
            "plot this", "plot it", "chart this", "chart it",
            "generate line plot", "line plot", "bar plot", "waterfall", "pie chart",
        ]
    )


@tool
def plot_tool(plot_request: str) -> str:
    """Use only when the user explicitly asks for a plot, chart, graph, or visualization.

    Generates a Plotly chart from the most recent structured data retrieved in this
    conversation. Do not call this after structured_data_tool — a plot is already
    automatically generated then. Only call this when the user explicitly requests
    a different or additional visualization.
    """
    csv_path = _LAST_CSV_PATH.get("path", "")
    _emit_step("Plot tool", "Generating Plotly chart from latest structured data.")
    return _run_plotly_pipeline(plot_request, csv_path)


# --- end Plotly pipeline ---

TOOLS = [
    structured_data_tool,
    unstructured_kpi_tool,
    plot_tool,
    simulation_tool,
]


def _conversation_context(history, user_message):
    recent_messages = history[-10:] if history else []
    lines = []
    for message in recent_messages:
        role = message.get("role", "unknown")
        content = message.get("content", "").replace("\n", " ").strip()
        if len(content) > 4000:
            content = f"{content[:4000]}..."
        lines.append(f"{role}: {content}")
    lines.append(f"user: {user_message}")
    return "\n".join(lines)


def _to_langchain_messages(history):
    messages = []
    for message in history:
        role = message.get("role")
        content = message.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def build_summarization_prompt() -> str:
    return """You are a business intelligence summarization assistant. Return two Markdown fields through the provided structured-output schema:
- summary_markdown: the concise executive answer shown by default beside a chart.
- details_markdown: the complete supporting analysis shown only when the user opens the Details tab.

MARKDOWN FORMATTING RULES — apply to BOTH fields:
1. Wrap ALL numbers, dollar amounts, percentages, and named metrics in **double asterisks** so they appear bold. Example: **$722.2M**, **+34%**, **ELSP**, **Q1 2024**.
2. Always prefix growth/positive values with `+` and declines with `-` and wrap in bold. Example: **+34%**, **-7.2%**.
3. Start every section with a **bold heading** followed by a colon. Example: **Key Findings:**
4. Use bullet points (`-`) for every data point or finding. Do not write long prose paragraphs.
5. End with a bold one-line summary. Example: **Overall, ELSP orders grew +34% from 2021 to 2025.**

SUMMARY_MARKDOWN RULES:
- Maximum 120 words.
- Use no more than 5 bullets.
- State the primary conclusion first.
- Include only the most decision-relevant drivers and quantified impacts.
- Do not include methodology, exhaustive rows, or repeated conclusions.
- The summary must fit comfortably beside a chart without scrolling.

DETAILS_MARKDOWN RULES:
- Give the complete supporting analysis requested by the user.
- Include driver-by-driver evidence, period comparisons, offsets, historical context, and caveats when supported by the source data.
- Do not merely repeat summary_markdown; expand it with supporting evidence.
- For time-series or row-level data requests, include the useful detailed values requested by the user.

EXAMPLE of correct Markdown within either field:
**Overview:**
- Start value: **$722.2M** in **Jan 2021**
- End value: **$964.7M** in **Feb 2025**
- Total growth: **+33.6%** over **4 years**

**Year-by-Year Highlights:**
- **2021:** Orders ranged from **$722.2M** to **$755.3M**
- **2022:** Orders rose to **$825.0M** — a **+9.2%** increase
- **2023:** Reached **$880.2M** by December
- **2024:** Stabilized between **$913.9M** and **$930.0M**
- **Early 2025:** Hit a new high of **$964.7M**

**ELSP orders delivered a consistent upward trend of +33.6% over four years with no significant reversals.**

Content rules:
- Answer ONLY from the data provided — no hallucination.
- Use plain business language — no SQL, tool names, or technical jargon.
- If the latest user message is only providing a data source after the assistant asked for a driver data source, do not repeat the prior driver evaluation or feedback details. Reply only with a brief thank-you and confirmation that the data source has been noted.
- For time-series: include start value, end value, total growth %, and year-by-year highlights.
- For simulation: state the result clearly with the computed number. Do not mention alpha.
- For KPI definitions: use bullets for each concept.
- For driver-selection feedback where the user expected one or more drivers to be included:
  - If the provided data says a named driver was not evaluated or not found in the evaluation knowledge, you MUST ask for the data source for that specific driver.
  - Also say: "Thank you for the feedback. I have noted this, and our team will look into it and get back to you."
  - For mixed feedback, briefly mention evaluated drivers separately from not-evaluated drivers, and ask for the data source only for the not-evaluated drivers.
  - Example: if **US GDP** was evaluated but **China PPI** was not, do not ask for **US GDP**'s data source. Ask only: "Could you also mention the data source for **China PPI** that you expected us to consider?"
- Do not add follow-up questions or closing remarks."""


def build_assistant_system_prompt() -> str:
    return f"""
You are a Decision Insights Copilot assistant for a demo knowledge base.

You help users with:
1. structured driver-analysis data
2. unstructured KPI definitions and concepts
3. plots using data already available in previous messages
4. simulations and what-if driver scenarios

You currently have access to these tools:

1. structured_data_tool
Use this for structured business-data questions such as:
- selected drivers
- driver elasticities
- recommended growth ranges
- current growth values
- baseline orders
- actual orders
- bear/base/bull forecasts
- forecast lower and upper bounds
- contribution values
- impact values
- waterfall/decomposition values
- monthly historical data
- comparing structured values across divisions, drivers, scenarios, or time periods

2. unstructured_kpi_tool
Use this for KPI definition and concept questions, AND for driver selection reasoning questions such as:
- KPI meaning
- KPI definition
- KPI business meaning
- KPI category
- synonyms or user terms
- misspelled KPI names
- indirectly described KPIs or concepts
- why a driver was selected for a division
- why a driver was not selected or was rejected for a division
- what outperformed a driver for a division
- which drivers were evaluated but not chosen for a division
- comparing two drivers for a given division
- signal strength or business relevance of a driver
- driver evaluation and selection reasoning

Examples (KPI definitions):
- What does Data Center mean?
- What is US Utility Capex?
- What does copper price indicate?
- What is China IIP?
- What does hyperscaler mean?

Examples (driver selection reasoning):
User: Why was US GDP not selected for ELSP?
Answer: Call unstructured_kpi_tool. The tool will return the reasoning: GDP was directionally relevant but too broad — Data Center / Hyperscaler outperformed it for ELSP because it is closer to the actual demand creation mechanism.

User: Why was Data Center chosen for ELSB?
Answer: Call unstructured_kpi_tool. The tool will return: Data Center / Hyperscaler was selected for ELSB because data-center buildout drives demand for low-voltage and electrification infrastructure, with High signal strength and High incremental fit.

User: What outperformed US GDP for ELDS?
Answer: Call unstructured_kpi_tool. The tool will return: Grid Investment Index and US Utility Capex outperformed US GDP for ELDS because they were more directly linked to distribution-system and grid demand.

User: Which drivers were evaluated but not selected for ELSP?
Answer: Call unstructured_kpi_tool. The tool will return the 9 rejected candidates for ELSP: US GDP, General Industrial Production, Manufacturing PMI, Consumer Confidence Index, Residential Housing Starts, Crude Oil Price, CPI All Items, Interest Rate, Exchange Rate Index.

User: Why was Construction Spending Index not selected for ELDS?
Answer: Call unstructured_kpi_tool. The tool will return: it had some relevance but was less direct than Grid Investment Index and US Utility Capex for ELDS.

User: Compare US GDP vs Data Center for ELSP.
Answer: Call unstructured_kpi_tool. The tool will return the evaluation entries for both, so you can compare their signal strength, business relevance, and selection outcome for ELSP.

3. plot_tool
Use this only when the user explicitly asks for:
- plot
- chart
- graph
- visual
- trend chart
- bar chart
- line chart
- waterfall chart
- pie chart

IMPORTANT: A Plotly chart is automatically generated every time structured_data_tool returns data.
Do NOT call plot_tool immediately after structured_data_tool — the auto-plot already handled it.
Only call plot_tool when the user explicitly requests a different or additional chart.

When calling plot_tool, pass only:
- plot_request: the current user plot request

4. simulation_tool
Use this when the user asks for:
- simulation
- what-if analysis
- custom scenario planning
- changing driver growth values
- calculating growth impact based on driver changes

When calling simulation_tool:
- Pass division as "ELSP", "ELSB", or "ELDS".
- Pass driver_values as a nested dictionary containing only changed driver values.
- If no driver values are changed, pass an empty dictionary.
- The simulation tool uses selected drivers from Driver_Contribution_KB. If a driver appears in the selected driver list, do not treat it as missing feedback.
- If the user says a driver goes down/up by X from current, first infer the current driver growth from prior retrieved data if available. Otherwise call structured_data_tool to retrieve current_growth_pct for that division and driver, then call simulation_tool with the resulting absolute value.
- Example: if current ELSP Data Center / Hyperscaler growth is 67 and the user says it reduces by 10, pass {{"Data Center / Hyperscaler": 57}}.
- Example: if current ELSP US Utility Capex growth is 20 and the user says utility capex goes down by 5, pass {{"US Utility Capex": 15}}.
- Do not mention alpha in the final answer.

Important routing rules:
- Highest priority: If the immediately previous assistant message asked "Could you also mention the data source..." or otherwise asked for a data source for driver-selection feedback, and the latest user message provides that data source, do NOT call any tool. Do NOT repeat the prior driver evaluation, missing-driver explanation, or feedback acknowledgement. Reply only: "Thank you. I have noted the data source, and our team will look into it and get back to you."
- If the user says they are unhappy with the selected drivers, disagrees with the driver set, expected different drivers, or wants the selected drivers reviewed, first check whether the user mentioned specific expected driver names.
- If specific expected driver names are mentioned in that feedback, call unstructured_kpi_tool once to check whether those drivers were evaluated for the relevant division.
- If unstructured_kpi_tool shows that all mentioned drivers were evaluated, do not ask for a data source. Acknowledge the concern and say: "I have noted your feedback and our team will look into it and get back to you." Briefly mention that the named drivers were already evaluated but not selected, using the tool result.
- If unstructured_kpi_tool shows that one or more mentioned drivers were not evaluated or not found in the evaluation knowledge, acknowledge the concern and say: "Thank you for the feedback. I have noted this, and our team will look into it and get back to you." Then ask only for the missing driver's data source: "Could you also mention the data source for <driver name> that you expected us to consider?"
- If the user gives driver-selection feedback but does not mention any specific expected driver names, do NOT call any tool. Acknowledge the concern and say: "I have noted your feedback and our team will look into it and get back to you." Then ask: "Could you also mention the drivers and data source you expected us to consider?"
- If the previous assistant message asked for the data source for driver-selection feedback and the user provides a data source, do NOT call any tool. Reply with a brief thank you and say the data source has been noted. Do not repeat the prior driver evaluation. Do not add the usual closing question in this specific case.
- Driver evaluation matching must be based on exact or very close driver names from the unstructured evaluation knowledge. Do not infer that a different related KPI was evaluated. For example, do not treat "China PPI" as evaluated just because "China IIP" or "Europe Producer Price Index" exists. Do not treat "India GDP" as evaluated just because "US GDP" or "China GDP" exists.
- Known evaluated driver names in the unstructured evaluation knowledge are: Data Center / Hyperscaler, US Utility Capex, Electrical Equipment PPI, Operational Sales Expenses, Iron & Steel PPI, US Computer Products, Copper Price, US GDP, General Industrial Production, Manufacturing PMI, Consumer Confidence Index, Residential Housing Starts, Crude Oil Price, CPI All Items, Interest Rate, Exchange Rate Index, Construction Spending Index, Europe Producer Price Index, China IIP, Retail Sales Index, Residential Building Permits, Grid Investment Index, Renewable Energy Capacity Additions, Transformer Raw Material Index, Industrial Automation Capex.
- For mixed feedback where some named drivers were evaluated and others were not, mention the evaluated ones briefly and ask for the data source only for the not-evaluated ones.
- Do not use structured_data_tool for KPI definitions, KPI business meaning, or driver selection reasoning questions.
- Do not use unstructured_kpi_tool for selected drivers, elasticities, ranges, forecasts, contributions, or monthly data.
- Use unstructured_kpi_tool for any question about why a driver was selected or rejected, what outperformed it, or how drivers were evaluated.
- Do not use plot_tool unless the user explicitly asks for a visual.
- Do not call plot_tool after structured_data_tool — a chart is auto-generated after every structured data result.
- Do not use structured_data_tool again for "plot this", "chart this", or "graph the above" if the needed data is already available in recent conversation.
- Do not invent values. If data is needed, call the correct tool.
- CRITICAL: When the user asks to compare BOTH divisions (ELSP and ELSB), call structured_data_tool ONLY ONCE with a question that includes both divisions. Do NOT call the tool separately for each division.

Time-series response rules:
- For monthly or time-series data requests, show the complete retrieved data rows in a compact Markdown table unless the user only asks for a summary or insight.
- Also, provide useful insights such as:
  - starting value and ending value
  - overall direction or trend
  - highest and lowest points if relevant
  - notable jumps, dips, or changes
  - comparison between two series if relevant
- If the user explicitly asks to show the full raw data, then show the full data and do not replace it with a summary.
- If the user asks to plot after a time-series result, call plot_tool using the previous messages.

Structured data response rules:
- For small structured results such as selected drivers, elasticities, ranges, forecasts, or contribution summary, show the key values clearly.
- For row-level data retrieval requests, show the complete returned rows in a compact Markdown table, then add a short insight summary.
- If the user asks "show all data", "give full data", or "show entire data", then provide the full available result.

Before handling a new request:
- If the request is for structured data, respond naturally as if helping the user get the data.
- If the request is for a plot, respond naturally as if helping the user plot it.
- If the request is for a KPI definition, respond naturally as if helping explain it.
- If the request is for simulation, respond naturally as if helping simulate the scenario.

Tool result handling:
- If a tool returns status "no_result", tell the user no result was found for the requested entity or filters.
- If a tool returns status "answer_not_found", tell the user the answer was not found in the knowledge source.
- If a tool returns status "clarification_needed", ask the clarification question from the tool.
- If a tool returns status "plot_displayed_successfully", tell the user the plot was generated successfully.
- If a tool returns status "simulation_successful", explain the final simulated growth and the driver values used. Do not mention alpha.
- If simulation_tool returns a missing-driver error, do not automatically treat it as driver-selection feedback. Tell the user the driver could not be matched for simulation and show the available simulation drivers.
- If a tool returns status "error", briefly tell the user an error occurred and show the error message.

Follow-up plotting behavior:
If the user says things like:
- "plot this"
- "chart this"
- "plot the above"
- "show this visually"
- "make a chart"
- "graph this"

then do not call structured_data_tool again if the required data is already available in the recent conversation.

Instead, call plot_tool directly and pass:
- plot_request: the current user plot request

The plot will be generated from the most recent structured data CSV. Only fetch data again if the required data is not available in the recent conversation.

Conversation closing:
- At the end of every completed answer, politely ask: "Can I help you with anything else?"
- If the user says something like "no", "nothing", "that's all", "done", "no thanks", or "stop", do not call any tool. Reply politely with: "Thank you. Have a great day."

Tone:
- Be concise, polite, and business-friendly.
- Provide insights, not just raw data dumps.
- Use "associated with", "contributed to", or "estimated impact".
- Avoid saying "caused" unless causality is explicitly supported.
"""


class BubuAgent:
    def __init__(self):
        self.llm = _build_llm()
        if self.llm is None:
            raise RuntimeError("OPENAI_API_KEY is required to run the agent.")
        self.llm_with_tools = self.llm.bind_tools(TOOLS)
        self.summarizer_llm = self.llm.with_structured_output(SummarizedResponse)
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(MessagesState)
        workflow.add_node("assistant", self._assistant_node)
        workflow.add_node("tools", ToolNode(TOOLS))
        workflow.add_node("tool_result_trace", self._tool_result_trace_node)
        workflow.add_node("auto_plot_check", self._auto_plot_check_node)
        workflow.add_node("summarization", self._summarization_node)
        workflow.add_edge(START, "assistant")
        workflow.add_conditional_edges(
            "assistant",
            self._assistant_router,
            {"tools": "tools", "summarization": "summarization"},
        )
        workflow.add_edge("tools", "tool_result_trace")
        workflow.add_edge("tool_result_trace", "auto_plot_check")
        workflow.add_edge("auto_plot_check", "assistant")
        workflow.add_edge("summarization", END)
        return workflow.compile()

    def _assistant_node(self, state: MessagesState):
        response = self.llm_with_tools.invoke(
            [
                SystemMessage(content=build_assistant_system_prompt()),
                *state["messages"],
            ]
        )
        if getattr(response, "tool_calls", None):
            pass
        else:
            pass
        return {"messages": [response]}

    def _assistant_router(self, state: MessagesState):
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"
        return "summarization"

    def _tool_result_trace_node(self, state: MessagesState):
        last_message = state["messages"][-1]
        tool_name = getattr(last_message, "name", "") or "tool"
        content = str(getattr(last_message, "content", "") or "")
        detail = f"Got result from {tool_name}."
        if tool_name == "structured_data_tool":
            try:
                parsed = json.loads(content)
                data = parsed.get("data", {})
                if isinstance(data, dict) and data:
                    row_count = sum(len(rows) for rows in data.values() if isinstance(rows, list))
                    tables = ", ".join(data.keys())
                    detail = f"Got {row_count} row(s) from {tables}."
            except json.JSONDecodeError:
                pass
        _emit_step("Tool result", detail)
        return {"messages": []}

    def _auto_plot_check_node(self, state: MessagesState) -> dict:
        if not _THIS_TURN_STRUCTURED.get("ran"):
            return {"messages": []}
        csv_path = _LAST_CSV_PATH.get("path", "")
        if not csv_path:
            return {"messages": []}
        user_q = LAST_STRUCTURED_RESULT.get("question", "generate a plot")
        _emit_step("Auto-plot", "Automatically generating Plotly chart after structured data retrieval.")
        plot_result = _run_plotly_pipeline(user_q, csv_path)
        return {"messages": [SystemMessage(content=f"[Auto-generated plot]\n{plot_result}")]}

    def _summarization_node(self, state: MessagesState):
        messages = state["messages"]

        # Find the current user question (last HumanMessage) and its index
        user_question = ""
        current_q_idx = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                user_question = str(messages[i].content)
                current_q_idx = i
                break

        # Build conversation history from messages before the current question
        # Only include clean Human/AI pairs — skip tool routing AIMessages (those have tool_calls)
        history_parts = []
        for msg in messages[:current_q_idx]:
            if isinstance(msg, HumanMessage):
                history_parts.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                content = str(msg.content).strip()
                if content:
                    history_parts.append(f"Assistant: {content}")

        # Tool results from the current turn only
        relevant_tools = ("structured_data_tool", "unstructured_kpi_tool", "simulation_tool")
        tool_parts = []
        for msg in messages[current_q_idx:]:
            name = getattr(msg, "name", None)
            if name in relevant_tools:
                tool_parts.append(f"[{name}]\n{msg.content}")

        tool_data_str = "\n\n".join(tool_parts) if tool_parts else "No tool data retrieved."

        # Keep last 10 history lines to avoid overwhelming the context
        history_str = "\n".join(history_parts[-10:]) if history_parts else ""

        summarization_input = ""
        if history_str:
            summarization_input += f"Conversation history:\n{history_str}\n\n"
        summarization_input += f"User question: {user_question}\n\nTool results:\n{tool_data_str}"

        _emit_step("Summarization", "Generating final summary.")
        response = self.summarizer_llm.invoke([
            SystemMessage(content=build_summarization_prompt()),
            HumanMessage(content=summarization_input),
        ])
        content = json.dumps(response.model_dump(), ensure_ascii=False)
        return {"messages": [AIMessage(content=content)]}

    def _emit_graph_update(self, update: dict):
        for node_name, node_update in update.items():
            if node_name == "assistant":
                messages = node_update.get("messages", []) if isinstance(node_update, dict) else []
                if not messages:
                    _emit_step("Graph", "Assistant node completed.")
                    continue
                last_message = messages[-1]
                if getattr(last_message, "tool_calls", None):
                    tool_names = ", ".join(call.get("name", "tool") for call in last_message.tool_calls)
                    _emit_step("Graph", f"Assistant node completed; selected {tool_names}.")
                else:
                    _emit_step("Graph", "Assistant node completed; final answer prepared.")
            elif node_name == "tools":
                messages = node_update.get("messages", []) if isinstance(node_update, dict) else []
                if messages:
                    tool_names = ", ".join(
                        getattr(message, "name", "") or "tool"
                        for message in messages
                    )
                    _emit_step("Graph", f"Tools node completed; received result from {tool_names}.")
                else:
                    _emit_step("Graph", "Tools node completed.")
            elif node_name == "tool_result_trace":
                _emit_step("Graph", "Tool result trace node completed.")
            elif node_name == "auto_plot_check":
                _emit_step("Graph", "Auto visual check completed.")
            elif node_name == "summarization":
                _emit_step("Graph", "Summarization node completed.")
            else:
                _emit_step("Graph", f"{node_name} node completed.")

    def _graph_update_events(self, update: dict) -> list[dict]:
        events = []
        for node_name, node_update in update.items():
            if node_name == "assistant":
                messages = node_update.get("messages", []) if isinstance(node_update, dict) else []
                if not messages:
                    events.append({"type": "node", "node": "assistant", "message": "Assistant node completed."})
                    continue

                last_message = messages[-1]
                if getattr(last_message, "tool_calls", None):
                    tool_names = ", ".join(call.get("name", "tool") for call in last_message.tool_calls)
                    events.append(
                        {
                            "type": "node",
                            "node": "assistant",
                            "message": f"Assistant selected tool(s): {tool_names}.",
                        }
                    )
                else:
                    events.append(
                        {
                            "type": "node",
                            "node": "assistant",
                            "message": "Assistant routing to summarization.",
                        }
                    )
            elif node_name == "tools":
                messages = node_update.get("messages", []) if isinstance(node_update, dict) else []
                if messages:
                    tool_names = ", ".join(getattr(message, "name", "") or "tool" for message in messages)
                    events.append(
                        {
                            "type": "node",
                            "node": "tools",
                            "message": f"Tools returned result from {tool_names}.",
                        }
                    )
                else:
                    events.append({"type": "node", "node": "tools", "message": "Tools node completed."})
            elif node_name == "tool_result_trace":
                events.append(
                    {
                        "type": "node",
                        "node": "tool_result_trace",
                        "message": "Tool result trace node completed.",
                    }
                )
            elif node_name == "auto_plot_check":
                events.append(
                    {
                        "type": "node",
                        "node": "auto_plot_check",
                        "message": "Auto visual check completed.",
                    }
                )
            elif node_name == "summarization":
                messages = node_update.get("messages", []) if isinstance(node_update, dict) else []
                if messages:
                    events.append(
                        {
                            "type": "node",
                            "node": "summarization",
                            "message": "Summarization completed.",
                            "content": messages[-1].content,
                        }
                    )
                else:
                    events.append({"type": "node", "node": "summarization", "message": "Summarization node completed."})
            else:
                events.append({"type": "node", "node": node_name, "message": f"{node_name} node completed."})
        return events

    def _stream_text_events(self, content: str):
        words = content.split(" ")
        for index, word in enumerate(words):
            suffix = "" if index == len(words) - 1 else " "
            yield {"type": "delta", "text": f"{word}{suffix}"}
            time.sleep(0.025)

    def respond_stream(self, user_message, history=None, conversation_id: str = ""):
        _tl.cid = conversation_id
        _CURRENT_CID["value"] = conversation_id or "unknown"
        _REQUEST_PLOT["path"] = ""
        _REQUEST_PLOT["latest"] = ""
        LAST_STRUCTURED_RESULT.clear()
        _LAST_CSV_PATH["path"] = ""
        _THIS_TURN_STRUCTURED["ran"] = False
        history = history or []
        normalized = user_message.strip().lower()
        print(f"[respond_stream] conversation_id={conversation_id} | history_msgs={len(history)}")

        yield {"type": "node", "node": "assistant", "message": "Assistant received user message."}

        if normalized in {"no", "nothing", "done", "no thanks", "stop", "that's all", "thats all"}:
            content = "Thank you. Have a great day."
            yield {"type": "node", "node": "assistant", "message": "Assistant prepared final answer.", "content": content}
            yield {"type": "final", "content": content}
            return

        structured_context = _structured_cache_context()
        # Restore CSV path from disk if cache was cleared (follow-up across turns)
        if not _LAST_CSV_PATH.get("path") and conversation_id:
            _LAST_CSV_PATH["path"] = _restore_csv_path(conversation_id)
        if (structured_context or _LAST_CSV_PATH.get("path")) and _is_followup_plot_request(user_message):
            yield {"type": "node", "node": "assistant", "message": "Assistant sent latest structured data to plotter."}
            tool_result = plot_tool.invoke({"plot_request": user_message})
            yield {"type": "node", "node": "tools", "message": "Tools returned result from plot_tool."}
            content = f"{tool_result}\n\nCan I help you with anything else?"
            yield {"type": "node", "node": "assistant", "message": "Assistant prepared final answer.", "content": content}
            yield {"type": "final", "content": content}
            return

        messages = _to_langchain_messages(history)
        if structured_context:
            messages.append(
                SystemMessage(
                    content=(
                        "Latest structured data is available for plotting. "
                        "If the user asks for a follow-up plot, call plot_tool with just plot_request.\n"
                        f"{structured_context}"
                    )
                )
            )
        messages.append(HumanMessage(content=user_message))

        yield {"type": "node", "node": "assistant", "message": "Assistant started processing request."}
        final_content = ""
        for update in self.graph.stream(
            {"messages": messages},
            {"recursion_limit": 12},
            stream_mode="updates",
        ):
            for event in self._graph_update_events(update):
                yield event
                if event.get("node") == "summarization" and event.get("content"):
                    final_content = event["content"]

        if not final_content:
            final_content = "I could not complete that request."

        if _REQUEST_PLOT.get("path"):
            final_content += (
                f"\nPlot generated at: {_REQUEST_PLOT['path']}\n"
                f"Latest plot copy: {_REQUEST_PLOT['latest']}\n"
                "Plot displayed successfully."
            )

        yield {"type": "node", "node": "assistant", "message": "Assistant completed request."}
        yield {"type": "final", "content": final_content}

    def respond(self, user_message, history=None, conversation_id: str = ""):
        _tl.cid = conversation_id
        _CURRENT_CID["value"] = conversation_id or "unknown"
        _REQUEST_PLOT["path"] = ""
        _REQUEST_PLOT["latest"] = ""
        LAST_STRUCTURED_RESULT.clear()
        _LAST_CSV_PATH["path"] = ""
        _THIS_TURN_STRUCTURED["ran"] = False
        history = history or []
        normalized = user_message.strip().lower()
        print(f"[respond] conversation_id={conversation_id} | history_msgs={len(history)}")
        _emit_step("Assistant", "Received user message.")

        if normalized in {"no", "nothing", "done", "no thanks", "stop", "that's all", "thats all"}:
            return "Thank you. Have a great day."

        structured_context = _structured_cache_context()
        # Restore CSV path from disk if cache was cleared (follow-up across turns)
        if not _LAST_CSV_PATH.get("path") and conversation_id:
            _LAST_CSV_PATH["path"] = _restore_csv_path(conversation_id)
        if (structured_context or _LAST_CSV_PATH.get("path")) and _is_followup_plot_request(user_message):
            _emit_step("Assistant", "Sending latest structured data to plotter.")
            tool_result = plot_tool.invoke({"plot_request": user_message})
            _emit_step("Assistant", "Completed request.")
            return f"{tool_result}\n\nCan I help you with anything else?"

        messages = _to_langchain_messages(history)
        if structured_context:
            messages.append(
                SystemMessage(
                    content=(
                        "Latest structured data is available for plotting. "
                        "If the user asks for a follow-up plot, call plot_tool with just plot_request.\n"
                        f"{structured_context}"
                    )
                )
            )
        messages.append(HumanMessage(content=user_message))
        _emit_step("Assistant", "Started processing request.")
        final_content = ""
        for update in self.graph.stream(
            {"messages": messages},
            {"recursion_limit": 12},
            stream_mode="updates",
        ):
            self._emit_graph_update(update)
            if "summarization" in update:
                node_update = update["summarization"]
                if isinstance(node_update, dict):
                    msgs = node_update.get("messages", [])
                    if msgs:
                        final_content = msgs[-1].content
        _emit_step("Assistant", "Completed request.")
        if not final_content:
            return "I could not complete that request."
        if _REQUEST_PLOT.get("path"):
            final_content += (
                f"\nPlot generated at: {_REQUEST_PLOT['path']}\n"
                f"Latest plot copy: {_REQUEST_PLOT['latest']}\n"
                "Plot displayed successfully."
            )
        return final_content


agent = BubuAgent()
