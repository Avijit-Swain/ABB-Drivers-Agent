import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


load_dotenv()

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DB_PATH = APP_DIR / "new artifacts" / "driver_analysis_chatbot.db"
PLOTS_DIR = APP_DIR / "plots"
MPL_CONFIG_DIR = APP_DIR / ".matplotlib"
MEMORY_PATH = DATA_DIR / "long_term_memory.txt"
UNSTRUCTURED_TEXT_PATH = DATA_DIR / "kpi_definitions_unstructured.txt"

MEMORY_LOCK = threading.RLock()
MEMORY_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bubu-memory")
TRACE_CALLBACK = None
LAST_STRUCTURED_RESULT = {
    "question": "",
    "result": "",
}


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
- division: business division such as ELSP or ELSB
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
- What is the alpha value for ELSB?
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

Important columns:
- division: business division such as ELSP or ELSB
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

Example questions:
- What are the selected drivers for ELSP?
- Show elasticity of Data Center for ELSP.
- What is the recommended range for US Utility Capex?
- What are the current driver growth values for ELSP?
- Which driver contributed the most to ELSP orders?
- Show contribution impact in MUSD for ELSB.
- Which drivers are positive contributors and negative drags?
""",
    "Monthly_Data": """
Purpose:
Monthly_Data contains monthly historical values for orders and selected
external/internal drivers.

Use this table for:
- monthly orders
- historical trends
- movement over time
- monthly driver values
- comparing orders against a driver over time
- time-series data by division

Important columns:
- date: monthly date
- division: business division such as ELSP or ELSB
- orders_received_net_musd: monthly Orders Received Net in million USD
- data_center___hyperscaler: monthly Data Center / Hyperscaler value
- us_computer_products: monthly US Computer Products value
- operational_sales_expenses: monthly Operational Sales Expenses value
- iron_and_steel_ppi: monthly Iron & Steel PPI value
- china_mining_of_coal_and_lignite: monthly China Mining of Coal and Lignite value
- us_gdp: monthly US GDP value
- china_iip: monthly China IIP value
- us_utility_capex: monthly US Utility Capex value
- copper_price: monthly Copper Price value
- europe_producer_price_index: monthly Europe Producer Price Index value

Example questions:
- Show monthly orders for ELSP.
- How did Data Center move over time?
- Compare Data Center against ELSP orders.
- What was the monthly trend for ELSB orders?
- Show historical movement of US GDP and orders.
""",
}


KPI_DEFINITIONS_FALLBACK = """
KPI Definitions for Driver Analysis Chatbot

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


MEMORY_UPDATE_PROMPT = """
You are the long-term memory manager for a Driver Analysis Chatbot.

Update long-term memory using the old memory, the recent conversation context,
and the latest user message. Save only durable preferences, reusable
instructions, or stable facts that should affect future responses.

Do NOT save one-time topics, current questions, raw data returned from tools,
greetings, closing messages, secrets, or vague interests.

If the user gives feedback about a plot or response, infer the scope from the
recent context. For example, if the recent context is monthly trend data and the
user says "use line plot", save "User prefers line charts for monthly
time-series data", not "User prefers line plots".

Memory writing rules:
- Use concise bullets.
- Start each bullet with "- User ..."
- Keep each memory specific enough to be useful later.
- Rewrite or replace duplicates instead of adding another line.
- Remove old memories that are one-time question topics.

Return JSON only:
{
  "updated_memory": "final rewritten memory string",
  "memory_changed": true
}
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
You are selecting the correct SQL table(s) for a Driver Analysis Chatbot.

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


def _fallback_table(question: str) -> str:
    normalized = question.lower()
    if any(term in normalized for term in ["monthly", "trend", "over time", "time series", "against", "versus", " vs ", "historical"]):
        return "Monthly_Data"
    if any(term in normalized for term in ["driver", "elasticity", "range", "contribution", "impact", "waterfall", "bridge", "decomposition"]):
        return "Driver_Contribution_KB"
    return "Forecast_KB"


def _fallback_sql(question: str, table_name: str) -> str:
    normalized = question.lower()
    division_filter = ""
    if "elsp" in normalized:
        division_filter = " WHERE LOWER(division) = LOWER('ELSP')"
    elif "elsb" in normalized:
        division_filter = " WHERE LOWER(division) = LOWER('ELSB')"

    if table_name == "Monthly_Data":
        if "data center" in normalized or "hyperscaler" in normalized:
            return (
                "SELECT date, division, orders_received_net_musd, data_center___hyperscaler "
                f"FROM Monthly_Data{division_filter} ORDER BY date LIMIT 120"
            )
        return f"SELECT * FROM Monthly_Data{division_filter} ORDER BY date LIMIT 120"

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
        result = json.dumps({"status": "success", "sql": sql_used, "data": final_rows}, indent=2, default=str)
        _cache_structured_result(question, result)
        return result
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)


@tool
def unstructured_kpi_tool(question: str) -> str:
    """Use for KPI definition and concept questions.

    Use this for KPI meaning, KPI definition, business meaning, category,
    synonyms, misspelled KPI names, or indirectly described KPI concepts.
    Do not use it for selected drivers, elasticities, ranges, forecasts,
    contributions, monthly data, or numeric structured lookups.
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


