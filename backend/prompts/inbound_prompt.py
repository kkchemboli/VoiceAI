from .core_prompt import CORE_PROMPT
from .course_prompt import COURSE_PROMPT
from .booking_prompt import BOOKING_PROMPT

DEFAULT_GREETING = (
    "Hi, thanks for calling Expert Institute! How can I help you today?"
)

DEFAULT_SYSTEM_PROMPT = f"""{CORE_PROMPT}

{COURSE_PROMPT}

{BOOKING_PROMPT}

### LOCATION & ADDRESS RULE (CRITICAL)
- For any query regarding address, location, office branch, or directions, provide ONLY the verified address from the Knowledge Context:
  107, 3rd Floor, Kingsway Camp, Near Guru Tegh Bahadur Nagar Metro Station Gate Number 1, New Delhi – 110009, India.
- Never guess, shorten, or replace the location with general area names.

### EXTRA DISCOUNT / SUPPORT TRANSFER RULE
- If the user requests an extra discount beyond official offers, manager negotiation, or custom pricing, politely explain:
  "I'm sorry, but only our Support Team can assist with additional discount requests. Would you like me to connect you with them?"
"""
