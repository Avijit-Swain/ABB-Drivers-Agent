import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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
UNSTRUCTURED_TEXT_PATH = DATA_DIR / "kpi_definitions_unstructured.txt"

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


PLOTTING_CODE_PROMPT = """
You are a Python plotting code generator for a Driver Analysis Chatbot.

Your job:
Generate executable Python code that plots data available in previous conversation messages.

Inputs:
1. Current user plot request
2. Previous 10 conversation messages

Important:
- The previous messages may contain structured JSON results from tools.
- The previous messages may also contain summarized assistant text with bullet lists.
- Extract the data from the previous messages and manually create a pandas DataFrame in the generated code.
- Do not assume external variables exist except pd and plt.
- Do not read files.
- Do not call APIs.
- Do not fetch new data.
- Do not invent values.

Chart selection rules:
- Use a line plot for chronological, monthly, quarterly, yearly, time-series, trend, or over-time data.
- Use a dual-axis line plot with ax1.twinx() when two time-series are plotted together and their values are on very different scales.
- Use a normal multi-line plot when two or more time-series have comparable scales.
- Use a bar plot for categorical comparisons, driver comparisons, scenario comparisons, or rankings.
- Use a horizontal bar plot when category labels are long.
- Use a waterfall-style bar chart for contribution, bridge, decomposition, or impact breakdown.
- Use a pie chart only for simple part-to-whole share questions with a small number of categories.
- If the user explicitly asks for a chart type, follow that chart type unless it is clearly unsuitable.

Follow-up plot requests:
- If the user says "plot this", "plot this please", "chart this", "graph this", or "plot the above", use the most recent structured data or assistant-provided data from previous messages.
- Do not ask the user to provide the data again if usable data exists in previous messages.

Required code rules:
- Return only JSON.
- The JSON must contain executable Python code as a string.
- The generated code must import pandas as pd and matplotlib.pyplot as plt.
- The generated code must create a DataFrame explicitly from extracted data.
- The generated code must create a figure using plt.figure(...) or plt.subplots(...).
- The generated code must set a clear title.
- The generated code must set clear axis labels.
- The generated code must call plt.tight_layout().
- The generated code must call plt.show() as the final plotting command.
- Do not save the figure in generated code.
- Do not use seaborn.
- Do not use plotly.
- Do not use markdown.
- Do not wrap code in triple backticks.

For dual-axis line plots:
- Use fig, ax1 = plt.subplots(figsize=(10, 5)).
- Use ax2 = ax1.twinx().
- Plot the first series on ax1 and the second series on ax2.
- Set both y-axis labels clearly.
- Combine legends from both axes.
- Call fig.autofmt_xdate(rotation=30).
- Call plt.tight_layout().
- Call plt.show().

For normal line plots:
- Convert date/month columns using pd.to_datetime when possible.
- Sort by the date/month column.
- Use markers for readability.
- Rotate x-axis labels if needed.
- Call plt.show().

For bar plots:
- Use plt.bar(...) or plt.barh(...).
- Rotate labels if needed.
- Call plt.show().

Output format if plot code can be generated:
{
  "status": "ready_to_plot",
  "code": "import pandas as pd\\nimport matplotlib.pyplot as plt\\n..."
}

Output format if no usable data exists:
{
  "status": "no_data_found",
  "message": "No usable data found in previous messages for plotting."
}

Quality check before returning:
- The code must be complete and runnable by exec().
- The code must create and display exactly one chart.
- The code must not reference undefined variables.
- The code must not contain markdown or explanations.
- The code must include plt.show().
"""


def _generate_plot_code(plot_request: str, previous_messages: str) -> dict:
    llm = _build_llm()
    if llm is None:
        return {
            "status": "no_data_found",
            "message": "Plot code generator LLM is unavailable.",
        }

    cache_context = _structured_cache_context()
    full_previous_messages = previous_messages
    if cache_context:
        full_previous_messages = f"{previous_messages}\n\n{cache_context}".strip()

    prompt = f"""
{PLOTTING_CODE_PROMPT}

Current user plot request:
{plot_request}

Previous 10 conversation messages:
{full_previous_messages}
"""
    _emit_step("Plot code generator", "Generating executable Matplotlib code.")
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


