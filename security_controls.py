"""Small, explainable privacy and compliance checks for the QA coaching demo.

These are *additional* checks after the input parser has masked known fields.
Pattern matching cannot guarantee that healthcare data is de-identified.
"""

import re


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


def sanitize_text(value, known_names=()):
    """Return (masked text, number of replacements); never change input in place."""
    text = str(value)
    count = 0
    # Known staff names come from local mappings; never put the mapping in AI data.
    for name in sorted(set(known_names), key=len, reverse=True):
        if name and len(name.strip()) >= 3:
            text, n = re.subn(r"\b" + re.escape(name.strip()) + r"\b", "[PERSON]", text, flags=re.I)
            count += n
    for _label, pattern, token in MASK_RULES:
        text, n = pattern.subn(token, text)
        count += n
    return text, count


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


def critical_compliance_alerts(data):
    """Flag *all* failed compliance items for human review.

    Identity/privacy keywords increase priority, but other compliance failures
    must not disappear just because an evaluator used different wording. This
    is an in-app *flag*, not a legal determination or a sent notification.
    """
    keywords = ("identit", "verif", "privacy", "disclos", "hipaa", "protected", "confidential")
    alerts = []
    seen = set()
    for record in data.get("records", []):
        if record.get("source") != "CO" or record.get("result") != "No":
            continue
        description = f"{record.get('behavior', '')} {record.get('comment', '')}".lower()
        record_id = record.get("record_id", "Unknown")
        if record_id not in seen:
            sensitive = any(word in description for word in keywords)
            alerts.append(
                {
                    "record_id": record_id,
                    "agent": record.get("agent", "Unknown"),
                    "behavior": record.get("behavior", "Compliance behavior"),
                    "priority": "Urgent identity/privacy review" if sensitive else "Compliance review",
                }
            )
            seen.add(record_id)
    return alerts
