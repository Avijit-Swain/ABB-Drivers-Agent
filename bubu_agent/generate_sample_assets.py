import csv
import random
import sqlite3
from pathlib import Path


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
CSV_PATH = DATA_DIR / "abb_orders.csv"
DB_PATH = DATA_DIR / "abb_sample.db"
FAQ_PATH = DATA_DIR / "abb_faq.txt"
SIMULATOR_PATH = APP_DIR / "simulator.py"


REGIONS = ["North", "South", "East", "West", "Central"]
PRODUCT_LINES = ["Drives", "Motors", "Robotics", "Electrification", "Process Automation"]
CUSTOMERS = [
    "Apex Manufacturing",
    "BlueRiver Utilities",
    "Crest Industrial",
    "Delta Metals",
    "Evergreen Foods",
    "Fusion Mobility",
    "HelioGrid Energy",
    "NovaChem",
]
STATUSES = ["Delivered", "In Progress", "Delayed", "Scheduled"]


def generate_orders(row_count=80):
    random.seed(41)
    rows = []

    for order_id in range(1001, 1001 + row_count):
        product_line = random.choice(PRODUCT_LINES)
        quantity = random.randint(1, 18)
        unit_price = random.randint(1200, 18500)
        discount_rate = random.choice([0, 0.03, 0.05, 0.08, 0.1])
        revenue = round(quantity * unit_price * (1 - discount_rate), 2)
        satisfaction_score = random.randint(72, 98)

        rows.append(
            {
                "order_id": order_id,
                "customer": random.choice(CUSTOMERS),
                "region": random.choice(REGIONS),
                "product_line": product_line,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_rate": discount_rate,
                "revenue": revenue,
                "status": random.choice(STATUSES),
                "satisfaction_score": satisfaction_score,
            }
        )

    return rows


def write_csv(rows):
    DATA_DIR.mkdir(exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_sqlite(rows):
    DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("DROP TABLE IF EXISTS orders")
        connection.execute(
            """
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                customer TEXT,
                region TEXT,
                product_line TEXT,
                quantity INTEGER,
                unit_price REAL,
                discount_rate REAL,
                revenue REAL,
                status TEXT,
                satisfaction_score INTEGER
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO orders (
                order_id,
                customer,
                region,
                product_line,
                quantity,
                unit_price,
                discount_rate,
                revenue,
                status,
                satisfaction_score
            )
            VALUES (
                :order_id,
                :customer,
                :region,
                :product_line,
                :quantity,
                :unit_price,
                :discount_rate,
                :revenue,
                :status,
                :satisfaction_score
            )
            """,
            rows,
        )


def write_faq():
    DATA_DIR.mkdir(exist_ok=True)
    FAQ_PATH.write_text(
        """ABB Assistant FAQ

Q: What is this prototype for?
A: This prototype validates an executive-facing assistant experience for ABB-style structured data, small text knowledge, plotting, and calculations.

Q: What table is available?
A: The local SQLite database contains an orders table with customer, region, product line, quantity, unit price, discount rate, revenue, delivery status, and satisfaction score.

Q: How should leadership use it?
A: Leadership can test natural language questions about sample commercial and operational data before the assistant is connected to real systems.

Q: What is echo mode?
A: Echo mode keeps the visible app behavior simple by returning the user's message while backend tools are being developed and tested.

Q: What will be added next?
A: The next iteration will connect the assistant node to real tool-calling logic, richer SQL generation, chart rendering, validated calculations, and human clarification handling.
""",
        encoding="utf-8",
    )


def write_simulator():
    SIMULATOR_PATH.write_text(
        '''def calculate_projected_value(base_value, growth_rate, periods, risk_adjustment=0):
    """Return a projected value after growth and risk adjustment."""
    projected = float(base_value) * ((1 + float(growth_rate)) ** int(periods))
    adjusted = projected * (1 - float(risk_adjustment))
    return round(adjusted, 2)


def simulate_service_margin(revenue, cost, warranty_reserve=0):
    """Return service margin percentage after warranty reserve."""
    net_revenue = float(revenue) - float(warranty_reserve)
    if net_revenue == 0:
        return 0
    margin = (net_revenue - float(cost)) / net_revenue
    return round(margin * 100, 2)
''',
        encoding="utf-8",
    )


def main():
    rows = generate_orders()
    write_csv(rows)
    write_sqlite(rows)
    write_faq()
    write_simulator()
    print(f"Wrote {CSV_PATH}")
    print(f"Wrote {DB_PATH}")
    print(f"Wrote {FAQ_PATH}")
    print(f"Wrote {SIMULATOR_PATH}")


if __name__ == "__main__":
    main()