def simulate_driver_growth(division: str, driver_values: Optional[dict] = None) -> dict:
    division = division.upper().strip()
    driver_values = driver_values or {}

    default_driver_values = {
        "ELSP": {
            "Data Center / Hyperscaler": 67.00,
            "US Computer Products": 0.20,
            "Operational Sales Expenses": 1.60,
            "Iron & Steel PPI": -0.48,
            "China Mining of Coal and Lignite": 0.18,
        },
        "ELSB": {
            "US GDP": 10.00,
            "China IIP": 12.00,
            "US Utility Capex": 24.00,
            "Copper Price": -2.00,
            "Europe Producer Price Index": 1.50,
        },
    }

    driver_weights = {
        "ELSP": {
            "Data Center / Hyperscaler": 0.13,
            "US Computer Products": 0.04,
            "Operational Sales Expenses": 0.27,
            "Iron & Steel PPI": 0.23,
            "China Mining of Coal and Lignite": 0.06,
        },
        "ELSB": {
            "US GDP": 0.18,
            "China IIP": 0.09,
            "US Utility Capex": 0.22,
            "Copper Price": -0.12,
            "Europe Producer Price Index": -0.08,
        },
    }

    alpha_values = {"ELSP": 2.00, "ELSB": 1.50}
    default_final_values = {"ELSP": 11.25, "ELSB": 9.78}
    driver_aliases = {
        "ELSP": {
            "data center": "Data Center / Hyperscaler",
            "hyperscaler": "Data Center / Hyperscaler",
            "data center / hyperscaler": "Data Center / Hyperscaler",
            "us computer products": "US Computer Products",
            "computer products": "US Computer Products",
            "operational sales expenses": "Operational Sales Expenses",
            "sales expenses": "Operational Sales Expenses",
            "iron & steel ppi": "Iron & Steel PPI",
            "iron and steel ppi": "Iron & Steel PPI",
            "steel ppi": "Iron & Steel PPI",
            "china mining of coal and lignite": "China Mining of Coal and Lignite",
            "coal and lignite": "China Mining of Coal and Lignite",
            "china coal mining": "China Mining of Coal and Lignite",
        },
        "ELSB": {
            "us gdp": "US GDP",
            "gdp": "US GDP",
            "china iip": "China IIP",
            "iip": "China IIP",
            "us utility capex": "US Utility Capex",
            "utility capex": "US Utility Capex",
            "copper price": "Copper Price",
            "copper": "Copper Price",
            "europe producer price index": "Europe Producer Price Index",
            "europe ppi": "Europe Producer Price Index",
            "producer price index": "Europe Producer Price Index",
        },
    }

    if division not in default_driver_values:
        return {"status": "error", "message": "Invalid division. Please use ELSP or ELSB."}

    if not driver_values:
        return {
            "status": "simulation_successful",
            "division": division,
            "final_growth_pct": default_final_values[division],
            "message": "No driver values were changed, so the default simulation value was returned.",
            "driver_values_used": default_driver_values[division],
        }

    final_driver_values = default_driver_values[division].copy()
    changed_drivers = {}
    for input_driver_name, input_value in driver_values.items():
        driver_key = str(input_driver_name).lower().strip()
        if driver_key in driver_aliases[division]:
            actual_driver_name = driver_aliases[division][driver_key]
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

    total_driver_impact = 0
    calculation_breakdown = []
    for driver_name, growth_value in final_driver_values.items():
        weight = driver_weights[division][driver_name]
        impact = growth_value * weight
        total_driver_impact += impact
        calculation_breakdown.append(
            {
                "driver": driver_name,
                "growth_value_pct": growth_value,
                "weight": weight,
                "impact_pct": round(impact, 4),
            }
        )

    final_growth_pct = alpha_values[division] + total_driver_impact
    return {
        "status": "simulation_successful",
        "division": division,
        "changed_drivers": changed_drivers,
        "driver_values_used": final_driver_values,
        "calculation_breakdown": calculation_breakdown,
        "final_growth_pct": round(final_growth_pct, 2),
    }


