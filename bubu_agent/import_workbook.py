import json
import re
import shutil
import sqlite3
from pathlib import Path

import pandas as pd


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
SOURCE_WORKBOOK = Path("/Users/avijit_swain/Downloads/driver_analysis_chatbot_kb_static_v2.xlsx")
LOCAL_WORKBOOK = DATA_DIR / "driver_analysis_chatbot_kb_static_v2.xlsx"
DB_PATH = DATA_DIR / "driver_analysis_workbook.db"
SCHEMA_PATH = DATA_DIR / "workbook_schema.json"


AGENT_WORKBOOK_GUIDANCE = {
    "purpose": (
        "Static knowledge base for the Driver Analysis chatbot. It stores "
        "division-level order data, selected drivers, elasticities, scenario "
        "ranges, KPI definitions, contribution values, and reference forecasts."
    ),
    "important_principle": (
        "The workbook stores facts and reference values. It is not a calculation "
        "engine. Custom scenario planning must be calculated in Python using "
        "alpha + SUM(elasticity * driver_growth_pct)."
    ),
    "supported_divisions": ["ELSP", "ELSB"],
    "primary_target_kpi": "orders_received_net / Orders Received Net",
        "routing_principles": [
            "Use Division_Baseline for division-level baseline, actual, growth, and alpha questions.",
            "Use Monthly_Data for historical/monthly trends, order vs driver trends, metric-against-orders plots, and line charts over time.",
        "Use KPI_Dictionary for KPI definitions, synonyms, misspellings, and KPI matching before querying other sheets.",
        "Use Driver_Scenario_Input for selected drivers, elasticities, current/pessimistic/optimistic growth, and recommended ranges.",
        "Use Contribution_Static for precomputed driver impact, MUSD contribution, contribution percentage, ranking, and waterfall-style explanation.",
        "Use Point_Forecast_Static for precomputed dummy bear/base/bull forecast values.",
        "Use Custom_Simulation_Template only as a structure/reference for custom scenario inputs; calculate custom results in Python.",
    ],
        "chart_rules": [
            "Use line charts for order trends, KPI trends, and order-vs-driver comparisons over time from Monthly_Data.",
            "Treat phrases like 'Data Center against my ELSP orders' as a Monthly_Data multi-series time-series plot.",
            "Use date on the x-axis for time-series charts.",
        "If comparing order and driver metrics with very different scales, normalize/index both series to 100 or explain the scale limitation.",
        "Use waterfall or sorted bar charts for driver contribution breakdowns from Contribution_Static.",
        "Use scenario comparison charts for bear/base/bull or custom scenario comparison.",
        "Every chart response should include one to three concise business interpretation lines.",
    ],
    "scenario_rules": [
        "Formula: order_growth_pct = alpha + SUM(elasticity * driver_growth_pct).",
        "Read division alpha from Division_Baseline.",
        "Use pessimistic/current/optimistic growth from Driver_Scenario_Input for standard scenarios.",
        "Use user-provided growth for custom scenarios and compare it with recommended_min_pct and recommended_max_pct.",
        "Warn when user-provided growth is outside the recommended range; do not silently reject unless business rules say so.",
        "If a KPI is not selected for the requested division, explain that selected-driver simulation is not available in v1.",
    ],
    "wording_rules": [
        "Avoid saying a driver caused movement unless causal assumptions are explicitly supported.",
        "Prefer 'associated with', 'contributed to explained movement', or 'estimated impact'.",
        "Do not rerun feature selection, retrain models, change elasticities, or create new drivers in v1.",
        "If the user disagrees with a driver, range, or result, capture feedback for future modelling review.",
    ],
}


