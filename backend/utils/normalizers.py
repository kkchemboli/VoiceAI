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


def extract_digits_from_spoken_text(text: str) -> str:
    """Extract raw digits from spoken text, handling English digit words, Hindi digit words,

    'double'/'triple' modifiers, and numeric characters.
    """
    if not text:
        return ""

    lower = text.lower()
    word_to_digit = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4",
        "पाँच": "5", "पांच": "5", "छह": "6", "छः": "6", "सात": "7",
        "आठ": "8", "नौ": "9", "नो": "9",
    }

    # Split into raw tokens and strip ASCII punctuation for Unicode safety (e.g. Hindi words with matras)
    raw_tokens = lower.split()
    tokens = []
    punctuation = ".,!?:;\"'()[]{}-/\\"
    for t in raw_tokens:
        cleaned = t.strip(punctuation)
        if cleaned:
            tokens.append(cleaned)

    digits = []
    multiplier = 1

    for token in tokens:
        if token == "double":
            multiplier = 2
            continue
        elif token == "triple":
            multiplier = 3
            continue

        if token.isdigit():
            for char in token:
                digits.append(char * multiplier)
                multiplier = 1
        elif token in word_to_digit:
            d = word_to_digit[token]
            digits.append(d * multiplier)
            multiplier = 1
        else:
            multiplier = 1

    return "".join(digits)


def format_digits_english(digits: str) -> str:
    """Format a string of digits into English digit names separated by spaces for speech confirmation."""
    digit_words = {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    }
    return " ".join(digit_words.get(d, d) for d in digits if d.isdigit())


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

    if len(clean) == 10 and clean[0] in "56789":
        return "91" + clean

    logger.warning(f"Could not normalize phone number: {phone_str} -> {clean}")
    return clean

