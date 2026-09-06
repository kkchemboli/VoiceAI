import re
from typing import List

QUESTION_SIGNALS: List[str] = [
    "?",
    "what",
    "how",
    "when",
    "where",
    "who",
    "kya",
    "kaise",
    "kitna",
    "कितना",
    "क्या",
    "कैसे",
]


def is_question_turn(user_text: str) -> bool:
    """Determine if a user turn is a question signal requiring RAG or fallback response."""
    if not user_text:
        return False
    lower_text = user_text.lower()
    return any(sig in lower_text for sig in QUESTION_SIGNALS)


def classify_intent(user_text: str) -> str:
    """Classify the user utterance into core intent categories."""
    if not user_text:
        return "GENERAL_QUERY"

    lower = user_text.lower()

    if any(k in lower for k in ["extra discount", "more discount", "manager", "senior", "owner", "discounts"]):
        return "EXTRA_DISCOUNT_TRANSFER"

    if any(k in lower for k in ["fee", "fees", "price", "cost", "discount", "पैसे", "फीस"]):
        return "PRICING_QUERY"

    if any(k in lower for k in ["address", "location", "office", "branch", "where", "पता", "कहाँ"]):
        return "ADDRESS_QUERY"

    if any(k in lower for k in ["demo", "book", "slot", "join", "class", "डेमो"]):
        return "DEMO_BOOKING_INTENT"

    if any(k in lower for k in ["course", "syllabus", "duration", "time", "कोर्स"]):
        return "COURSE_QUERY"

    return "GENERAL_QUERY"
