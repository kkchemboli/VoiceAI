import logging
import re

logger = logging.getLogger("voice-agent")


def _normalize_slot_key(text: str) -> str:
    """Canonicalize a slot reference so LLM phrasing variations still match.

    Handles case, punctuation, ordinals, an optional year, an optional "at"
    separator, and either day-first or month-first date ordering.
    """
    text = text.lower()
    text = re.sub(r"[,\-–—/;.]+", " ", text)
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"\b\d{4}\b", "", text)  # drop the year
    text = text.replace("a m", "am").replace("p m", "pm")

    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
        "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
        "november": 11, "december": 12,
    }

    def as_int(token: str):
        try:
            return int(token)
        except ValueError:
            return None

    day = month = hour = minute = None
    ampm = ""
    tokens = text.split()
    for token in tokens:
        if month is None and token in months:
            month = months[token]
        elif day is None:
            d = as_int(token)
            if d is not None and d <= 31:
                day = d
        elif token in ("am", "pm"):
            ampm = token
    for token in tokens:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", token)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))

    if day is not None and month is not None and hour is not None and minute is not None:
        return f"{month:02d}-{day:02d}-{hour:02d}-{minute:02d}-{ampm}"

    text = re.sub(r"\b(at)\b", "", " ".join(tokens))
    return re.sub(r"\s+", " ", text).strip()


def _normalize_phone_e164(phone_str):
    """
    Normalize an Indian phone number to E.164-like format without '+'.
    Handles: '+91XXXXXXXXXX', '0XXXXXXXXXX', '91XXXXXXXXXX', 'XXXXXXXXXX',
    and outbound SIP identities with UUID suffix.
    """
    if not phone_str:
        return None

    clean = str(phone_str).strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    clean = clean.lstrip("+")

    if "_" in clean:
        clean = clean.split("_")[0]

    if clean.startswith("0"):
        clean = clean[1:]

    if clean.startswith("91") and len(clean) == 12:
        return clean

    if len(clean) == 10 and clean[0] in "6789":
        return "91" + clean

    logger.warning(f"Could not normalize phone number: {phone_str} -> {clean}")
    return clean
