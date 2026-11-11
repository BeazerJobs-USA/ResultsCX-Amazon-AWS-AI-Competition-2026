"""Shared display helpers and input widgets that keep their values.

Two rules are applied throughout the interface and implemented here once:

* **Indicators never rely on color alone.** Every badge carries an icon and
  words: a green thumbs-up *and* "Pass", a red thumbs-down *and* "Fail".
* **Text the application did not write is shown as text.** Evaluator comments,
  uploaded values, typed notes, and model output go through
  :func:`security_controls.escape_markdown` before they reach ``st.markdown``,
  so they cannot add links, images, or HTML to the page.

The input helpers below write each value into a plain dictionary (the *store*)
both from the widget's return value and from an ``on_change`` callback. The
callback matters when a user types and immediately changes workspace: that run
never draws the widget again, and Streamlit would otherwise drop what was typed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any, Callable, Sequence

import pandas as pd
import streamlit as st

from security_controls import escape_markdown as esc
from ui import state
from workflow import common, compliance

# -- labels that say who or what produced a piece of information -------------------------
FACT_LABEL = ":blue-badge[:material/fact_check: Recorded in the QA data]"
AI_LABEL = ":violet-badge[:material/smart_toy: AI proposal, not verified]"
HUMAN_LABEL = ":green-badge[:material/person: Supervisor decision]"
DEMO_LABEL = ":orange-badge[:material/edit_note: Handwritten demonstration content]"
RULE_LABEL = ":blue-badge[:material/rule: Rule-based, not AI]"

# Plain-text indicators for dataframe cells, where Markdown is not rendered.
RESULT_TEXT = {"Pass": "👍 Pass", "Fail": "👎 Fail"}
_RISK_STYLE = {
    compliance.LOW: ("blue", ":material/info:"),
    compliance.MODERATE: ("orange", ":material/priority_high:"),
    compliance.HIGH: ("red", ":material/warning:"),
    compliance.CRITICAL: ("red", ":material/error:"),
}
_STAGE_STYLE = {
    "complete": ("green", ":material/check_circle:", "Complete"),
    "active": ("blue", ":material/pending:", "In progress"),
    "waiting": ("gray", ":material/radio_button_unchecked:", "Not started"),
}


def badge(text: str, tone: str = "gray", icon: str = "") -> str:
    """A colored Markdown badge. ``text`` is escaped; ``icon`` is a Material icon code."""
    return f":{tone}-badge[{icon + ' ' if icon else ''}{esc(text)}]"


def result_badge(result: str) -> str:
    """Green thumbs-up for a pass, red thumbs-down for a fail, neutral otherwise."""
    if result == "Pass":
        return badge("Pass", "green", ":material/thumb_up:")
    if result == "Fail":
        return badge("Fail", "red", ":material/thumb_down:")
    return badge("Not scored", "gray", ":material/help:")


def pending_badge(text: str = "Pending review") -> str:
    return badge(text, "gray", ":material/hourglass_empty:")


def insufficient_badge(text: str = "Insufficient evidence") -> str:
    return badge(text, "gray", ":material/help:")


def risk_badge(level: str) -> str:
    if level not in _RISK_STYLE:
        return badge("No risk level", "gray")
    tone, icon = _RISK_STYLE[level]
    return badge(f"{level} risk", tone, icon)


def stage_badge(status: str) -> str:
    tone, icon, words = _STAGE_STYLE.get(status, _STAGE_STYLE["waiting"])
    return badge(words, tone, icon)


def flag_status_badge(status: str) -> str:
    label = compliance.STATUS_LABELS.get(status, status)
    if status in (compliance.OPEN, compliance.EVIDENCE_REQUESTED):
        return pending_badge(label)
    if status == compliance.REFERRED:
        return badge(label, "orange", ":material/forward:")
    if status == compliance.DISMISSED:
        return badge(label, "gray", ":material/block:")
    if status == compliance.FINDING_RECORDED:
        return badge(label, "red", ":material/gavel:")
    return badge(label, "green", ":material/task_alt:")


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A Markdown table. Cells must already be escaped or be trusted badge markup."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(cell or " " for cell in row) + " |" for row in rows]
    return "\n".join(lines)


_BADGE = re.compile(r"^:[a-z]+-badge\[(?::material/[a-z_0-9]+: )?(.*)\]$", re.S)


def plain(markup: Any) -> str:
    """Text of a badge or an escaped string, for places that do not render Markdown."""
    text = str(markup or "").strip()
    match = _BADGE.match(text)
    return re.sub(r"\\(.)", r"\1", match.group(1) if match else text)


def data_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """A scrolling table for wide content. Cells may be badge markup or escaped text."""
    st.dataframe(
        pd.DataFrame([[plain(cell) for cell in row] for row in rows], columns=list(headers)),
        hide_index=True,
        width="stretch",
    )


def page_header(title: str, purpose: str) -> None:
    st.header(title)
    st.caption(purpose)


def quote(text: Any, empty: str = "No comment was recorded.") -> None:
    """Show untrusted free text as a quotation."""
    cleaned = " ".join(str(text or "").split())
    if cleaned:
        st.markdown("> " + esc(cleaned))
    else:
        st.caption(empty)


def facts(pairs: Sequence[tuple[str, Any]]) -> None:
    """Label/value lines. Values are escaped."""
    st.markdown("  \n".join(f"**{label}:** {esc(value)}" for label, value in pairs))


def bullets(items: Sequence[Any]) -> None:
    """A bulleted list of untrusted text."""
    if items:
        st.markdown("\n".join(f"- {esc(item)}" for item in items))


def show_problems(problems: Sequence[str], heading: str = "This cannot be recorded yet:") -> None:
    st.error(heading + "\n\n" + "\n".join(f"- {esc(problem)}" for problem in problems))


def show_flash() -> None:
    """Display the message left by the previous run, once."""
    message = st.session_state.get("flash")
    if not message:
        return
    st.session_state["flash"] = None
    renderer = {"success": st.success, "warning": st.warning, "error": st.error}.get(message["level"], st.info)
    renderer(esc(message["text"]))


def role_allows(permission: str, action: str) -> bool:
    """Whether the current role may take ``action``; explains it on screen when it may not."""
    _name, role = state.actor()
    if state.read_only():
        st.info(f"This is a replayed run, shown as it was saved. It is not possible to {action} here.")
        return False
    if common.can(role, permission):
        return True
    st.info(
        f"Only the {esc(common.allowed_roles(permission))} role can {action}. "
        f"The current role is {esc(role)}; change it in the sidebar to continue."
    )
    return False


def goto_button(label: str, section: str, key: str, primary: bool = False) -> None:
    st.button(
        label,
        key=key,
        on_click=state.goto,
        args=(section,),
        type="primary" if primary else "secondary",
        icon=":material/arrow_forward:",
    )


def empty_state(message: str, button_label: str, section: str, key: str) -> None:
    """What a workspace shows when the stage before it has not been done."""
    st.info(message)
    goto_button(button_label, section, key, primary=True)


# -- inputs that keep their values -------------------------------------------------------
def _capture(store: dict[str, Any], field: str, key: str) -> None:
    """``on_change`` callback: save a widget's new value before the script runs."""
    if key in st.session_state:
        store[field] = st.session_state[key]