TABLE_METADATA = {
    "readme": {
        "description": (
            "Workbook guide and sheet documentation. Use this only when the user asks "
            "what the workbook contains, what a sheet is for, or whether formulas are used."
        ),
        "routing_keywords": [
            "workbook overview",
            "readme",
            "sheet description",
            "what data is available",
            "formulas",
        ],
    },
    "division_baseline": {
        "description": (
            "Division-level baseline and actual orders. This is the source of truth for "
            "questions comparing 2025 actual orders with 2024 baseline orders by division, "
            "including growth amount, growth percentage, baseline year, forecast year, and "
            "alpha/intercept values used by the simulator."
        ),
        "routing_keywords": [
            "division growth",
            "growth of ELSP",
            "growth of ELSB",
            "2025 compared to 2024",
            "baseline vs actual",
            "actual orders",
            "baseline orders",
            "actual_growth_pct",
            "actual_growth_musd",
            "alpha",
        ],
    },
    "driver_scenario_input": {
        "description": (
            "Selected driver inputs by division. Contains driver names, KPI codes, selected_flag, "
            "elasticity, pessimistic/current/optimistic growth percentages, recommended min/max "
            "growth ranges, units, frequency, country/region, source type, and rationale. Use for "
            "questions about selected drivers, scenario input assumptions, elasticities, growth "
            "ranges, driver metadata, or which KPIs are used for a division."
        ),
        "routing_keywords": [
            "selected drivers",
            "driver scenario",
            "elasticity",
            "pessimistic growth",
            "current growth",
            "optimistic growth",
            "recommended range",
            "selected_flag",
            "driver rationale",
        ],
    },
    "point_forecast_static": {
        "description": (
            "Static point forecast outputs by division. Contains alpha plus bear/base/bull order "
            "growth percentages and bear/base/bull forecast order values in MUSD. Use when the "
            "user asks for precomputed forecast scenarios or bear/base/bull values."
        ),
        "routing_keywords": [
            "point forecast",
            "bear forecast",
            "base forecast",
            "bull forecast",
            "forecast orders",
            "forecast growth",
            "scenario forecast",
        ],
    },
    "contribution_static": {
        "description": (
            "Static driver contribution results by division and driver. Contains elasticity, "
            "current growth, driver impact percentage, baseline orders, driver impact in MUSD, "
            "signed contribution percentage, absolute contribution percentage, and notes. Use "
            "for contribution, impact, waterfall, bridge, ranking, or decomposition questions."
        ),
        "routing_keywords": [
            "contribution",
            "driver impact",
            "impact MUSD",
            "signed contribution",
            "absolute contribution",
            "waterfall",
            "bridge",
            "decomposition",
        ],
    },
    "custom_simulation_template": {
        "description": (
            "Template for user-provided custom scenario simulations. Contains division, driver, "
            "elasticity, blank custom_growth_pct placeholder, recommended min/max ranges, whether "
            "the range check is expected, and agent notes. Use for custom scenario setup, required "
            "inputs, validation ranges, or asking the user to provide custom growth assumptions."
        ),
        "routing_keywords": [
            "custom simulation",
            "custom scenario",
            "custom growth",
            "simulation template",
            "within range",
            "recommended min",
            "recommended max",
        ],
    },
    "kpi_dictionary": {
        "description": (
            "KPI reference dictionary. Contains KPI name, KPI code, definition, business meaning, "
            "synonyms, category, country/region, frequency, and source type. Use for questions "
            "asking what a KPI means, KPI code definitions, synonyms, categories, or business meaning."
        ),
        "routing_keywords": [
            "KPI definition",
            "KPI code",
            "business meaning",
            "synonyms",
            "metric definition",
            "what does KPI mean",
        ],
    },
    "monthly_data": {
        "description": (
            "Monthly time-series data for 2021-2025 across ELSP and ELSB. Contains monthly orders "
            "received net in MUSD and monthly/quarterly macro or driver KPI values such as US GDP, "
            "US Data Center, China IIP, US Computer Products, US Capacity, US Utility Capex, China "
            "GDP, Europe Producer Price Index, Copper Price, and Iron Price. Use for trends over "
            "time, monthly/yearly comparisons, time-series plots, correlations, and historical KPI "
            "or order patterns. Do not use this for the simple 2025 versus 2024 baseline growth "
            "question; use division_baseline for that."
        ),
        "routing_keywords": [
            "monthly trend",
            "over time",
            "time series",
            "2021",
            "2022",
            "2023",
            "2024",
            "2025 monthly",
            "orders trend",
            "historical KPI",
            "correlation",
        ],
    },
}