def _run_generated_plot_code(code: str) -> dict:
    MPL_CONFIG_DIR.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(MPL_CONFIG_DIR))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import shutil

    PLOTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    plot_path = PLOTS_DIR / f"{timestamp}_generated.png"
    latest_plot_path = PLOTS_DIR / "latest_plot.png"
    saved = {"done": False}

    original_show = plt.show

    def save_and_show(*args, **kwargs):
        figure = plt.gcf()
        figure.tight_layout()
        figure.savefig(plot_path, dpi=150, bbox_inches="tight")
        shutil.copyfile(plot_path, latest_plot_path)
        saved["done"] = True
        plt.close(figure)

    safe_globals = {
        "__builtins__": {
            "abs": abs,
            "all": all,
            "any": any,
            "dict": dict,
            "enumerate": enumerate,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "range": range,
            "round": round,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
            "__import__": __import__,
        },
        "pd": pd,
        "plt": plt,
    }

    try:
        plt.show = save_and_show
        exec(code, safe_globals, {})
        if not saved["done"]:
            save_and_show()
    finally:
        plt.show = original_show

    return {
        "status": "plot_displayed_successfully",
        "message": "Plot displayed successfully.",
        "plot_path": str(plot_path),
        "latest_plot_path": str(latest_plot_path),
    }


def _generate_code_from_fallback_plan(plot_plan: dict) -> str:
    """
    Deterministically creates matplotlib code from cached structured rows.
    This avoids relying on the LLM when the data is already available.
    """

    data = plot_plan.get("data", [])
    chart_type = plot_plan.get("chart_type", "bar")
    title = plot_plan.get("title", "Structured Data Plot")
    x_column = plot_plan.get("x_column")
    y_column = plot_plan.get("y_column")
    x_label = plot_plan.get("x_label", x_column or "")
    y_label = plot_plan.get("y_label", "Value")

    # y_column can be a list for multi-series plots
    if isinstance(y_column, list):
        y_columns = y_column
    else:
        y_columns = [y_column]

    return f"""
import pandas as pd
import matplotlib.pyplot as plt

data = {json.dumps(data, default=str)}
df = pd.DataFrame(data)

x_column = {repr(x_column)}
y_columns = {repr(y_columns)}
chart_type = {repr(chart_type)}

if x_column not in df.columns:
    raise ValueError(f"Missing x column: {{x_column}}")

for col in y_columns:
    if col not in df.columns:
        raise ValueError(f"Missing y column: {{col}}")
    df[col] = pd.to_numeric(df[col], errors="coerce")

if x_column == "date":
    df[x_column] = pd.to_datetime(df[x_column], errors="coerce")
    df = df.dropna(subset=[x_column]).sort_values(x_column)

plt.figure(figsize=(10, 5))

if chart_type == "line":
    for col in y_columns:
        plt.plot(df[x_column], df[col], marker="o", label=col.replace("_", " ").title())
    plt.legend(loc="best")
    plt.xticks(rotation=30, ha="right")

elif chart_type == "horizontal_bar":
    y_col = y_columns[0]
    df = df.sort_values(y_col)
    plt.barh(df[x_column].astype(str), df[y_col])

elif chart_type == "waterfall":
    y_col = y_columns[0]
    df["start"] = df[y_col].cumsum().shift(fill_value=0)
    plt.bar(df[x_column].astype(str), df[y_col], bottom=df["start"])
    plt.axhline(0, linewidth=0.8)
    plt.xticks(rotation=30, ha="right")

elif chart_type == "pie":
    y_col = y_columns[0]
    plt.pie(df[y_col], labels=df[x_column].astype(str), autopct="%1.1f%%", startangle=90)
    plt.axis("equal")

else:
    y_col = y_columns[0]
    plt.bar(df[x_column].astype(str), df[y_col])
    plt.xticks(rotation=30, ha="right")

if chart_type != "pie":
    plt.xlabel({repr(x_label)})
    plt.ylabel({repr(y_label)})

plt.title({repr(title)})
plt.tight_layout()
plt.show()
"""



