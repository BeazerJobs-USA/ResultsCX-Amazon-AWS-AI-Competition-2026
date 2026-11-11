"""Small, explainable privacy checks for the QA coaching demo.

Two boundaries are handled here:

* **Before a model call.** :func:`sanitize_payload` and :func:`sanitize_text`
  mask recognizable identifier patterns in everything bound for Bedrock.
* **Before the screen.** :func:`escape_markdown` neutralizes Markdown and HTML
  in text the application did not write (evaluator comments, uploaded files,
  model output), so it is displayed as text and cannot embed links or images.

These are *additional* checks after the input parser has masked known fields.
Pattern matching cannot guarantee that healthcare data is de-identified.
"""

from __future__ import annotations

import re
import string
from typing import Any

# These patterns are intentionally readable so a student can inspect the rules.
# Put the more specific patterns first (for example, a phone before a generic ID).
MASK_RULES = [
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[EMAIL]"),
    (
        "phone",
        re.compile(r"(?<!\w)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\w)"),
        "[PHONE]",
    ),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (
        "date",
        re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b"),
        "[DATE]",
    ),
    (
        "labeled identifier",
        re.compile(
            r"\b(?:patient|member|account|policy|claim)\s*(?:id|number|#|no\.)\s*[:#-]?\s*[A-Z0-9-]{4,}\b",
            re.I,
        ),
        "[IDENTIFIER]",
    ),
    (
        "labeled person",
        re.compile(
            r"\b(?:patient|member|customer)\s+name\s*:\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b",
            re.I,
        ),
        "[PERSON]",
    ),
    ("long number", re.compile(r"(?<!\w)\d{9,}(?!\w)"), "[ID]"),
]

# Keys of the application's own date fields. Their values are withheld from the model
# on purpose, and are counted apart from identifiers found in free text.
STRUCTURED_DATE_KEYS = frozenset({"date", "call_date", "date_range"})


def sanitize_text(value, known_names=()):
    """Return (masked text, number of replacements); never change input in place."""
    text, counts = _mask(value, known_names)
    return text, sum(counts.values())


def _mask(value, known_names=()) -> tuple[str, dict[str, int]]:
    text = str(value)
    counts: dict[str, int] = {}
    # Known staff names come from local mappings; never put the mapping in AI data.
    for name in sorted(set(known_names), key=len, reverse=True):
        if name and len(name.strip()) >= 3:
            text, n = re.subn(r"\b" + re.escape(name.strip()) + r"\b", "[PERSON]", text, flags=re.I)
            if n:
                counts["known staff name"] = counts.get("known staff name", 0) + n
    for label, pattern, token in MASK_RULES:
        text, n = pattern.subn(token, text)
        if n:
            counts[label] = counts.get(label, 0) + n
    return text, counts


def sanitize_payload(value, known_names=()):
    """Scrub every string in nested model input, including free-text field names.

    Only a new copy is returned. Totals and numeric defect rates are preserved.
    """
    if isinstance(value, str):
        return sanitize_text(value, known_names)
    if isinstance(value, list):
        results = [sanitize_payload(item, known_names) for item in value]
        return [item for item, _ in results], sum(count for _, count in results)
    if isinstance(value, dict):
        cleaned, count = {}, 0
        for key, item in value.items():
            safe_key, key_count = sanitize_text(key, known_names) if isinstance(key, str) else (key, 0)
            safe_item, item_count = sanitize_payload(item, known_names)
            cleaned[safe_key] = safe_item
            count += key_count + item_count
        return cleaned, count
    return value, 0


def mask_report(payload: Any, known_names=()) -> dict[str, Any]:
    """Explain what the pre-model pass would mask in ``payload``, without changing it.

    ``free_text`` counts identifier patterns found in text people wrote, by rule.
    ``structured_dates`` counts the application's own call-date fields, which are
    withheld from the model deliberately. Reporting them together would make an
    invented dataset with no identifiers look as if it had contained some.
    """
    free_text: dict[str, int] = {}
    structured_dates = 0

    def walk(value: Any, parent_key: str = "") -> None:
        nonlocal structured_dates
        if isinstance(value, str):
            _text, counts = _mask(value, known_names)
            if parent_key in STRUCTURED_DATE_KEYS:
                structured_dates += counts.pop("date", 0)
            for label, n in counts.items():
                free_text[label] = free_text.get(label, 0) + n
        elif isinstance(value, list):
            for item in value:
                walk(item, parent_key)
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    walk(key)
                walk(item, key if isinstance(key, str) else "")

    walk(payload)
    return {
        "free_text": free_text,
        "free_text_total": sum(free_text.values()),
        "structured_dates": structured_dates,
    }


_MARKDOWN_SPECIALS = re.compile("([" + re.escape(string.punctuation) + "])")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_markdown(value: Any, keep_newlines: bool = False) -> str:
    """Make untrusted text safe to place inside a Markdown string.

    Every ASCII punctuation character is backslash-escaped, which CommonMark renders
    as the literal character. That disables links, images, HTML tags, emphasis,
    headings, tables, LaTeX, and Streamlit directives such as ``:red[...]``. Line
    breaks collapse to spaces unless ``keep_newlines`` is set, so a value cannot break
    out of a table cell or start a new block.
    """
    text = _CONTROL_CHARS.sub(" ", str(value if value is not None else ""))
    if keep_newlines:
        lines = [_MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(line.split())) for line in text.splitlines()]
        return "  \n".join(lines)
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(text.split()))
