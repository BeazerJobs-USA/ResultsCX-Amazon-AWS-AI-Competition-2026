"""Transparent, scenario-based estimate of supervisor preparation time saved.

No values here are measurements from ResultsCX or the competition.
"""


def calculate_business_impact(cases_per_week, manual_minutes, assisted_minutes, hourly_rate, weeks_per_year=50):
    """Compare manual preparation with AI-assisted preparation AND human review."""
    inputs = (cases_per_week, manual_minutes, assisted_minutes, hourly_rate, weeks_per_year)
    if any(number < 0 for number in inputs):
        raise ValueError("Business scenario inputs cannot be negative")

    manual_weekly_hours = cases_per_week * manual_minutes / 60
    assisted_weekly_hours = cases_per_week * assisted_minutes / 60
    saved_weekly_hours = manual_weekly_hours - assisted_weekly_hours
    return {
        "manual_weekly_hours": manual_weekly_hours,
        "assisted_weekly_hours": assisted_weekly_hours,
        "saved_weekly_hours": saved_weekly_hours,
        "saved_annual_hours": saved_weekly_hours * weeks_per_year,
        "annual_capacity_value": saved_weekly_hours * weeks_per_year * hourly_rate,
        "time_reduction_pct": 100 * saved_weekly_hours / manual_weekly_hours if manual_weekly_hours else 0,
    }