COLUMN_DESCRIPTIONS = {
    "division_baseline": {
        "division": "Division code. Known values include ELSP and ELSB.",
        "baseline_year": "Baseline comparison year, currently 2024.",
        "forecast_year": "Actual/forecast comparison year, currently 2025.",
        "baseline_orders_musd": "Orders in the baseline year in million USD.",
        "actual_orders_musd": "Actual orders in the forecast year in million USD.",
        "actual_growth_musd": "Increase from baseline_orders_musd to actual_orders_musd in million USD.",
        "actual_growth_pct": "Percentage growth from baseline year to forecast year.",
        "alpha_pct": "Dummy alpha/intercept percentage used by the simulator.",
        "notes": "Notes about the baseline row.",
    },
    "driver_scenario_input": {
        "division": "Division code for the driver input.",
        "driver": "Driver or external KPI name used in the scenario.",
        "kpi_code": "Short KPI code that maps to kpi_dictionary.",
        "selected_flag": "Whether the driver is selected. Values use Yes.",
        "elasticity": "Elasticity linking driver growth to order impact.",
        "pessimistic_growth_pct": "Pessimistic scenario driver growth percentage.",
        "current_growth_pct": "Current/base scenario driver growth percentage.",
        "optimistic_growth_pct": "Optimistic scenario driver growth percentage.",
        "recommended_min_pct": "Recommended lower bound for custom growth input.",
        "recommended_max_pct": "Recommended upper bound for custom growth input.",
        "unit": "Measurement unit, typically growth percentage.",
        "frequency": "Data refresh or measurement frequency.",
        "country_or_region": "Relevant geography for the KPI or driver.",
        "source_type": "External, commodity, or other source classification.",
        "rationale_short": "Short explanation of why the driver matters.",
    },
    "point_forecast_static": {
        "division": "Division code for static forecast outputs.",
        "alpha_pct": "Alpha/intercept percentage used in the static forecast.",
        "bear_order_growth_pct": "Bear-case order growth percentage.",
        "base_order_growth_pct": "Base-case order growth percentage.",
        "bull_order_growth_pct": "Bull-case order growth percentage.",
        "bear_orders_musd": "Bear-case forecast orders in million USD.",
        "base_orders_musd": "Base-case forecast orders in million USD.",
        "bull_orders_musd": "Bull-case forecast orders in million USD.",
        "note": "Notes about static forecast values.",
    },
    "contribution_static": {
        "division": "Division code for contribution row.",
        "driver": "Driver contributing to order growth.",
        "elasticity": "Elasticity used for contribution calculation.",
        "current_growth_pct": "Current driver growth percentage.",
        "driver_impact_pct": "Driver impact as percentage contribution.",
        "baseline_orders_musd": "Baseline orders used for impact calculation.",
        "driver_impact_musd": "Driver impact converted to million USD.",
        "signed_contribution_pct": "Signed contribution percentage, preserving positive or negative direction.",
        "absolute_contribution_pct": "Absolute contribution percentage for ranking magnitude.",
        "note": "Notes about static contribution values.",
    },
    "custom_simulation_template": {
        "division": "Division code for custom simulation input.",
        "driver": "Driver to be assigned a user custom growth assumption.",
        "elasticity": "Elasticity to apply in simulation.",
        "custom_growth_pct": "Blank placeholder where the user can provide custom growth percentage.",
        "recommended_min_pct": "Recommended lower bound for custom growth.",
        "recommended_max_pct": "Recommended upper bound for custom growth.",
        "within_range_expected": "Expected validation result for custom input range checks.",
        "notes_for_agent": "Guidance for assistant behavior during simulation.",
    },
    "kpi_dictionary": {
        "kpi_name": "Readable KPI or driver name.",
        "kpi_code": "Short KPI code used by driver_scenario_input.",
        "definition": "Plain-language KPI definition.",
        "business_meaning": "Why the KPI matters for ABB business context.",
        "synonyms": "Alternate phrases that should map to this KPI.",
        "category": "KPI category such as macro, industrial, sector demand, commodity, or investment.",
        "country_or_region": "Relevant geography.",
        "frequency": "Measurement frequency.",
        "source_type": "Source classification.",
    },
    "monthly_data": {
        "date": "Month start date.",
        "year": "Calendar year.",
        "month": "Calendar month number.",
        "division": "Division code for monthly observations.",
        "orders_received_net_musd": "Monthly net orders received in million USD.",
        "us_gdp": "US GDP driver value.",
        "us_data_center": "US data center / hyperscaler driver value.",
        "china_iip": "China industrial production driver value.",
        "us_computer_products": "US computer products manufacturing driver value.",
        "us_capacity": "US capacity driver value.",
        "us_utility_capex": "US utility capex driver value.",
        "china_gdp": "China GDP driver value.",
        "europe_producer_price_index": "Europe producer price index driver value.",
        "copper_price": "Copper price commodity driver value.",
        "iron_price": "Iron price commodity driver value.",
    },
}