@tool
def simulation_tool(division: str, driver_values: Optional[dict] = None) -> str:
    """Use for simulation, what-if analysis, or custom scenario planning.

    Call this when the user asks to simulate ELSP or ELSB growth by changing one
    or more driver growth values. Pass only changed drivers in driver_values;
    unchanged drivers keep their default values. Do not show alpha in the final
    answer; alpha is used internally.
    """
    _emit_step("Simulation tool", f"Running simulation for {division}.")
    result = simulate_driver_growth(division=division, driver_values=driver_values)
    return json.dumps(result, indent=2, default=str)


def _load_memory_text() -> str:
    with MEMORY_LOCK:
        if not MEMORY_PATH.exists():
            return ""
        return MEMORY_PATH.read_text(encoding="utf-8").strip()


def get_long_term_memory() -> str:
    memory_text = _load_memory_text()
    return memory_text if memory_text else "No long-term memory saved yet."


def _save_memory_text(memory_text: str):
    DATA_DIR.mkdir(exist_ok=True)
    with MEMORY_LOCK:
        MEMORY_PATH.write_text(memory_text.strip() + ("\n" if memory_text.strip() else ""), encoding="utf-8")


def _update_long_term_memory(old_memory: str, user_message: str, conversation_context: str) -> dict:
    llm = _build_llm()
    if llm is None:
        return {"updated_memory": old_memory, "memory_changed": False}

    prompt = f"""
{MEMORY_UPDATE_PROMPT}

Old long-term memory:
{old_memory}

Recent conversation context:
{conversation_context}

Latest user message:
{user_message}
"""
    response = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=user_message)])
    return _parse_json_response(response.content)


def _should_update_memory(user_message: str) -> bool:
    normalized = user_message.lower().strip()
    if not normalized or normalized in {"hi", "hello", "thanks", "thank you", "no", "done", "stop"}:
        return False
    signals = [
        "i prefer",
        "always",
        "from now",
        "remember",
        "instead",
        "use ",
        "make it",
        "i like",
        "i don't like",
        "should be",
        "default",
    ]
    return any(signal in normalized for signal in signals)


def _schedule_memory_update(user_message: str, conversation_context: str):
    if not _should_update_memory(user_message):
        return False

    old_memory = _load_memory_text()

    def worker():
        try:
            result = _update_long_term_memory(old_memory, user_message, conversation_context)
            updated_memory = str(result.get("updated_memory", "")).strip()
            if updated_memory and updated_memory.lower() != "no long-term memory saved yet.":
                _save_memory_text(updated_memory)
        except Exception:
            pass

    MEMORY_EXECUTOR.submit(worker)
    return True


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


PLOTTING_PROMPT = """
You are a plotting planner for a driver-analysis chatbot.

Create a plotting plan using:
1. the current plot request
2. data available in previous conversation messages
3. long-term memory instructions

General chart selection rules:
- Use a line plot for chronological, monthly, quarterly, yearly, trend, or time-series data.
- Use a bar plot for categorical comparison, driver comparison, division comparison, scenario comparison, or ranking.
- Use a horizontal bar plot when category names are long or there are many categories.
- Use a waterfall plot for contribution, impact breakdown, bridge, decomposition, or positive/negative driver movement.
- Use a pie chart only for simple part-to-whole share questions with a small number of categories.
- Do not use pie charts for time-series data.
- If the user explicitly asks for a chart type, follow that chart type unless clearly unsuitable.

Data rules:
- Do not invent data.
- Use only data available in previous messages.
- If usable data is not available, return no_data_found.

Return JSON only:
{
  "status": "ready_to_plot",
  "chart_type": "line" | "bar" | "horizontal_bar" | "waterfall" | "pie",
  "title": "short chart title",
  "x_column": "column name",
  "y_column": "numeric column name",
  "data": [{"column1": "value", "column2": 123}],
  "x_label": "x-axis label",
  "y_label": "y-axis label",
  "reason": "brief reason"
}

or:
{
  "status": "no_data_found",
  "message": "No usable data found in previous messages for plotting."
}
"""