def _dynamic_key(key: str, options: Sequence[Any]) -> str:
    """A key that changes when the option list changes, so stale selections cannot linger."""
    digest = hashlib.sha1("|".join(map(str, options)).encode("utf-8")).hexdigest()[:8]
    return f"{key}:{digest}"


def _index(choices: list[Any], current: Any, allow_empty: bool) -> int | None:
    """Position of the stored value; the first option (or nothing) when it is not offered."""
    if current in choices:
        return choices.index(current)
    return None if allow_empty else 0


def text_input(label: str, store: dict[str, Any], field: str, *, key: str, **kwargs: Any) -> str:
    value = st.text_input(
        label, value=str(store.get(field) or ""), key=key, on_change=_capture, args=(store, field, key), **kwargs
    )
    store[field] = value
    return value


def text_area(label: str, store: dict[str, Any], field: str, *, key: str, **kwargs: Any) -> str:
    value = st.text_area(
        label, value=str(store.get(field) or ""), key=key, on_change=_capture, args=(store, field, key), **kwargs
    )
    store[field] = value
    return value


def checkbox(label: str, store: dict[str, Any], field: str, *, key: str, **kwargs: Any) -> bool:
    value = st.checkbox(
        label, value=bool(store.get(field)), key=key, on_change=_capture, args=(store, field, key), **kwargs
    )
    store[field] = value
    return value


