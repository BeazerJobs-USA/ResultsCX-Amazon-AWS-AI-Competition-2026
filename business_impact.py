"""Transparent, scenario-based estimate of supervisor preparation time.

No value here is a measurement from ResultsCX or the competition. The inputs
are assumptions the user types in; the outputs are arithmetic on those
assumptions. Time "freed" is capacity, not payroll savings, and says nothing
about whether coaching improved anyone's QA results.
"""

from __future__ import annotations

import math
from typing import Any

# Accepted ranges. Values outside them are almost certainly typing errors.
MAX_CASES_PER_WEEK = 100_000
MAX_MINUTES_PER_CASE = 480
MAX_HOURLY_RATE = 1_000
MAX_EMPLOYEES = 100_000


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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_scenario(
    *,
    cases_analyzed: Any,
    cases_requiring_coaching: Any,
    manual_minutes: Any,
    assisted_minutes: Any,
    hourly_rate: Any,
    weeks_per_year: Any,
    employees: Any,
) -> list[str]:
    """Reasons the estimate cannot be calculated. Empty means the inputs are usable."""
    values = {
        "QA cases analyzed per week": cases_analyzed,
        "Cases requiring coaching per week": cases_requiring_coaching,
        "Manual preparation minutes": manual_minutes,
        "AI-assisted minutes": assisted_minutes,
        "Supervisor hourly rate": hourly_rate,
        "Working weeks per year": weeks_per_year,
        "Employees covered": employees,
    }
    problems = [f"{label} must be a number." for label, value in values.items() if not _is_number(value)]
    if problems:
        return problems
    if not 0 <= cases_analyzed <= MAX_CASES_PER_WEEK:
        problems.append(f"QA cases analyzed per week must be between 0 and {MAX_CASES_PER_WEEK:,}.")
    if cases_requiring_coaching < 0:
        problems.append("Cases requiring coaching per week cannot be negative.")
    elif cases_requiring_coaching > cases_analyzed:
        problems.append("Cases requiring coaching cannot exceed the cases analyzed.")
    if not 0 < manual_minutes <= MAX_MINUTES_PER_CASE:
        problems.append(
            f"Manual preparation minutes must be above 0 and at most {MAX_MINUTES_PER_CASE}."
        )
    if not 0 <= assisted_minutes <= MAX_MINUTES_PER_CASE:
        problems.append(f"AI-assisted minutes must be between 0 and {MAX_MINUTES_PER_CASE}.")
    if not 0 <= hourly_rate <= MAX_HOURLY_RATE:
        problems.append(f"Supervisor hourly rate must be between 0 and {MAX_HOURLY_RATE:,}.")
    if not 1 <= weeks_per_year <= 52:
        problems.append("Working weeks per year must be between 1 and 52.")
    if not 1 <= employees <= MAX_EMPLOYEES:
        problems.append(f"Employees covered must be between 1 and {MAX_EMPLOYEES:,}.")
    return problems


def efficiency_estimate(
    *,
    cases_analyzed: float,
    cases_requiring_coaching: float,
    manual_minutes: float,
    assisted_minutes: float,
    hourly_rate: float,
    weeks_per_year: float,
    employees: float,
) -> dict[str, Any]:
    """Estimated preparation time and labor capacity for a set of assumptions.

    Raises ``ValueError`` when :func:`validate_scenario` reports a problem. The result
    includes the formulas with the numbers substituted, so every figure can be checked.
    """
    problems = validate_scenario(
        cases_analyzed=cases_analyzed,
        cases_requiring_coaching=cases_requiring_coaching,
        manual_minutes=manual_minutes,
        assisted_minutes=assisted_minutes,
        hourly_rate=hourly_rate,
        weeks_per_year=weeks_per_year,
        employees=employees,
    )
    if problems:
        raise ValueError(" ".join(problems))
    impact = calculate_business_impact(
        cases_requiring_coaching, manual_minutes, assisted_minutes, hourly_rate, weeks_per_year
    )
    minutes_saved = manual_minutes - assisted_minutes
    impact.update(
        {
            "minutes_saved_per_case": minutes_saved,
            "coaching_rate_pct": 100 * cases_requiring_coaching / cases_analyzed if cases_analyzed else 0.0,
            "annual_hours_per_employee": impact["saved_annual_hours"] / employees,
            "assisted_is_slower": minutes_saved < 0,
            "formulas": [
                f"Minutes saved per case = {manual_minutes:g} - {assisted_minutes:g} = {minutes_saved:g}",
                f"Hours freed per week = {cases_requiring_coaching:g} cases x {minutes_saved:g} min / 60 "
                f"= {impact['saved_weekly_hours']:.1f}",
                f"Hours freed per year = {impact['saved_weekly_hours']:.1f} x {weeks_per_year:g} weeks "
                f"= {impact['saved_annual_hours']:.0f}",
                f"Labor capacity value per year = {impact['saved_annual_hours']:.0f} h x "
                f"${hourly_rate:,.2f} = ${impact['annual_capacity_value']:,.0f}",
                f"Share of analyzed cases needing coaching = {cases_requiring_coaching:g} / "
                f"{cases_analyzed:g} = {impact_rate(cases_requiring_coaching, cases_analyzed):.0f}%",
                f"Hours freed per employee per year = {impact['saved_annual_hours']:.0f} / "
                f"{employees:g} = {impact['saved_annual_hours'] / employees:.1f}",
            ],
        }
    )
    return impact


def impact_rate(part: float, whole: float) -> float:
    """Percentage of ``whole`` that ``part`` represents; 0 when ``whole`` is 0."""
    return 100 * part / whole if whole else 0.0