TABLE_QUESTION_GUIDE = {
    "readme": {
        "grain": "One row per workbook documentation item.",
        "use_for": [
            "Questions asking what sheets or data are available in the workbook.",
            "Questions asking whether workbook formulas exist or where calculations should happen.",
            "Questions asking for the purpose of a specific sheet.",
        ],
        "do_not_use_for": [
            "Numeric business answers about orders, growth, forecasts, drivers, KPIs, or trends.",
        ],
        "answer_columns": ["item", "description"],
        "example_questions": [
            "What does this workbook contain?",
            "Which sheet has forecast values?",
            "Are Excel formulas used in this workbook?",
        ],
    },
    "division_baseline": {
        "grain": "One row per division with 2024 baseline orders and 2025 actual/forecast comparison values.",
        "use_for": [
            "Division-level 2025 vs 2024 growth questions.",
            "Baseline orders, actual orders, actual growth MUSD, actual growth percent, or alpha by division.",
            "Questions like 'what was the growth of ELSP for 2025 compared to 2024'.",
        ],
        "do_not_use_for": [
            "Monthly trend questions; use monthly_data.",
            "Driver-level contribution or impact questions; use contribution_static.",
            "Selected driver or elasticity assumption questions; use driver_scenario_input.",
            "Bear/base/bull forecast scenario questions; use point_forecast_static.",
        ],
        "answer_columns": [
            "division",
            "baseline_year",
            "forecast_year",
            "baseline_orders_musd",
            "actual_orders_musd",
            "actual_growth_musd",
            "actual_growth_pct",
            "alpha_pct",
        ],
        "example_questions": [
            "What was the growth of ELSP for 2025 compared to 2024?",
            "Show baseline and actual orders by division.",
            "What is the actual growth percentage for ELSB?",
            "Which division had higher actual orders in 2025?",
            "Show alpha values by division.",
        ],
    },
    "driver_scenario_input": {
        "grain": "One row per division and selected driver input assumption.",
        "use_for": [
            "Selected drivers by division.",
            "Elasticity, current/pessimistic/optimistic driver growth, recommended input ranges, KPI code, unit, frequency, source, and rationale.",
            "Scenario assumption questions before calculating or plotting contributions.",
        ],
        "do_not_use_for": [
            "Already-calculated contribution/impact results; use contribution_static.",
            "KPI definitions or business meanings; use kpi_dictionary.",
            "Monthly historical KPI values; use monthly_data.",
            "Division baseline vs actual orders; use division_baseline.",
        ],
        "answer_columns": [
            "division",
            "driver",
            "kpi_code",
            "selected_flag",
            "elasticity",
            "pessimistic_growth_pct",
            "current_growth_pct",
            "optimistic_growth_pct",
            "recommended_min_pct",
            "recommended_max_pct",
            "unit",
            "frequency",
            "country_or_region",
            "source_type",
            "rationale_short",
        ],
        "example_questions": [
            "Which drivers are selected for ELSP?",
            "What are the elasticities for ELSB drivers?",
            "What is the recommended growth range for Copper Price?",
            "Show current and optimistic growth assumptions for ELSP.",
            "Which KPI codes are used for ELSB?",
        ],
    },
    "point_forecast_static": {
        "grain": "One row per division with precomputed bear/base/bull forecast growth and order values.",
        "use_for": [
            "Precomputed point forecast outputs.",
            "Bear/base/bull forecast order values or order growth percentages by division.",
            "Questions comparing forecast scenarios across divisions.",
        ],
        "do_not_use_for": [
            "Actual 2025 vs 2024 growth; use division_baseline.",
            "Custom user scenario calculations; use calculate_final_value with inputs from the user or custom_simulation_template.",
            "Driver-level impact decomposition; use contribution_static.",
        ],
        "answer_columns": [
            "division",
            "alpha_pct",
            "bear_order_growth_pct",
            "base_order_growth_pct",
            "bull_order_growth_pct",
            "bear_orders_musd",
            "base_orders_musd",
            "bull_orders_musd",
            "note",
        ],
        "example_questions": [
            "Show bear/base/bull forecasts by division.",
            "What is the bull forecast order value for ELSP?",
            "Compare base order growth percent across divisions.",
            "Plot forecast orders by division.",
        ],
    },
    "contribution_static": {
        "grain": "One row per division and driver with precomputed contribution/impact results.",
        "use_for": [
            "Driver contribution, impact, decomposition, bridge, waterfall, ranking, or share questions.",
            "Driver impact in MUSD or percent by division.",
            "Contribution plots by driver.",
        ],
        "do_not_use_for": [
            "Selected driver assumption lists; use driver_scenario_input.",
            "Raw monthly trends; use monthly_data.",
            "KPI definitions; use kpi_dictionary.",
            "Baseline vs actual division growth; use division_baseline.",
        ],
        "answer_columns": [
            "division",
            "driver",
            "elasticity",
            "current_growth_pct",
            "driver_impact_pct",
            "baseline_orders_musd",
            "driver_impact_musd",
            "signed_contribution_pct",
            "absolute_contribution_pct",
            "note",
        ],
        "example_questions": [
            "Show driver contribution for ELSP.",
            "Which driver has the largest impact for ELSB?",
            "Plot driver impact MUSD by driver.",
            "Create a waterfall chart of ELSP driver contributions.",
            "Rank drivers by absolute contribution percentage.",
        ],
    },
    "custom_simulation_template": {
        "grain": "One row per division and driver with template fields for user-provided custom growth inputs.",
        "use_for": [
            "Questions about what inputs are needed for a custom simulation.",
            "Recommended min/max ranges for user custom growth assumptions.",
            "Range validation setup for custom scenario input.",
        ],
        "do_not_use_for": [
            "Static contribution answers; use contribution_static.",
            "Running the final simulation result; use calculate_final_value after collecting inputs.",
            "KPI definitions; use kpi_dictionary.",
        ],
        "answer_columns": [
            "division",
            "driver",
            "elasticity",
            "custom_growth_pct",
            "recommended_min_pct",
            "recommended_max_pct",
            "within_range_expected",
            "notes_for_agent",
        ],
        "example_questions": [
            "What inputs do I need for a custom simulation?",
            "What is the recommended custom growth range for US Utility Capex?",
            "Which drivers need custom growth values for ELSP?",
            "Is my custom growth assumption within the expected range?",
        ],
    },
    "kpi_dictionary": {
        "grain": "One row per KPI/driver definition and synonym set.",
        "use_for": [
            "KPI definitions, business meanings, synonyms, categories, source type, frequency, or geography.",
            "Mapping user language to KPI codes before querying driver_scenario_input or monthly_data.",
        ],
        "do_not_use_for": [
            "Numeric KPI time-series values; use monthly_data.",
            "Selected driver assumptions; use driver_scenario_input.",
            "Contribution values; use contribution_static.",
        ],
        "answer_columns": [
            "kpi_name",
            "kpi_code",
            "definition",
            "business_meaning",
            "synonyms",
            "category",
            "country_or_region",
            "frequency",
            "source_type",
        ],
        "example_questions": [
            "What does KPI code DC_HYPER mean?",
            "Define Copper Price.",
            "What are synonyms for US Computer Products?",
            "Which KPIs are commodity indicators?",
            "What is the business meaning of China IIP?",
        ],
    },
    "monthly_data": {
        "grain": "One row per month and division from 2021 through 2025 with orders and driver KPI values.",
        "use_for": [
            "Monthly trends, time-series plots, over-time analysis, yearly comparisons from monthly observations, and historical driver/KPI values.",
            "Questions about orders_received_net_musd over time by division.",
            "Order versus driver trend charts, such as orders vs Data Center for ELSP.",
            "Metric-against-orders plots, such as Data Center against my ELSP orders.",
            "Historical values for US GDP, US Data Center, China IIP, US Computer Products, US Capacity, US Utility Capex, China GDP, Europe Producer Price Index, Copper Price, or Iron Price.",
            "Correlation-style questions between monthly orders and monthly KPI drivers.",
        ],
        "do_not_use_for": [
            "Simple 2025 vs 2024 baseline growth by division; use division_baseline.",
            "Precomputed driver contribution or impact values; use contribution_static.",
            "KPI definitions; use kpi_dictionary.",
            "Selected driver assumptions; use driver_scenario_input.",
        ],
        "answer_columns": [
            "date",
            "year",
            "month",
            "division",
            "orders_received_net_musd",
            "us_gdp",
            "us_data_center",
            "china_iip",
            "us_computer_products",
            "us_capacity",
            "us_utility_capex",
            "china_gdp",
            "europe_producer_price_index",
            "copper_price",
            "iron_price",
        ],
        "example_questions": [
            "Plot monthly orders trend for ELSP.",
            "Show orders over time for ELSB.",
            "What were ELSP orders by month in 2025?",
            "Plot order vs Data Center for ELSP.",
            "I want to see the plot of Data Center against my ELSP orders.",
            "Plot Copper Price over time.",
            "Compare yearly total orders by division using monthly data.",
            "Show monthly US Data Center values for ELSP.",
        ],
    },
}