def _create_plot_plan(plot_request: str, previous_messages: str) -> dict:
    cached_plan = _fallback_plot_plan_from_cache(plot_request)
    if cached_plan.get("status") == "ready_to_plot" and _is_followup_plot_request(plot_request):
        _emit_step("Plot planner", "Using cached structured data for follow-up plot.")
        return cached_plan

    llm = _build_llm()
    if llm is None:
        return cached_plan

    cache_context = _structured_cache_context()
    full_previous_messages = previous_messages
    if cache_context:
        full_previous_messages = f"{previous_messages}\n\n{cache_context}".strip()

    prompt = f"""
Current plot request:
{plot_request}

Previous 10 messages:
{full_previous_messages}

Long-term memory:
{get_long_term_memory()}

GENERAL PLOTTING RULES:
{PLOTTING_PROMPT}
"""
    _emit_step("Plot planner", "Creating plot plan from previous messages and memory.")
    response = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=plot_request)])
    return _parse_json_response(response.content)


def _is_followup_plot_request(plot_request: str) -> bool:
    normalized = plot_request.lower().strip()
    if len(normalized.split()) <= 5 and any(term in normalized for term in ["plot", "chart", "graph", "visual"]):
        return True
    return any(
        phrase in normalized
        for phrase in [
            "plot this",
            "plot it",
            "chart this",
            "chart it",
            "generate line plot",
            "line plot",
            "bar plot",
            "waterfall",
            "pie chart",
        ]
    )


def _fallback_plot_plan_from_cache(plot_request: str) -> dict:
    cached_result = LAST_STRUCTURED_RESULT.get("result")
    if not cached_result:
        return {"status": "no_data_found", "message": "No cached structured data is available for plotting."}

    try:
        parsed = json.loads(cached_result)
    except json.JSONDecodeError:
        return {"status": "no_data_found", "message": "Cached structured data could not be parsed."}

    table_data = parsed.get("data", {})
    rows = []
    for table_rows in table_data.values():
        if isinstance(table_rows, list) and table_rows:
            rows = table_rows
            break

    if not rows:
        return {"status": "no_data_found", "message": "Cached structured data has no rows to plot."}

    first_row = rows[0]
    x_column = _infer_x_column(first_row)
    numeric_columns = _infer_numeric_columns(first_row, x_column)
    if not x_column or not numeric_columns:
        return {"status": "no_data_found", "message": "Cached data does not contain plottable columns."}

    normalized_request = plot_request.lower()
    chart_type = "bar"
    if any(term in normalized_request for term in ["line", "trend", "monthly", "over time", "time series"]):
        chart_type = "line"
    elif "waterfall" in normalized_request:
        chart_type = "waterfall"
    elif "pie" in normalized_request:
        chart_type = "pie"
    elif "horizontal" in normalized_request:
        chart_type = "horizontal_bar"

    if _looks_like_date_column(x_column, rows):
        chart_type = "line" if "bar" not in normalized_request else chart_type

    y_column = numeric_columns if chart_type in {"line", "bar"} and len(numeric_columns) > 1 else numeric_columns[0]
    return {
        "status": "ready_to_plot",
        "chart_type": chart_type,
        "title": "Structured Data Plot",
        "x_column": x_column,
        "y_column": y_column,
        "data": rows,
        "x_label": x_column.replace("_", " ").title(),
        "y_label": "Value",
        "reason": "Used the latest cached structured data returned by the structured tool.",
    }


