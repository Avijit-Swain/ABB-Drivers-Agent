def calculate_projected_value(base_value, growth_rate, periods, risk_adjustment=0):
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


def simulate_driver_forecast(baseline_orders_musd, alpha_pct=0, driver_impacts_pct=None):
    """Return order growth and forecast orders from driver impact percentages."""
    impacts = driver_impacts_pct or []
    total_growth_pct = float(alpha_pct) + sum(float(value) for value in impacts)
    forecast_orders_musd = float(baseline_orders_musd) * (1 + total_growth_pct)
    return {
        "total_growth_pct": round(total_growth_pct, 6),
        "forecast_orders_musd": round(forecast_orders_musd, 3),
    }