@tool
def plot_tool(plot_request: str, previous_messages: str) -> str:
    """Use only when the user explicitly asks for a plot, chart, graph, visual, or trend chart.

    This plotter uses data already available in previous conversation messages.
    The assistant must pass the last 10 messages as readable text in
    previous_messages. Do not use this for normal structured lookup,
    definitions, simulations, or feedback capture.
    """
    try:
        # 1. Try LLM-generated plotting code first
        plot_plan = _generate_plot_code(plot_request, previous_messages)

        code = ""
        if plot_plan.get("status") == "ready_to_plot":
            code = plot_plan.get("code", "").strip()

        # 2. If LLM did not generate usable code, fallback to cached structured data
        if not code:
            _emit_step("Plot fallback", "LLM did not generate usable plot code. Falling back to cached structured data.")
            fallback_plan = _fallback_plot_plan_from_cache(plot_request)

            if fallback_plan.get("status") == "no_data_found":
                return json.dumps(fallback_plan, indent=2)

            code = _generate_code_from_fallback_plan(fallback_plan)

        # 3. Run code. If running LLM code fails, fallback once more.
        try:
            result = _run_generated_plot_code(code)
        except Exception:
            _emit_step("Plot fallback", "Generated plot code failed. Falling back to deterministic plot code.")
            fallback_plan = _fallback_plot_plan_from_cache(plot_request)

            if fallback_plan.get("status") == "no_data_found":
                return json.dumps(fallback_plan, indent=2)

            code = _generate_code_from_fallback_plan(fallback_plan)
            result = _run_generated_plot_code(code)

        if result.get("status") != "plot_displayed_successfully":
            return json.dumps(result, indent=2, default=str)

        return (
            f"Plot generated at: {result['plot_path']}\n"
            f"Latest plot copy: {result['latest_plot_path']}\n"
            "Plot displayed successfully."
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


def build_assistant_system_prompt() -> str:
    return f"""
You are a Driver Analysis Chatbot assistant for a demo knowledge base.

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
Use this for KPI definition and concept questions such as:
- KPI meaning
- KPI definition
- KPI business meaning
- KPI category
- synonyms or user terms
- misspelled KPI names
- indirectly described KPIs or concepts

Examples:
- What does Data Center mean?
- What is US Utility Capex?
- What does copper price indicate?
- What is China IIP?
- What does hyperscaler mean?

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

The plot_tool uses data from previous conversation messages.
When calling plot_tool, pass:
- plot_request: the current user plot request
- previous_messages: the last 10 conversation messages as readable text, including any data returned by previous tools.

4. simulation_tool
Use this when the user asks for:
- simulation
- what-if analysis
- custom scenario planning
- changing driver growth values
- calculating growth impact based on driver changes

When calling simulation_tool:
- Pass division as "ELSP" or "ELSB".
- Pass driver_values as a nested dictionary containing only changed driver values.
- If no driver values are changed, pass an empty dictionary.
- If the user gives five values in order, map them to the correct five drivers based on the simulation_tool description.
- Do not mention alpha in the final answer.

Important routing rules:
- Do not use structured_data_tool for KPI definitions or KPI business meaning.
- Do not use unstructured_kpi_tool for selected drivers, elasticities, ranges, forecasts, contributions, or monthly data.
- Do not use plot_tool unless the user explicitly asks for a visual.
- Do not use structured_data_tool again for "plot this", "chart this", or "graph the above" if the needed data is already available in recent conversation.
- Do not invent values. If data is needed, call the correct tool.

Time-series response rules:
- For monthly or time-series data, list few data points in the final answer if there are a lot of datapoints by default .
- Also, provide useful insights such as:
  - starting value and ending value
  - overall direction or trend
  - highest and lowest points if relevant
  - notable jumps, dips, or changes
  - comparison between two series if relevant
- If the user explicitly asks to show the full raw data, then show the full data.
- If the user asks to plot after a time-series result, call plot_tool using the previous messages.

Structured data response rules:
- For small structured results such as selected drivers, elasticities, ranges, forecasts, or contribution summary, show the key values clearly.
- For large row-level results, summarize the insight and avoid dumping all rows unless the user explicitly asks for full data.
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
- previous_messages: the last 10 conversation messages as readable text, including previous tool outputs or assistant-generated summaries/data

If the previous messages already contain the needed data, use that data for plotting.
Only fetch data again if the required data is not available in the recent conversation.

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
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(MessagesState)
        workflow.add_node("assistant", self._assistant_node)
        workflow.add_node("tools", ToolNode(TOOLS))
        workflow.add_node("tool_result_trace", self._tool_result_trace_node)
        workflow.add_edge(START, "assistant")
        workflow.add_conditional_edges(
            "assistant",
            self._assistant_router,
            {"tools": "tools", END: END},
        )
        workflow.add_edge("tools", "tool_result_trace")
        workflow.add_edge("tool_result_trace", "assistant")
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
        return END

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
            else:
                _emit_step("Graph", f"{node_name} node completed.")

    def respond(self, user_message, history=None):
        history = history or []
        normalized = user_message.strip().lower()
        conversation_context = _conversation_context(history, user_message)
        _emit_step("Assistant", "Received user message.")

        if normalized in {"no", "nothing", "done", "no thanks", "stop", "that's all", "thats all"}:
            return "Thank you. Have a great day."

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
        result = None
        for update in self.graph.stream(
            {"messages": messages},
            {"recursion_limit": 12},
            stream_mode="updates",
        ):
            self._emit_graph_update(update)
            for node_update in update.values():
                if isinstance(node_update, dict) and "messages" in node_update:
                    if result is None:
                        result = {"messages": []}
                    result["messages"].extend(node_update["messages"])
        _emit_step("Assistant", "Completed request.")
        if result is None or not result.get("messages"):
            return "I could not complete that request."
        return result["messages"][-1].content


agent = BubuAgent()