def _infer_x_column(row: dict) -> str:
    for preferred in ["date", "month", "year", "division", "selected_driver_name"]:
        if preferred in row:
            return preferred
    for key, value in row.items():
        if not _is_number_like(value):
            return key
    return next(iter(row.keys()), "")


def _infer_numeric_columns(row: dict, x_column: str) -> list[str]:
    excluded = {x_column, "year", "baseline_year", "forecast_year"}
    return [
        key
        for key, value in row.items()
        if key not in excluded and _is_number_like(value)
    ]


def _is_number_like(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _looks_like_date_column(column: str, rows: list[dict]) -> bool:
    if column.lower() == "date":
        return True
    if not rows:
        return False
    sample = str(rows[0].get(column, ""))
    try:
        datetime.fromisoformat(sample)
        return True
    except ValueError:
        return False


def _render_plot(plot_plan: dict) -> dict:
    MPL_CONFIG_DIR.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(MPL_CONFIG_DIR))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import shutil

    chart_type = plot_plan["chart_type"]
    title = plot_plan.get("title", "Chart")
    x_column = plot_plan["x_column"]
    y_column = plot_plan["y_column"]
    x_label = plot_plan.get("x_label", x_column)
    y_label = plot_plan.get("y_label", y_column if isinstance(y_column, str) else "Value")
    df = pd.DataFrame(plot_plan["data"])

    y_columns = y_column if isinstance(y_column, list) else [y_column]
    y_columns = [column for column in y_columns if column in df.columns]

    if df.empty or x_column not in df.columns or not y_columns:
        return {"status": "no_data_found", "message": "Required plotting columns were not found."}

    for column in y_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=y_columns, how="all")
    if df.empty:
        return {"status": "no_data_found", "message": "No numeric values found for plotting."}

    PLOTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    plot_path = PLOTS_DIR / f"{timestamp}_{chart_type}.png"
    latest_plot_path = PLOTS_DIR / "latest_plot.png"

    plt.figure(figsize=(9, 5))
    if chart_type == "line":
        try:
            df[x_column] = pd.to_datetime(df[x_column])
            df = df.sort_values(x_column)
        except Exception:
            pass
        colors = ["#ff000f", "#101828", "#2171b5", "#667085", "#d7301f"]
        for index, column in enumerate(y_columns):
            plt.plot(
                df[x_column],
                df[column],
                marker="o",
                linewidth=2,
                color=colors[index % len(colors)],
                label=column.replace("_", " ").title(),
            )
        if len(y_columns) > 1:
            plt.legend(frameon=False)
        plt.xticks(rotation=30, ha="right")
    elif chart_type == "bar":
        if len(y_columns) > 1:
            x_positions = range(len(df))
            width = 0.8 / len(y_columns)
            colors = ["#ff000f", "#101828", "#2171b5", "#667085", "#d7301f"]
            for index, column in enumerate(y_columns):
                offset = (index - (len(y_columns) - 1) / 2) * width
                plt.bar(
                    [position + offset for position in x_positions],
                    df[column],
                    width=width,
                    color=colors[index % len(colors)],
                    label=column.replace("_", " ").title(),
                )
            plt.xticks(list(x_positions), df[x_column].astype(str), rotation=30, ha="right")
            plt.legend(frameon=False)
        else:
            plt.bar(df[x_column].astype(str), df[y_columns[0]], color="#ff000f")
            plt.xticks(rotation=30, ha="right")
    elif chart_type == "horizontal_bar":
        value_column = y_columns[0]
        df = df.sort_values(value_column)
        plt.barh(df[x_column].astype(str), df[value_column], color="#ff000f")
    elif chart_type == "waterfall":
        value_column = y_columns[0]
        df = df.copy()
        df["start"] = df[value_column].cumsum().shift(fill_value=0)
        plt.bar(df[x_column].astype(str), df[value_column], bottom=df["start"], color="#ff000f")
        plt.axhline(0, linewidth=0.8, color="#101828")
        plt.xticks(rotation=30, ha="right")
    elif chart_type == "pie":
        value_column = y_columns[0]
        plt.pie(df[value_column], labels=df[x_column].astype(str), autopct="%1.1f%%", startangle=90)
        plt.axis("equal")
    else:
        return {"status": "error", "message": f"Unsupported chart type: {chart_type}"}

    if chart_type != "pie":
        plt.xlabel(x_label)
        plt.ylabel(y_label)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    shutil.copyfile(plot_path, latest_plot_path)
    plt.close()

    return {
        "status": "plot_displayed_successfully",
        "message": "Plot displayed successfully.",
        "plot_path": str(plot_path),
        "latest_plot_path": str(latest_plot_path),
        "chart_type": chart_type,
        "title": title,
        "reason": plot_plan.get("reason", ""),
    }