def normalize_name(name):
    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower())
    normalized = normalized.strip("_")
    return normalized or "unnamed"


def unique_names(names):
    seen = {}
    result = []
    for name in names:
        base = normalize_name(name)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


def read_readme(excel_path):
    readme = pd.read_excel(excel_path, sheet_name="README")
    descriptions = {}
    if {"Item", "Description"}.issubset(readme.columns):
        for _, row in readme.iterrows():
            item = str(row["Item"]).strip()
            description = str(row["Description"]).strip()
            if item and description and description.lower() != "nan":
                descriptions[item] = description
    return descriptions


def sample_records(df, limit=3):
    samples = df.head(limit).copy()
    samples = samples.where(pd.notnull(samples), None)
    records = []

    for record in samples.to_dict(orient="records"):
        cleaned = {}
        for key, value in record.items():
            if hasattr(value, "isoformat"):
                cleaned[key] = value.isoformat()
            else:
                cleaned[key] = value
        records.append(cleaned)

    return records


def import_workbook(source_path=SOURCE_WORKBOOK):
    if not source_path.exists():
        raise FileNotFoundError(f"Workbook not found: {source_path}")

    DATA_DIR.mkdir(exist_ok=True)
    shutil.copyfile(source_path, LOCAL_WORKBOOK)

    workbook = pd.ExcelFile(source_path)
    readme_descriptions = read_readme(source_path)
    schema = {
        "source_workbook": str(source_path),
        "local_workbook": str(LOCAL_WORKBOOK),
        "database": str(DB_PATH),
        "agent_guidance": AGENT_WORKBOOK_GUIDANCE,
        "tables": [],
        "readme": readme_descriptions,
    }

    with sqlite3.connect(DB_PATH) as connection:
        for sheet_name in workbook.sheet_names:
            df = pd.read_excel(source_path, sheet_name=sheet_name)
            table_name = normalize_name(sheet_name)
            original_columns = [str(column) for column in df.columns]
            normalized_columns = unique_names(original_columns)
            df.columns = normalized_columns
            df.to_sql(table_name, connection, if_exists="replace", index=False)

            schema["tables"].append(
                {
                    "sheet_name": sheet_name,
                    "table_name": table_name,
                    "description": TABLE_METADATA.get(table_name, {}).get(
                        "description",
                        readme_descriptions.get(sheet_name, ""),
                    ),
                    "routing_keywords": TABLE_METADATA.get(table_name, {}).get(
                        "routing_keywords",
                        [],
                    ),
                    "question_guide": TABLE_QUESTION_GUIDE.get(table_name, {}),
                    "row_count": int(len(df)),
                    "sample_records": sample_records(df),
                    "columns": [
                        {
                            "original_name": original,
                            "column_name": normalized,
                            "dtype": str(df[normalized].dtype),
                            "description": COLUMN_DESCRIPTIONS.get(table_name, {}).get(
                                normalized,
                                "",
                            ),
                        }
                        for original, normalized in zip(original_columns, normalized_columns)
                    ],
                }
            )

    SCHEMA_PATH.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"Imported workbook: {source_path}")
    print(f"SQLite database: {DB_PATH}")
    print(f"Schema metadata: {SCHEMA_PATH}")


if __name__ == "__main__":
    import_workbook()