def selectbox(
    label: str,
    options: Sequence[Any],
    store: dict[str, Any],
    field: str,
    *,
    key: str,
    format_func: Callable[[Any], str] = str,
    dynamic: bool = False,
    allow_empty: bool = False,
    **kwargs: Any,
) -> Any:
    """Dropdown. Option text is shown as plain text, so it must not be escaped."""
    choices = list(options)
    widget_key = _dynamic_key(key, choices) if dynamic else key
    index = _index(choices, store.get(field), allow_empty)
    value = st.selectbox(
        label,
        choices,
        index=index,
        format_func=format_func,
        key=widget_key,
        on_change=_capture,
        args=(store, field, widget_key),
        **kwargs,
    )
    store[field] = value
    return value


def radio(
    label: str,
    options: Sequence[Any],
    store: dict[str, Any],
    field: str,
    *,
    key: str,
    format_func: Callable[[Any], str] = str,
    dynamic: bool = False,
    allow_empty: bool = False,
    **kwargs: Any,
) -> Any:
    """Radio group. Option text is rendered as Markdown, so escape anything untrusted."""
    choices = list(options)
    widget_key = _dynamic_key(key, choices) if dynamic else key
    index = _index(choices, store.get(field), allow_empty)
    value = st.radio(
        label,
        choices,
        index=index,
        format_func=format_func,
        key=widget_key,
        on_change=_capture,
        args=(store, field, widget_key),
        **kwargs,
    )
    store[field] = value
    return value


def multiselect(
    label: str,
    options: Sequence[Any],
    store: dict[str, Any],
    field: str,
    *,
    key: str,
    format_func: Callable[[Any], str] = str,
    dynamic: bool = False,
    **kwargs: Any,
) -> list[Any]:
    choices = list(options)
    widget_key = _dynamic_key(key, choices) if dynamic else key
    default = [item for item in store.get(field) or [] if item in choices]
    value = st.multiselect(
        label,
        choices,
        default=default,
        format_func=format_func,
        key=widget_key,
        on_change=_capture,
        args=(store, field, widget_key),
        **kwargs,
    )
    store[field] = list(value)
    return store[field]


def number_input(
    label: str, store: dict[str, Any], field: str, *, key: str, default: int | float, **kwargs: Any
) -> int | float:
    """Numeric input. ``default``, ``min_value``, ``max_value`` and ``step`` must share one type."""
    kind = type(default)
    current = store.get(field, default)
    current = kind(current) if isinstance(current, (int, float)) and not isinstance(current, bool) else default
    low, high = kwargs.get("min_value"), kwargs.get("max_value")
    if low is not None:
        current = max(current, low)
    if high is not None:
        current = min(current, high)
    value = st.number_input(
        label, value=current, key=key, on_change=_capture, args=(store, field, key), **kwargs
    )
    store[field] = value
    return value


def date_input(
    label: str,
    store: dict[str, Any],
    field: str,
    *,
    key: str,
    default: dt.date,
    min_value: dt.date | None = None,
    max_value: dt.date | None = None,
    **kwargs: Any,
) -> dt.date:
    current = store.get(field)
    current = current if isinstance(current, dt.date) else default
    if min_value and current < min_value:
        current = min_value
    if max_value and current > max_value:
        current = max_value
    value = st.date_input(
        label,
        value=current,
        min_value=min_value,
        max_value=max_value,
        key=key,
        on_change=_capture,
        args=(store, field, key),
        format="YYYY-MM-DD",
        **kwargs,
    )
    store[field] = value
    return value