@tool
def plot_tool(plot_request: str, previous_messages: str) -> str:
    """Use only when the user explicitly asks for a plot, chart, graph, visual, or trend chart.

    This plotter uses data already available in previous conversation messages.
    The assistant must pass the last 10 messages as readable text in
    previous_messages. Do not use this for normal structured lookup,
    definitions, simulations, or feedback capture.
    """
    try:
        plot_plan = _create_plot_plan(plot_request, previous_messages)
        if plot_plan.get("status") == "no_data_found":
            _emit_step("Plot planner", "Planner did not find data; using cached structured result.")
            plot_plan = _fallback_plot_plan_from_cache(plot_request)
            if plot_plan.get("status") == "no_data_found":
                return json.dumps(plot_plan, indent=2)
        result = _render_plot(plot_plan)
        if result.get("status") != "plot_displayed_successfully":
            return json.dumps(result, indent=2, default=str)
        return (
            f"Plot generated at: {result['plot_path']}\n"
            f"Latest plot copy: {result['latest_plot_path']}\n"
            f"Chart type: {result['chart_type']}\n"
            f"Title: {result['title']}\n"
            f"Reason: {result.get('reason', '')}"
        )
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)


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
        if len(content) > 1200:
            content = f"{content[:1200]}..."
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


def build_assistant_system_prompt() -> str:
    return f"""
You are a Driver Analysis Chatbot assistant for an ABB demo knowledge base.

- Do no summarize the data points. Show the entire data please from previous tool call result.

You help users with:
1. structured driver-analysis data
2. unstructured KPI definitions and concepts
3. plots using data already available in previous messages
4. simulations / what-if analysis

Long-term memory:
{get_long_term_memory()}

How to use long-term memory:
- Use long-term memory only when relevant to the current request.
- If memory contains chart preferences, apply them when plotting.
- If memory conflicts with the current request, follow the current request.
- Do not treat one-off past chart types as permanent preferences unless they
  are saved in long-term memory.

Tools:

1. structured_data_tool
Use this for structured business-data questions such as:
- selected drivers
- driver elasticities
- recommended growth ranges
- current growth values
- baseline orders
- actual orders
- bear/base/bull forecasts
- contribution values
- impact values
- waterfall/decomposition values
- monthly historical data
- comparing structured values across divisions, drivers, scenarios, or time periods

2. unstructured_kpi_tool
Use this for KPI definition and concept questions such as:
- KPI meaning
- KPI definition
- KPI business meaning
- KPI category
- synonyms or user terms
- misspelled KPI names
- indirectly described KPIs or concepts

3. plot_tool
Use only when the user explicitly asks for a plot/chart/graph/visual. It uses
data from previous conversation messages, so when calling it pass:
- plot_request: current user plot request
- previous_messages: last 10 conversation messages as readable text, including
  data returned by previous tools
If the system message contains "Latest raw structured result for plotting",
copy that raw JSON into previous_messages when calling plot_tool. Do not pass
only the assistant's human-readable summary.
If the user asks to plot something but no data is present in previous messages,
first call structured_data_tool to retrieve the data, then call plot_tool.
When a plot is generated, include the exact "Plot generated at:" line in your
final answer so the UI can render it.

4. simulation_tool
Use for simulation, what-if analysis, custom scenario planning, or changing one
or more driver growth values for ELSP or ELSB. Do not show alpha in the final
answer; the simulator uses it internally.

Important routing rules:
- Do not use structured_data_tool for KPI definitions or business meaning.
- Do not use unstructured_kpi_tool for selected drivers, elasticities, ranges,
  forecasts, contributions, or monthly numeric data.
- Do not use plot_tool unless the user explicitly asks for a visual.
- Do not invent values. If data is needed, call the correct tool.
- If a request is ambiguous, ask a concise clarification question directly.

Tool result handling:
- If a tool returns status "no_result", tell the user no result was found.
- If a tool returns status "answer_not_found", say it was not found in the knowledge source.
- If a tool returns status "clarification_needed", ask the clarification question.
- If a tool returns status "error", briefly show the error message.

Conversation closing:
- At the end of every completed answer, ask: "Can I help you with anything else?"
- If the user says no, nothing, done, no thanks, or stop, reply: "Thank you. Have a great day."

Tone:
- Be concise, polite, and business-friendly.
- Use "associated with", "contributed to", or "estimated impact".
- Avoid saying "caused" unless causality is explicitly supported.
- Do no summarize the data points. Show the entire data please.
"""


class BubuAgent:
    def __init__(self):
        self.llm = _build_llm()
        self.llm_with_tools = self.llm.bind_tools(TOOLS) if self.llm else None
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(MessagesState)
        workflow.add_node("assistant", self._assistant_node)
        workflow.add_node("tools", ToolNode(TOOLS))
        workflow.add_edge(START, "assistant")
        workflow.add_conditional_edges(
            "assistant",
            self._assistant_router,
            {"tools": "tools", END: END},
        )
        workflow.add_edge("tools", "assistant")
        return workflow.compile()

    def _assistant_node(self, state: MessagesState):
        if self.llm_with_tools is None:
            last_user_message = self._latest_user_message(state["messages"])
            return {"messages": [AIMessage(content=self._fallback_answer(last_user_message))]}

        response = self.llm_with_tools.invoke(
            [
                SystemMessage(content=build_assistant_system_prompt()),
                *state["messages"],
            ]
        )
        if getattr(response, "tool_calls", None):
            tool_names = ", ".join(call.get("name", "tool") for call in response.tool_calls)
            _emit_step("Assistant", f"Selected {tool_names}.")
        else:
            _emit_step("Assistant", "Prepared final answer.")
        return {"messages": [response]}

    def _assistant_router(self, state: MessagesState):
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"
        return END

    def respond(self, user_message, history=None):
        history = history or []
        normalized = user_message.strip().lower()
        conversation_context = _conversation_context(history, user_message)
        _emit_step("Assistant", "Received user message.")

        if normalized in {"no", "nothing", "done", "no thanks", "stop", "that's all", "thats all"}:
            return "Thank you. Have a great day."

        if _schedule_memory_update(user_message, conversation_context):
            _emit_step("Memory", "Queued long-term memory update in background.")

        structured_context = _structured_cache_context()
        if structured_context and _is_followup_plot_request(user_message):
            _emit_step("Assistant", "Sending latest structured data to plotter.")
            tool_result = plot_tool.invoke(
                {
                    "plot_request": user_message,
                    "previous_messages": f"{conversation_context}\n\n{structured_context}",
                }
            )
            _emit_step("Assistant", "Completed request.")
            return f"{tool_result}\n\nCan I help you with anything else?"

        messages = _to_langchain_messages(history)
        if structured_context:
            messages.append(
                SystemMessage(
                    content=(
                        "Latest raw structured result for plotting. Use this "
                        "as previous_messages for plot_tool when the user asks "
                        f"for a follow-up plot.\n{structured_context}"
                    )
                )
            )
        messages.append(HumanMessage(content=user_message))
        _emit_step("Assistant", "Started processing request.")
        result = self.graph.invoke({"messages": messages}, {"recursion_limit": 12})
        _emit_step("Assistant", "Completed request.")
        return result["messages"][-1].content

    def load_memories(self):
        memory_text = _load_memory_text()
        if not memory_text:
            return []
        return [
            line.strip().lstrip("- ").strip()
            for line in memory_text.splitlines()
            if line.strip() and line.strip().lower() != "no long-term memory saved yet."
        ]

    def clear_memories(self):
        DATA_DIR.mkdir(exist_ok=True)
        with MEMORY_LOCK:
            MEMORY_PATH.write_text("", encoding="utf-8")

    def _latest_user_message(self, messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return message.content
        return ""

    def _fallback_answer(self, user_message: str) -> str:
        return (
            "The LLM is not configured, so I cannot run the agent right now. "
            f"You said: {user_message}"
        )


agent = BubuAgent()
