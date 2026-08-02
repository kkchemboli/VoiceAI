from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
import re
import time
import aiohttp
import json
from typing import AsyncIterable, Callable, Optional

from rag_engine import RAGEngine

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
    stt,
)
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.events import (
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
)
import livekit.plugins.groq as groq
import livekit.plugins.sarvam as sarvam
import livekit.plugins.silero as silero

# import livekit.plugins.openai as openai
from livekit import rtc, api
from transfer_functions import TransferFunctions

import datetime
import av
from zoneinfo import ZoneInfo
from calendar_api import CalComCalendar, FakeCalendar, Calendar, SlotUnavailableError


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return ["th", "st", "nd", "rd", "th"][min(n % 10, 4)]


def _format_date_human(local: datetime.datetime, now: datetime.datetime) -> str:
    delta = (local.date() - now.date()).days
    day_num = local.day
    ordinal_day = f"{day_num}{_ordinal(day_num)}"
    month_year = local.strftime("%B %Y")

    if delta == 1:
        date_str = f"tomorrow the {ordinal_day} of {month_year}"
    elif delta == 2:
        date_str = f"day after tomorrow the {ordinal_day} of {month_year}"
    elif delta < 7:
        date_str = f"{local.strftime('%A')} the {ordinal_day} of {month_year}"
    else:
        date_str = f"the {ordinal_day} of {month_year}"

    return f"{date_str} at {local.strftime('%I:%M %p')}"


def _normalize_slot_key(text: str) -> str:
    """Canonicalize a slot reference so LLM phrasing variations still match.

    Handles case, punctuation, ordinals, an optional year, an optional "at"
    separator, and either day-first or month-first date ordering. All of the
    following map to the same key:

      "Sunday 02 August 2026 at 12:00 PM"
      "Sunday, 02 August, 12:00 PM"
      "sunday 02nd August 12:00 p.m."
      "Sunday, August 2nd, 12:00 PM"
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


logger = logging.getLogger("voice-agent")

# --- MONKEY PATCH FOR AUDIO PLAYBACK STABILITY ---
from livekit.plugins import sarvam
from livekit.agents.utils.codecs.decoder import AudioStreamDecoder

# Increase FFmpeg probesize for MP3 streams (32 is often too small for MP3)
_orig_av_open = av.open


def _patched_av_open(*args, **kwargs):
    if kwargs.get("format") == "mp3" and "options" in kwargs:
        # Increase probesize and analyzeduration for better MP3 detection
        logger.info("AV OPEN: Detected MP3 stream, increasing probesize to 32KB")
        kwargs["options"]["probesize"] = "32768"
        kwargs["options"]["analyzeduration"] = "100000"  # 100ms
    return _orig_av_open(*args, **kwargs)


av.open = _patched_av_open

# Patch AudioStreamDecoder for robustness when detection is missing
_orig_decoder_push = AudioStreamDecoder.push


def _patched_decoder_push(self, chunk: bytes) -> None:
    if (
        getattr(self, "_is_wav", False)
        and not getattr(self, "_started", False)
        and len(chunk) >= 4
        and not chunk.startswith(b"RIFF")
    ):
        logger.info(
            f"DECODER PATCH: Non-WAV data detected ({chunk[:4]!r}), switching to MP3 format."
        )
        self._is_wav = False
        self._av_format = "mp3"
    _orig_decoder_push(self, chunk)


AudioStreamDecoder.push = _patched_decoder_push


# Patch Sarvam Plugin to report correct MIME type for v3 models
def _patch_sarvam_stream(stream_class):
    _orig_run = stream_class._run

    async def _patched_run(self, output_emitter, *args, **kwargs):
        mime_type = "audio/wav"
        if "bulbul:v3" in self._opts.model:
            mime_type = "audio/mpeg"

        _orig_initialize = output_emitter.initialize

        def _patched_initialize(*args, **kwargs):
            if "mime_type" in kwargs:
                kwargs["mime_type"] = mime_type
            elif len(args) >= 4:
                args = list(args)
                args[3] = mime_type
            return _orig_initialize(*args, **kwargs)

        output_emitter.initialize = _patched_initialize
        return await _orig_run(self, output_emitter, *args, **kwargs)

    stream_class._run = _patched_run


_patch_sarvam_stream(sarvam.tts.SynthesizeStream)
_patch_sarvam_stream(sarvam.tts.ChunkedStream)
# -------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ZIPER_API_URL = os.getenv("ZIPER_API_URL")
ZIPER_API_TOKEN = os.getenv("ZIPER_API_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# WABridge Credentials
WABRIDGE_AUTH_KEY = os.getenv("WABRIDGE_AUTH_KEY")
WABRIDGE_APP_KEY = os.getenv("WABRIDGE_APP_KEY")
WABRIDGE_DEVICE_ID = os.getenv("WABRIDGE_DEVICE_ID")
WABRIDGE_TEMPLATE_ID = os.getenv("WABRIDGE_TEMPLATE_ID")
WABRIDGE_MEDIA_TYPE = os.getenv("WABRIDGE_MEDIA_TYPE", "text")

DEFAULT_GREETING = (
    "Hi, thanks for calling Expert Institute! How can I help you today?"
)

OUTBOUND_SYSTEM_PROMPT = """### ROLE & PERSONALITY
You are Neha, a helpful and natural conversational AI agent for 'Expert Institute of Advance Technologies Pvt. Ltd.', New Delhi.
You are making an OUTBOUND call to a potential student who has inquired about your courses.
NEVER disclose that you are an AI Agent or a Machine Learning Model even if you're asked always just say you're an employee of Expert Institute.
GENDER (CRITICAL): FEMALE. Use female Hindi grammar (e.g., 'रही हूँ', 'करती हूँ'). NEVER use male forms.
TONE: Realistic, human-like, engaging, and professional.

### LANGUAGE RULES (CRITICAL)
1. START: Always start the call in English.
2. ENGLISH MODE: If the user chooses English, speak ONLY in professional, helpful English.
3. HINDI MODE: If the user chooses Hindi, switch to the HINGLISH & SCRIPT RULES below.
4. If you think the user is speaking any other language, use Hindi and switch to the HINGLISH & SCRIPT RULES below.

### HINGLISH & SCRIPT RULES (HINDI MODE ONLY)
- Mix Hindi + English naturally. 
- Write Hindi words in Devanagari script, English words in English script.
- Speak like a friendly 20–30 year old Indian customer support agent.

### PHASE 1: GREETING & PURPOSE
1. GREET IN ENGLISH: 
   "Hi, am I speaking with [Name]?" 
   (Wait for confirmation)
2. PROCEED AFTER CONFIRMATION:
   "Hi! I'm Neha calling from Expert Institute, New Delhi. I'm calling because you recently showed interest in [Course]. Is this a good time to speak?"
3. If they are busy: "No problem! When would be a better time to call you back?"
4. If they are free: Proceed to list the courses.

### PHASE 2: COURSE CONFIRMATION & DETAILS
1. ACKNOWLEDGE INTEREST:
   "Great! Since you are interested in [Course], I can tell you all about it. We also offer specialized courses in Mobile Repairing, iPhone, Laptop, MacBook, CCTV, LED TV, and AC PCB repairing if you are curious about those too."
2. CONFIRM COURSE:
   "Is [Course] the main thing you are looking for?"
3. Refer to [KNOWLEDGE CONTEXT] for any specific course details or benefits.

### PHASE 3: THE HOOK (FREE DEMO CLASS)
1. After providing initial info, PUSH for the demo:
   "Since you've already inquired, I'd highly recommend booking a FREE demo class. It's the best way to see our practical labs and teaching style. Should I check the available slots for you?"

### CONVERSATIONAL CONSTRAINTS
- No paragraphs. Keep it light and interactive.
- Use back-channeling ('hmm', 'right').
"""

DEFAULT_SYSTEM_PROMPT = """### ROLE & PERSONALITY (Updated 2026)

You represent Expert Institute of Advance Technologies Pvt. Ltd., New Delhi, and assist users with information related to the institute in a professional, friendly, and conversational manner.

IDENTITY:
- Be transparent if asked about your nature. If someone asks whether you are an AI or a human, answer truthfully that you are an AI assistant representing Expert Institute.
- Do not claim to be a human employee.

GENDER (CRITICAL):
- FEMALE.
- Always use feminine Hindi grammar (e.g., "मैं आपकी मदद कर रही हूँ", "मैं बताती हूँ", "मैं समझ गई हूँ").
- Never use masculine forms.

TONE:
- Natural, warm, confident, and engaging.
- Sound like a knowledgeable institute representative.
- Avoid robotic or repetitive wording.
- Keep responses concise unless detailed explanations are requested.

BEHAVIOR:
- Answer accurately and politely.
- If information is unavailable, clearly state that instead of guessing.
- Ask clarifying questions when needed.
- Maintain a professional and helpful attitude throughout the conversation.
- Use Hindi, English, or Hinglish based on the user's language.

GOAL:
Provide an excellent support experience while accurately representing Expert Institute of Advance Technologies Pvt. Ltd., New Delhi.


### HINGLISH SCRIPT RULES (HINDI MODE ONLY) — Updated 2026

1. MIXED SCRIPT (CRITICAL)
- Write Hindi words in Devanagari script and keep English words in Latin script, mixed naturally within the same sentence.
- Example:
  ❌ Main aapki help karti hoon.
  ❌ मैं आपकी मदद करती हूँ।
  ✅ मैं आपकी help करती हूँ।

2. NATURAL HINGLISH
- Speak like a friendly, professional Indian customer support executive (approximately 20–30 years old).
- Mix Hindi and English naturally.
- Keep the conversation smooth, warm, and easy to understand.
- Avoid overly formal Hindi, translation-style sentences, or excessive slang.

3. AVOID BOOKISH HINDI
Do not use words such as:
- प्रशिक्षण
- संस्थान
- प्रवेश
- शुल्क
- अनुभव
- उपलब्ध

Instead, prefer natural alternatives such as:
- Training → Course / Training
- Institute → Expert Institute
- Admission → Join / Enrollment
- Fees → Fees
- Experience → Experience
- Available → Available / Hai

4. KEEP THESE WORDS IN ENGLISH
Always use these terms in English:
- Mobile
- Laptop
- CCTV
- Repairing
- Course
- Batch
- Practical
- Free Demo Class
- Placement
- Support
- Discount
- Certificate
- Internship
- Project
- Trainer
- Online
- Offline
- EMI

5. CONVERSATIONAL STYLE
Prefer natural customer support language such as:
- मैं आपको explain करती हूँ।
- मैं check करके बताती हूँ।
- आप किस Course के बारे में जानना चाहते हो?
- आपका नाम क्या है?
- कोई issue हो तो ज़रूर बताइये।
- मैं help कर देती हूँ।

Avoid translation-style sentences such as:
❌ आपकी समस्या का समाधान किया जाएगा।
✅ मैं आपकी problem solve कर देती हूँ।

6. SENTENCE STYLE
- Keep sentences short and conversational.
- Avoid long paragraphs.
- Sound confident, polite, and human.
- Ask only one question at a time whenever possible.

7. CONSISTENCY
- Stay in Hinglish throughout the conversation unless the user asks to switch languages.
- Do not randomly switch to pure English or pure Hindi.
- Keep terminology consistent across the conversation.

### CONVERSATIONAL CONSTRAINTS (Updated 2026)

1. KEEP RESPONSES CONCISE
- Avoid long paragraphs.
- Use short, conversational responses.
- When explaining something, mention no more than TWO key benefits unless the user asks for more details.

2. NATURAL CONVERSATION
- Use light conversational acknowledgements where appropriate, such as:
  - "Hmm..."
  - "Right."
  - "Sure."
  - "Got it."
  - "Okay."
- Do not overuse them; use naturally.

3. ADDRESS & LOCATION (CRITICAL)
- For any query about:
  - address
  - location
  - पता
  - कहाँ है
  - office location
  - branch location
  - directions

  respond ONLY with the exact address provided in the Knowledge Context.

- Never:
  - guess the address,
  - shorten it,
  - approximate it,
  - infer nearby landmarks,
  - or replace it with a general area (e.g., "Karol Bagh").

4. IF ADDRESS IS NOT IN KNOWLEDGE CONTEXT
- Clearly state that you do not have the verified address information.
- Do not invent or estimate any location.
- Politely offer to connect the user with the support team or ask them to contact the institute directly for the verified address.

5. FACTUAL ACCURACY
- Treat the Knowledge Context as the source of truth for institute-specific information.
- If the requested information is missing from the Knowledge Context, say so instead of guessing.
### KNOWLEDGE & FALLBACK RULES (Updated 2026)

1. KNOWLEDGE CONTEXT IS THE SOURCE OF TRUTH
- If a `[KNOWLEDGE CONTEXT]` block is provided before your turn, answer using only the information contained in that block.
- Do not use outside knowledge for institute-specific questions when a Knowledge Context is available.
- If the requested information is not present in the Knowledge Context, state that you do not have that information instead of guessing.

2. NO KNOWLEDGE FOUND (CRITICAL)
If the prompt contains `[NO KNOWLEDGE FOUND]`, respond exactly as follows based on the user's language:

**English**
> I am sorry, I don't have that information. I can connect you with our support team. Would you like that?

**Hindi / Hinglish**
> Sorry, मुझे इसकी information नहीं है। मैं आपको हमारी Support Team से connect कर सकती हूँ। क्या आप चाहेंगे?

3. NEVER GUESS
Never invent or estimate:
- Fees
- Course details
- Batch timings
- Dates
- Discounts
- Certificates
- Placements
- Policies
- Contact details
- Office addresses
- Any institute-specific information

If the information is unavailable, clearly say so and offer to connect the user with the Support Team.

4. ADDRESS & LOCATION (CRITICAL)
For queries such as:
- address
- location
- office location
- branch location
- पता
- कहाँ है
- directions

Always provide the exact address exactly as written in the Knowledge Context.

Never:
- guess,
- shorten,
- paraphrase,
- infer nearby landmarks,
- or replace it with a general area (for example, "Karol Bagh").

5. ADDRESS NOT FOUND
If the exact address is not present in the Knowledge Context:
- Clearly state that you do not have the verified address information.
- Do not guess or estimate.
- Offer to connect the user with the Support Team.

6. CONSISTENCY
- Treat the Knowledge Context as the single source of truth for all institute-specific information.
- If there is any conflict between prior conversation and the Knowledge Context, follow the Knowledge Context.
### PHASE 1: GREETING (Updated 2026)

1. GREETING (CRITICAL)
Always begin the conversation with:

> Hi, thanks for calling Expert Institute! How can I help you today?

2. LANGUAGE DETECTION
- Start in English.
- Detect the user's reply.
- If the user continues in English, continue in English.
- If the user replies in Hindi or Hinglish, switch to Hindi Mode according to the language rules.




### PHASE 2: COURSE INFORMATION (Updated 2026)

#### 1. KNOWLEDGE SOURCE (CRITICAL)
- Always use the Knowledge Context for institute-specific information.
- Never invent course details, duration, fees, batches, discounts, placements, or certifications.
- If the requested information is missing from the Knowledge Context, inform the user and offer to connect them with the Support Team.

---

#### 2. PRICE RULE (CRITICAL)
- Do NOT mention fees or pricing unless the user explicitly asks.
- If explaining a course, focus on the course content, Practical training, and learning outcomes.
- After stating the prices of the course, the agent should ask the user weather they want to transfer the call or not to the support team?
---

#### 3. COURSE INTENT

If the user has already mentioned a specific course, do NOT list all available courses.

Instead:
- Acknowledge the selected course.
- Proceed directly with explaining that course.

If the user has NOT specified a course, list the available courses from the Knowledge Context, for example:

> We offer Mobile Repairing Course, iPhone Repairing Course, Laptop Repairing Course, MacBook Repairing Course, CCTV Camera Training, LED, LCD & Smart TV Repairing Course, and AC PCB Repairing Course.

Then ask:

> Which course are you interested in?

Wait for the user's response before continuing.

---

#### 4. BEFORE EXPLAINING THE COURSE

Once the course has been selected, politely say:

> Please stay with me for a moment. I'll explain the course step by step.

---

#### 5. COURSE EXPLANATION (MICRO STEPS)

Keep the explanation conversational.

Explain one step at a time.

**Step 1 – Overview**
- Give a short overview.
- Example:
  > This is a complete training from zero to advanced chip-level.

**Step 2 – Outcome**
- Explain what the learner will be able to do after completing the course.
- Mention no more than TWO key outcomes unless the user asks for more.

**Step 3 – Core Skills**
- Mention one or two important skills or brands covered.
- Example:
  - Samsung
  - Apple
  - Xiaomi

Then explain:

> Basic training includes electronic fundamentals, component identification, soldering, and desoldering.

**Step 4 – Practical Training**
- Explain that learners receive hands-on Practical training using real devices or projects (if supported by the Knowledge Context).

**Step 5 – Advanced Learning**
- Explain advanced topics such as:
  - Chip-level Repairing
  - Software
  - Hardware

---

#### 6. ENGAGEMENT

After every two or three short responses, ask:

> Would you like to know more?

Wait for the user's response before continuing.

---

#### 7. STYLE

- Keep responses short.
- Avoid paragraphs.
- Avoid reading like a brochure.
- Sound conversational and professional.

---

### PHASE 3: FREE DEMO CLASS BOOKING (Updated 2026)

#### 1. OFFER A FREE DEMO CLASS
When appropriate, invite the user to attend a Free Demo Class.
If the user declines, respond naturally and continue the conversation without applying pressure.

English:
> The Free Demo Class helps you understand our teaching style and how we can help you achieve your learning goals.

Hindi/Hinglish:
> Free Demo Class से आपको हमारा teaching style और practical learning process समझने में help मिलेगी।

Avoid pressuring the user.

---

#### 2. CHECK AVAILABLE SLOTS
When the user agrees to book a Free Demo Class:

Call:
> list_available_slots

Present the available slots by mentioning only:
- Day
- Date
- Time

Never mention:
- Slot IDs
- UUIDs
- Hashes
- Any internal or technical identifiers

Ask the user to choose their preferred slot.

---

#### 3. NAME COLLECTION & CONFIRMATION (CRITICAL)

**DO NOT ASK FOR THE USER'S NAME (CRITICAL):** Do not ask for the user's name at any earlier point in the conversation. The name must only be requested after the user has agreed to book a Free Demo Class and has selected a slot, as part of the booking flow below.

After the user selects a slot, check whether their name has already been confirmed.

- If the user's name is already confirmed earlier in the conversation, do not ask for it again.
- If the name has not yet been provided or confirmed, politely ask for it before proceeding.
- Never assume, infer, or guess the user's name.

Once the user provides their name:
- Repeat the name.
- Spell it letter by letter using the English alphabet.
- Ask the user to confirm the spelling.

Example:
> Thank you. I heard your name as Rahul.
> Let me confirm the spelling:
> R - A - H - U - L.
> Is that correct?

If the user corrects the spelling:
- Repeat the corrected spelling.
- Ask for confirmation once more.
- Proceed only after the user confirms it.

If the user explicitly refuses to share their name or asks to continue without providing it, respect their preference and continue with the booking process without repeatedly asking.

---

#### 4. PHONE NUMBER COLLECTION
After the user's name has been confirmed (or they have chosen not to provide one), collect their phone number.

Accept phone numbers spoken as:
- Individual digits
- Number words
- "Double"
- "Triple"
- Any combination of these

Convert the spoken representation into digits internally.

Examples:
- nine one double eight double eight seven three double zero
→ 9188887300
- nine eight seven six five four three two one zero
→ 9876543210

If any part of the number is unclear, politely ask the user to repeat only the unclear portion.

---

#### 5. PHONE NUMBER CONFIRMATION (CRITICAL)
Always confirm the phone number before booking.

Read the phone number one digit at a time in English.

Example:
> Let me confirm your number:
> Nine one eight eight eight eight seven three zero zero.
> Is that correct?

Proceed only after the user confirms.

---

#### 6. BOOK THE FREE DEMO CLASS
Once the following are confirmed:
- Selected slot
- Phone number
- Name (if provided)

Call:
> schedule_demo_class(selected_slot, phone_number, name)

Use the internally stored selected_slot. Never reveal the slot ID or any technical identifier to the user.

After a successful booking, confirm naturally.

Example:
> Great! Your Free Demo Class has been booked successfully. We look forward to meeting you.

---

#### Booking Flow (Mandatory Order)
1. Offer the Free Demo Class.
2. If the user agrees, call `list_available_slots`.
3. Display only the day, date, and time.
4. User selects a slot.
5. Collect and confirm the user's name (unless already confirmed or the user declines to share it) — only at this point, not earlier.
6. Collect the phone number.
7. Confirm the phone number digit by digit.
8. Call `schedule_demo_class(selected_slot, phone_number, name)`.
9. Confirm the booking.

### PHASE 4: PRICING (Updated 2026)

#### 1. PRICE DISCLOSURE (CRITICAL)
- Mention pricing only when the user explicitly asks about fees or price.
- Always use the pricing provided in the Knowledge Context.
- Never guess, estimate, or invent prices.

---

#### 2. PRICE FORMAT

When pricing is available, present it in this order:

1. Original Price
2. Discounted Price (if applicable)
3. Applicable Discount

Example:

> The original fee is ₹50,000.
> With the current offer, you pay ₹30,000.
> That's a 40% discount.

---

#### 3. DISCOUNT POLICY

If supported by the Knowledge Context:

- One Course → 40% Discount
- Two Courses → 50% Discount

Do not mention discounts that are not present in the Knowledge Context.

---

#### 4. EXTRA DISCOUNT REQUEST

If the user requests:
- additional discount,
- special price,
- negotiation,
- manager approval,
- custom offer,

politely explain that only the Support Team can assist.

Example (English):

> I'm sorry, but only our Support Team can assist with additional discount requests. Would you like me to connect you with them?

Example (Hindi/Hinglish):

> Sorry, additional Discount के लिए सिर्फ हमारी Support Team help कर सकती है। क्या मैं आपको उनसे connect कर दूँ?

Never negotiate or promise any additional discount yourself.

---

#### 5. NEVER ACT AS THE SUPPORT TEAM

- Do not approve discounts.
- Do not promise future offers.
- Do not make pricing exceptions.
- Always refer discount negotiations to the Support Team.

---

### COURSE SELECTION STATE (CRITICAL)

Before responding, determine whether the user has already selected a course.

Case 1:
If the user has already mentioned a specific course anywhere earlier in the conversation:
- Store it as the Selected Course.
- Continue discussing ONLY that course.
- Do not list all available courses.
- Do not ask which course they are interested in.

Case 2:
If the user has NOT mentioned any course:
- List the available courses.
- Ask which course they are interested in.

### FEW-SHOT EXAMPLE (ENGLISH)
Scenario 1: User has not mentioned any course

Agent:
Hi, thanks for calling Expert Institute! How can I help you today?

Customer:
What courses do you have?

Agent:
We offer Mobile Repairing Course, iPhone Repairing Course, Laptop Repairing Course, MacBook Repairing Course, CCTV Camera Training, LED, LCD & Smart TV Repairing Course, and AC PCB Repairing Course.
Which course are you interested in?

Customer:
I want to know about the iPhone Repairing Course.

Agent:
Sure. Please stay with me for a moment while I explain.
This is a complete training from zero to advanced chip-level.
You'll learn Practical Repairing techniques and troubleshooting for iPhone devices.
Basic training includes electronic fundamentals, component identification, soldering, and desoldering.
Would you like to know more?

Customer:
What is the price of the iPhone Repairing Course?

Agent:
The original fee is ₹40,000.
With the current offer, the fee is ₹24,000, which includes a 40% discount.
Would you like me to help you book a Free Demo Class?

Customer:
Yes, I'd like to book a Free Demo Class.

Agent:
Great! Let me check the available slots for you.
[Agent calls list_available_slots]
We have these slots available:
- Monday, 28th July, 10:00 AM
- Tuesday, 29th July, 2:00 PM
- Wednesday, 30th July, 11:00 AM
Which slot works best for you?

Customer:
Tuesday, 29th July, 2:00 PM works for me.

Agent:
Perfect! Before I book the slot, may I have your name, please?

Customer:
Raj Gupta.

Agent:
Thank you, Raj. Let me confirm the spelling:
R-A-J G-U-P-T-A.
Is that correct?

Customer:
Yes.

Agent:
Great! Now, may I have your phone number for the booking?

Customer:
Nine one eight eight eight eight seven three double zero.

Agent:
Let me confirm your number:
Nine one eight eight eight eight seven three zero zero.
Is that correct?

Customer:
Yes.

Agent:
Great! Your Free Demo Class has been booked successfully. We look forward to meeting you. 

Scenario 2: User already mentions a course (your problem case)

User:
I want to know about the Mobile Repairing Course.

↓

Agent:
Sure. Please stay with me for a moment while I explain.
This is a complete training from zero to advanced chip-level.
You'll learn Practical Repairing techniques and troubleshooting for Mobile devices.
Basic training includes electronic fundamentals, component identification, soldering, and desoldering.
Would you like to know more?

Customer:
Yes, please tell me more.

Agent:
Advanced training covers chip-level Repairing, software troubleshooting, and hardware diagnostics.
We also provide practical training on real devices.
Would you like to book a Free Demo Class to see our teaching style firsthand?

Customer:
Yes, I'd like to book a Free Demo Class.

Agent:
Great! Let me check the available slots for you.
[Agent calls list_available_slots]
We have these slots available:
- Monday, 28th July, 10:00 AM
- Tuesday, 29th July, 2:00 PM
- Wednesday, 30th July, 11:00 AM
Which slot works best for you?

Customer:
Wednesday, 30th July, 11:00 AM.

Agent:
Perfect! Before I book the slot, may I have your name, please?

Customer:
Rahul.

Agent:
Thank you, Rahul. Let me confirm the spelling:
R-A-H-U-L.
Is that correct?

Customer:
Yes.

Agent:
Great! Now, may I have your phone number for the booking?

Customer:
Nine eight seven six five four three two one zero.

Agent:
Let me confirm your number:
Nine eight seven six five four three two one zero.
Is that correct?

Customer:
Yes.

Agent:
Great! Your Free Demo Class has been booked successfully. We look forward to meeting you.

### VOICE RULES

- Speak naturally.
- Pause briefly after every one or two sentences.
- Never sound like you're reading a script.
- Never repeat the same sentence.
- Keep responses under 20–25 seconds whenever possible.
- Ask only one question at a time.

### MEMORY RULES

Remember only during the current conversation:

- User Name
- Selected Course
- Preferred Language
- Demo Booking Status
- Selected Slot
- Phone Number (only after confirmation)

Do not ask again unless the user changes it.

### INTERRUPTION HANDLING

If the user interrupts while you're explaining:

- Stop immediately.
- Answer the user's new question first.
- Do not continue from where you stopped unless the user asks.

Never say:
"As I was saying..."

Instead say:
"Sure."
"Absolutely."
"Let me answer that first."

### PHASE 6: CALL CLOSING (Updated 2026)

If the user's query is resolved:

English:
"Thank you for contacting Expert Institute. Have a wonderful day."

Hindi/Hinglish:
"Expert Institute से contact करने के लिए thank you। आपका दिन शुभ रहे।"

If a Demo Class is booked:

"Thank you. We look forward to meeting you in the Free Demo Class."

Do not add unnecessary sales pitches.

### PHASE 5: OBJECTION HANDLING (Updated 2026)

If the user is unsure about joining, respond naturally.

COMMON OBJECTIONS

1. "I need some time."
English:
"Sure, take your time. Before you decide, would you like to attend a Free Demo Class? It will help you understand our teaching style."

Hindi/Hinglish:
"बिल्कुल। आप आराम से decide कीजिये। अगर आप चाहें तो एक Free Demo Class attend कर सकते हैं। इससे आपको हमारा teaching style समझने में help मिलेगी।"

2. "I need to talk to my family."

English:
"I completely understand. A Free Demo Class can also help your family understand the Course before making a decision."

Hindi/Hinglish:
"बिल्कुल समझ सकती हूँ। Free Demo Class के बाद decision लेना और भी easy हो जाता है।"

3. "It's expensive."

Do NOT negotiate.

Politely explain the current discount.

If the user requests a better price, connect them with the Support Team."""


async def fetch_agent_config_from_supabase():
    """Fetch system_prompt, opening_greeting, and knowledge_base from Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.info("Supabase not configured, using default config")
        return {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "knowledge_texts": [],
        }

    try:
        from supabase import create_client

        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

        config = {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "outbound_system_prompt": "",  # Empty by default to trigger fallback
            "outbound_opening_greeting": "",
            "knowledge_texts": [],
        }

        try:
            response = supabase.table("agent_config").select("key", "value").execute()
            if response.data:
                for item in response.data:
                    key = item.get("key")
                    value = item.get("value")
                    if key in config and value:
                        config[key] = value
        except Exception as e:
            logger.error(f"Error fetching config from agent_config table: {e}")

        knowledge_response = (
            supabase.table("knowledge_base").select("content").execute()
        )
        if knowledge_response.data:
            config["knowledge_texts"] = [
                item["content"]
                for item in knowledge_response.data
                if item.get("content")
            ]

        logger.info("Successfully fetched config from Supabase")
        return config
    except Exception as e:
        logger.error(f"Error fetching config from Supabase: {e}")
        return {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "knowledge_texts": [],
        }


class ExpertInstituteAgent(Agent):
    LANGUAGE_CONFIG = {
        "hi": {"lang": "hi-IN", "speaker": "roopa", "pace": 1.05},
        "en": {"lang": "en-IN", "speaker": "roopa", "pace": 1.05},
    }

    def __init__(
        self,
        fnc_ctx=None,
        rag_engine=None,
        on_user_activity: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._current_lang: Optional[str] = None
        self._fnc_ctx = fnc_ctx
        self._rag_engine = rag_engine
        self._on_user_activity = on_user_activity

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """
        Called before the LLM generates a response.
        Retrieves relevant knowledge chunks for the latest user message
        and injects them (or a fallback directive) into the context.
        """
        if not self._rag_engine:
            return

        # Extract the latest user text
        user_text = ""
        content = getattr(new_message, "content", "")
        if isinstance(content, list):
            user_text = " ".join([str(c) for c in content if isinstance(c, str)])
        else:
            user_text = str(content).strip()

        if not user_text or len(user_text) < 8:
            return

        result = await self._rag_engine.retrieve(user_text)

        if result.found:
            # Inject retrieved knowledge as a system message at the start
            turn_ctx.items.insert(
                1,
                llm.ChatMessage(role="system", content=[result.context]),
            )
        else:
            # Inject fallback directive ONLY for question-like turns
            question_signals = [
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
            lower_text = user_text.lower()
            is_question = any(sig in lower_text for sig in question_signals)
            if is_question:
                turn_ctx.items.insert(
                    1,
                    llm.ChatMessage(
                        role="system",
                        content=[
                            "[NO KNOWLEDGE FOUND] The user's question is outside your knowledge base. Tell them you don't have that information and offer to transfer the call to the support team."
                        ],
                    ),
                )

    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: any
    ) -> AsyncIterable[stt.SpeechEvent]:
        logger.info("STT node started processing audio...")
        default_stt = super().stt_node(audio, model_settings)
        try:
            async for event in default_stt:
                # ONLY process language detection and deterministic triggers on FINAL transcripts
                # This prevents rapid re-triggering and reduces CPU/network load
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    if event.alternatives and event.alternatives[0].text:
                        text = event.alternatives[0].text.lower()
                        if self._on_user_activity:
                            self._on_user_activity(text)

                        # 1. Language Locking (First Interaction)
                        if self._current_lang is None:
                            # Determine language from text or STT metadata
                            detected_lang = None
                            if (
                                "hindi" in text
                                or "hi" == text
                                or "हिंदी" in text
                                or "hinglish" in text
                            ):
                                detected_lang = "hi"
                            elif "english" in text or "en" == text or "अंग्रेजी" in text:
                                detected_lang = "en"
                            elif event.alternatives[0].language:
                                detected_lang = event.alternatives[0].language.split(
                                    "-"
                                )[0]

                            if detected_lang:
                                config = self.LANGUAGE_CONFIG.get(
                                    detected_lang, self.LANGUAGE_CONFIG["hi"]
                                )
                                self._current_lang = str(config["lang"])
                                logger.info(
                                    f"Language LOCKED to {config['lang']} based on transcript/metadata: '{text}'"
                                )

                                self.session.tts.update_options(
                                    target_language_code=str(config["lang"]),
                                    model="bulbul:v3",
                                    speaker=str(config["speaker"]),
                                    pace=float(config["pace"]),
                                    temperature=0.6,
                                    output_audio_bitrate="64k",
                                    min_buffer_size=150,
                                    max_chunk_length=150,
                                )

                        # 2. Deterministic Discount/Transfer Trigger
                        if "discount" in text or "reduce price" in text:
                            logger.info(
                                f"!!! Deterministic transfer triggered: {text} !!!"
                            )
                            try:
                                if hasattr(self, "session") and self.session:
                                    self.session.say(
                                        "I'll transfer your call to our support team.",
                                        allow_interruptions=False,
                                    )
                                asyncio.create_task(self._fnc_ctx.transfer_call())
                            except Exception as e:
                                logger.error(
                                    f"Error during deterministic transfer: {e}"
                                )

                yield event
        except Exception as e:
            logger.error(f"Error in stt_node: {e}")
            raise


async def generate_call_summary(chat_messages, user_phone=None, booking_info=None):
    """
    Summarizes the call transcript using Groq and returns a formatted string.
    """
    # Extract conversation into text format
    transcript = []
    for msg in chat_messages:
        if getattr(msg, "role", "") in ["user", "assistant"]:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content = " ".join([str(c) for c in content if isinstance(c, str)])
            elif not isinstance(content, str):
                continue
            transcript.append(f"{msg.role.capitalize()}: {content}")

    if not transcript:
        logger.info("Empty transcript, not generating summary.")
        return None

    transcript_text = "\n".join(transcript)
    summary = "Call wrapped up."

    from datetime import datetime

    current_time = datetime.now().strftime("%I:%M %p, %d %b %Y")

    async with aiohttp.ClientSession() as session:
        # Use Groq API to generate a structured summary
        if GROQ_API_KEY:
            try:
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
                payload = {
                    "model": "llama-3.1-8b-instant",  # Using the faster/supported model
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a specialized call summarizer. Your goal is to extract key details from the transcript.\n"
                                "Output strictly in this format exactly:\n"
                                "AI AGENT\n\n"
                                "name: [Extract name of the customer if mentioned, otherwise 'Unknown']\n"
                                "number: [The phone number provided]\n\n"
                                "course: [The course name user is interested in, or 'None']\n\n"
                                "summary: [A professional 1-2 sentence summary]\n\n"
                                f"call time: {current_time}\n\n"
                                "Do not include any other text, labels, or markdown formatting."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Phone Number: {user_phone or 'Unknown'}\nTranscript:\n{transcript_text}",
                        },
                    ],
                }
                async with session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    resp_data = await resp.json()
                    if "choices" in resp_data and len(resp_data["choices"]) > 0:
                        summary = resp_data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"Failed to generate summary: {e}")
                summary = f"name: Unknown\nnumber: {user_phone or 'Unknown'}\nsummary: Could not generate summary."

        # Append booking details if booking was made
        if booking_info and booking_info.get("booked"):
            summary += "\n\nBOOKING DETAILS:\n"
            summary += f"date: {booking_info.get('date', 'N/A')}\n"
            summary += f"time: {booking_info.get('time', 'N/A')}\n"
            summary += f"name: {booking_info.get('name', 'Unknown')}\n"
            summary += f"phone: {booking_info.get('phone', 'Unknown')}"

        return summary


# AssistantTools replaced by TransferFunctions (see transfer_functions.py)


def _normalize_phone_e164(phone_str):
    """
    Normalize an Indian phone number to E.164-like format without '+'.
    Handles: '+91XXXXXXXXXX', '0XXXXXXXXXX' (trunk prefix), '91XXXXXXXXXX',
             'XXXXXXXXXX', and outbound SIP identities with UUID suffix (e.g. '918652153375_a3f2').
    Returns 12-digit string (91 + 10-digit mobile) or the best-effort cleaned string.
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


async def send_ziper_whatsapp(phone_number, message_text):
    """
    Ziper.io WhatsApp API integration using standard URL parameters.
    """
    if not phone_number:
        return

    clean_phone = _normalize_phone_e164(phone_number)

    logger.info(f"Preparing to send Ziper.io WhatsApp message to {clean_phone}...")

    access_token = os.getenv("ZIPER_ACCESS_TOKEN") or os.getenv("ZIPER_API_TOKEN")
    instance_id = os.getenv("ZIPER_INSTANCE_ID")

    if not access_token or not instance_id:
        logger.error(
            "Ziper.io credentials missing! Please check that ZIPER_ACCESS_TOKEN and ZIPER_INSTANCE_ID are correctly set in your .env file."
        )
        return

    try:
        url = "https://ziper.io/api/send.php"
        params = {
            "access_token": access_token,
            "instance_id": instance_id,
            "type": "text",
            "number": clean_phone,
            "message": message_text,
        }

        async with aiohttp.ClientSession() as session:
            # Use GET request to pass URL parameters automatically safely encoded
            async with session.get(url, params=params, ssl=False) as response:
                if response.status in [200, 201]:
                    logger.info(f"Successfully sent Ziper.io WhatsApp to {clean_phone}")
                else:
                    resp_text = await response.text()
                    logger.error(f"Ziper.io API failed: {resp_text}")
    except Exception as e:
        logger.error(f"Error calling Ziper.io: {e}")


async def send_wabridge_whatsapp(phone_number, template_id=None):
    """
    WABridge WhatsApp API integration to send template messages.
    Uses GET request with query params matching the WABridge createmessage endpoint.
    """
    if not phone_number:
        return

    clean_phone = _normalize_phone_e164(phone_number)
    actual_template = template_id or WABRIDGE_TEMPLATE_ID

    logger.info(
        f"Sending WABridge template to {clean_phone} | template={actual_template} | media_type={WABRIDGE_MEDIA_TYPE}"
    )

    if not all([WABRIDGE_AUTH_KEY, WABRIDGE_APP_KEY, WABRIDGE_DEVICE_ID, WABRIDGE_TEMPLATE_ID]):
        logger.error("WABridge credentials or Template ID missing! Please check your .env file.")
        return

    try:
        url = "https://web.wabridge.com/api/createmessage"
        params = {
            "auth-key": WABRIDGE_AUTH_KEY,
            "app-key": WABRIDGE_APP_KEY,
            "device_id": WABRIDGE_DEVICE_ID,
            "destination_number": clean_phone,
            "template_id": actual_template,
            "variables": "[]",
            "media_type": WABRIDGE_MEDIA_TYPE,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, ssl=False) as response:
                resp_text = await response.text()
                logger.info(f"WABridge HTTP {response.status} | Raw: {resp_text}")

                try:
                    resp_json = json.loads(resp_text)
                    api_status = resp_json.get("status")
                    api_message = resp_json.get("message")
                    message_id = resp_json.get("data", {}).get("messageid")
                    from_number = resp_json.get("data", {}).get("from")
                    to_number = resp_json.get("data", {}).get("to")

                    logger.info(
                        f"WABridge response: status={api_status} message={api_message} "
                        f"messageid={message_id} from={from_number} to={to_number}"
                    )

                    if api_status and not message_id:
                        logger.warning(
                            "WABridge returned status=true but no messageid — delivery may fail"
                        )

                    if not api_status:
                        logger.error(f"WABridge API reported failure: {api_message}")

                except json.JSONDecodeError:
                    logger.error(f"WABridge returned non-JSON response: {resp_text[:200]}")

                if response.status not in [200, 201]:
                    logger.error(f"WABridge HTTP error {response.status}")

    except Exception as e:
        logger.error(f"Error calling WABridge: {e}")


def prewarm(proc: JobProcess):
    print("DEBUG: PREWARM STARTED")
    try:
        proc.userdata["vad"] = silero.VAD.load(
            activation_threshold=0.5,
            min_speech_duration=0.25,
            min_silence_duration=0.3,
            prefix_padding_duration=0.3,
        )
        print("DEBUG: SILERO VAD LOADED SUCCESSFULLY (Optimized for SIP)")
    except Exception as e:
        print(f"DEBUG: SILERO VAD LOAD FAILED: {e}")

    # RAG is now loaded in entrypoint for loop stability.
    pass


async def entrypoint(ctx: JobContext):
    print(f"!!! CRITICAL: JOB ASSIGNED TO WORKER !!! Room: {ctx.room.name}")
    print(f"DEBUG: ENTRYPOINT STARTED for room {ctx.room.name}")
    try:
        logger.info(f"Connecting to room {ctx.room.name}")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        print("DEBUG: CONNECTED TO ROOM SUCCESS")

        call_start_time = datetime.datetime.now()

        # --- Safe RAG Initialization (Inside Entrypoint) ---
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key and "rag" not in ctx.proc.userdata:
            try:
                rag = RAGEngine(openai_api_key=openai_api_key)

                # Automatically find all local TXT and PDF knowledge files
                base_dir = os.path.dirname(__file__)
                kb_files = [
                    os.path.join(base_dir, f)
                    for f in os.listdir(base_dir)
                    if f.endswith((".txt", ".pdf")) and f != "requirements.txt"
                ]
                logger.debug(f"RAG: Detected {len(kb_files)} knowledge files: {kb_files}")

                if kb_files:
                    try:
                        await rag.load_knowledge(kb_files)
                        logger.info(f"RAG: Indexed {len(kb_files)} local files (TXT/PDF).")
                    except Exception as e:
                        logger.error(f"RAG: Failed to load knowledge files: {type(e).__name__}: {e}")

                # Check for Google Sheet URL (Safely)
                if sheet_url := os.getenv("GOOGLE_SHEET_URL"):
                    try:
                        await rag.load_knowledge_from_sheet(sheet_url)
                    except Exception as e:
                        logger.error(
                            f"RAG: Failed to load Google Sheet ({e}), continuing without it."
                        )

                ctx.proc.userdata["rag"] = rag
                print(
                    "DEBUG: UNIVERSAL RAG ENGINE LOADED SUCCESSFULLY (PDF + TXT + SHEETS)"
                )
            except Exception as e:
                logger.error(f"RAG: Initialization failed: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"DEBUG: CONNECTION FAILED: {e}")
        return

    agent_config = await fetch_agent_config_from_supabase()

    # --- Metadata & Persona Selection ---
    recipient_name = "Student"
    target_course = "our technical programs"

    # Safely handle metadata (especially for Inbound calls where it might be empty)
    if ctx.job.metadata and ctx.job.metadata.strip():
        try:
            meta = json.loads(ctx.job.metadata)
            recipient_name = meta.get("recipientName", recipient_name)
            target_course = meta.get("targetCourse", target_course)
            
            # Normalize and handle fallback for generic/missing courses
            generic_vals = ["our technical programs", "our training programs", "technical programs", "training programs", "none", "unknown"]
            if not target_course or target_course.lower().strip() in generic_vals:
                target_course = "our mobile repairing course"
            else:
                # Add "course" suffix for specific courses if it doesn't already have it
                if "course" not in target_course.lower() and "program" not in target_course.lower():
                    target_course = f"our {target_course} course"
                else:
                    target_course = f"our {target_course}"
            
            logger.info(
                f"Metadata detected: Calling {recipient_name} for {target_course}"
            )
        except Exception as e:
            logger.warning(f"Metadata provided but failed to parse: {e}")
            logger.debug(f"Raw metadata was: '{ctx.job.metadata}'")

    # Detect if we have a generic name (to avoid saying "Hi Student")
    is_generic_name = recipient_name.lower().strip() in ["student", "unknown", "none", "prospect", ""]

    # Detect call direction (Inbound vs Outbound)
    # Outbound calls are explicitly dispatched by vobiz_outbound.py with "outbound" in room name
    # Inbound calls (SIP users calling the system) have room names like "+91865..." or contain "sip"
    room_name = ctx.room.name.lower()
    is_outbound = "outbound" in room_name

    if is_outbound:
        logger.info("OUTBOUND call detected. Using refined outbound persona.")

        # 1. System Prompt Fallback Logic
        db_outbound_prompt = agent_config.get("outbound_system_prompt")
        if db_outbound_prompt and db_outbound_prompt.strip():
            logger.info("Using custom OUTBOUND system prompt from Supabase.")
            system_prompt = db_outbound_prompt
        else:
            logger.info(
                "No custom outbound prompt found (or empty). Falling back to hardcoded OUTBOUND_SYSTEM_PROMPT."
            )
            system_prompt = OUTBOUND_SYSTEM_PROMPT

        # Inject dynamic details into prompt
        system_prompt = system_prompt.replace("[Name]", recipient_name)
        system_prompt = system_prompt.replace("[Course]", target_course)
        
        if is_generic_name:
            system_prompt += "\n\nCRITICAL: You have already introduced yourself and mentioned the course interest in the initial greeting. DO NOT repeat your introduction. Respond naturally to the user's answer and proceed to course details (Phase 2)."
        
        system_prompt += f"\n\nCURRENT CONTEXT:\nYou are calling {recipient_name} specifically about the {target_course} course they inquired about."

        system_prompt += (
            "\n\nBOOKING VERIFICATION RULE (CRITICAL): NEVER confirm, promise, or commit to any "
            "specific booking date or time with the user until you have called `list_available_slots` "
            "and that exact day/time appears in its returned list. If the user requests a specific "
            "time, call `list_available_slots` first, then tell them whether that exact time is "
            "available, and only proceed after showing the actual available options. Never invent or "
            "assume a slot is available, and never tell the user a slot is unavailable without seeing "
            "it absent from the `list_available_slots` output."
        )

        # 2. Greeting Fallback Logic
        db_outbound_greeting = agent_config.get("outbound_opening_greeting")
        if db_outbound_greeting and db_outbound_greeting.strip():
            logger.info("Using custom OUTBOUND greeting from Supabase.")
            greeting_text = db_outbound_greeting.replace("[Name]", recipient_name).replace("[Course]", target_course)
        else:
            logger.info("No custom outbound greeting found. Using Smart Greeting logic.")
            if is_generic_name:
                greeting_text = f"Hello! I'm Neha calling from Expert Institute, New Delhi. I'm calling because we received an inquiry regarding {target_course}. Is this a good time to speak?"
            else:
                greeting_text = f"Hi, am I speaking with {recipient_name}?"

        print(f"DEBUG: OUTBOUND GREETING SELECTED: '{greeting_text}'")
    else:
        logger.info("INBOUND call detected. Using standard configuration.")

        system_prompt = agent_config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        greeting_text = agent_config.get("opening_greeting", DEFAULT_GREETING)
        print(f"DEBUG: INBOUND GREETING SELECTED: '{greeting_text}'")

    knowledge_texts = agent_config["knowledge_texts"]

    rag_engine: Optional[RAGEngine] = ctx.proc.userdata.get("rag")
    if knowledge_texts and rag_engine:
        try:
            await rag_engine.load_knowledge_from_text(knowledge_texts)
            logger.info(
                f"RAG engine loaded with {len(knowledge_texts)} knowledge sources from Supabase"
            )
        except Exception as e:
            logger.error(f"Failed to load knowledge from Supabase: {e}")
    elif rag_engine:
        logger.info("RAG engine loaded from prewarm userdata (local files).")
    else:
        has_openai_key = bool(os.getenv("OPENAI_API_KEY"))
        has_rag_in_userdata = "rag" in ctx.proc.userdata
        logger.warning(
            f"RAG engine not available — knowledge retrieval disabled. "
            f"openai_key_present={has_openai_key}, rag_in_userdata={has_rag_in_userdata}"
        )

    # Initial Chat Context - this defines the persona and system rules
    initial_ctx = llm.ChatContext()
    initial_ctx.add_message(role="system", content=system_prompt)

    # Calendar Initialization
    timezone = "Asia/Kolkata"
    tz_info = None
    try:
        from zoneinfo import ZoneInfo

        tz_info = ZoneInfo(timezone)
    except Exception as e:
        logger.warning(
            f"ZoneInfo database missing or error ({e}). Using hardcoded Indian Offset (+5:30)."
        )
        tz_info = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

    cal_event_id = os.getenv("CAL_EVENT_ID")
    if cal_event_id:
        try:
            cal_event_id = int(cal_event_id)
        except ValueError:
            cal_event_id = None

    if cal_api_key := os.getenv("CAL_API_KEY", None):
        logger.info(
            f"CAL_API_KEY detected, using cal.com calendar (event_id: {cal_event_id})"
        )
        cal = CalComCalendar(
            api_key=cal_api_key, timezone=timezone, event_id=cal_event_id
        )
    else:
        logger.warning("CAL_API_KEY is not set. Falling back to FakeCalendar")
        cal = FakeCalendar(timezone=timezone)
    await cal.initialize()

    _slots_map = {}
    _slots_normalized = {}
    booking_info = {"booked": False, "name": "", "phone": "", "date": "", "time": ""}

    @llm.function_tool(
        description="Get available appointment slots for demo classes. Returns slots grouped by day with available times. Use this to check availability."
    )
    async def list_available_slots():
        now = datetime.datetime.now(tz_info)
        range_days = 30
        slots = await cal.list_available_slots(
            start_time=now, end_time=now + datetime.timedelta(days=range_days)
        )

        if not slots:
            return "No slots available at the moment."

        # Group slots by date
        from collections import defaultdict
        day_map = defaultdict(list)
        for slot in slots:
            local = slot.start_time.astimezone(tz_info)
            day_key = local.strftime("%A %d %B %Y")
            time_str = local.strftime("%I:%M %p")
            day_map[day_key].append((time_str, slot))

        lines = []
        for day, times in sorted(day_map.items()):
            time_list = ", ".join(t for t, _ in sorted(times))
            lines.append(f"{day}: {time_list}")
            for time_str, slot in times:
                key = f"{day} at {time_str}"
                _slots_map[key] = slot
                _slots_normalized[_normalize_slot_key(key)] = slot

        return "\n".join(lines)

    @llm.function_tool(
        description="Schedule a demo class appointment. Call this after the user agrees and provides their name and phone number. Use the exact date and time string from list_available_slots."
    )
    async def schedule_demo_class(
        selected_slot: str,
        phone_number: str,
        name: str,
    ):
        slot = _slots_map.get(selected_slot)
        if not slot:
            slot = _slots_normalized.get(_normalize_slot_key(selected_slot))
        if not slot:
            return (
                f"Error: Slot '{selected_slot}' was not found among the available slots. "
                "This does not mean the slot is unavailable. Call list_available_slots to get the "
                "current available slots, then ask the user to choose from the exact options listed, "
                "and pass the chosen option to schedule_demo_class exactly as returned."
            )

        try:
            result = await cal.schedule_appointment(
                start_time=slot.start_time,
                attendee_name=name,
                phone_number=phone_number,
            )
            if result.startswith("Error"):
                return result
        except Exception as e:
            logger.error(f"Unexpected error in schedule_demo_class: {e}")
            return f"Error: An unexpected error occurred while booking. ({str(e)})"

        local = slot.start_time.astimezone(tz_info)
        now = datetime.datetime.now(tz_info)

        # Track booking information for summary
        booking_info["booked"] = True
        booking_info["name"] = name
        booking_info["phone"] = phone_number
        booking_info["date"] = _format_date_human(local, now)
        booking_info["time"] = local.strftime("%I:%M %p")

        return f"Success: The appointment was scheduled for {_format_date_human(local, now)}."

    # Component Initialization for Demo
    # Using gpt-oss-120b for enhanced capabilities.
    llm_node = groq.LLM(
        model="openai/gpt-oss-120b", temperature=0.1
    )
    """llm_node = openai.LLM(model="gpt-5.4-nano", temperature=0.1)"""
    # Using Sarvam Saaras v3 for high-quality localized STT with auto-detection
    stt_node = sarvam.STT(
        model="saaras:v3",
        language="unknown",
        mode="codemix",
    )

    tts_node = sarvam.TTS(
        target_language_code="en-IN",  # Initialized for English greeting
        model="bulbul:v3",
        speaker="roopa",
        pace=1.05,
        speech_sample_rate=22050,
        temperature=0.6,
        output_audio_bitrate="64k",
        min_buffer_size=150,
        max_chunk_length=150,
    )

    logger.info(
        f"Initializing Demo Agent | LLM: {llm_node.model} | STT: {stt_node.model} (Auto) | TTS: {tts_node.model} (Premium)"
    )

    # Extract user phone from room name if possible (SIP format: +9174..._suffix)
    user_phone = "Unknown"
    room_name = ctx.room.name
    if room_name.startswith("+"):
        user_phone = room_name.split("_")[0].replace("+", "")
    elif "_" in room_name:
        user_phone = room_name.split("_")[0]

    # Initialize tools (using the new TransferFunctions framework)
    fnc_ctx = TransferFunctions(ctx, user_phone)

    # --- Autocut state: end call after 60s of user inactivity ---
    AUTOCUT_TIMEOUT = 60  # seconds of inactivity before ending the call

    last_user_speech_time = [time.monotonic()]
    autocut_triggered = [False]
    agent_is_speaking = [False]
    user_is_speaking = [False]
    user_requested_hangup = [False]
    summary_sent = [False]

    def reset_autocut_timer(reason: str = "activity"):
        last_user_speech_time[0] = time.monotonic()
        logger.info(f"AUTOCUT: timer reset due to {reason}.")

    def is_closing_assistant_message(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        return any(
            closing_line in normalized
            for closing_line in (
                "thank you for calling expert institute. goodbye",
                "expert institute call करने के लिए धन्यवाद. goodbye",
            )
        )

    def is_soft_closing_assistant_message(text: str) -> bool:
        normalized = text.lower()
        return "goodbye" in normalized or "good bye" in normalized or "bye" in normalized

    async def hang_up_call(reason: str):
        if autocut_triggered[0]:
            return

        autocut_triggered[0] = True
        logger.info(f"AUTOCUT: {reason}. Ending call.")
        try:
            await session.aclose()
        except Exception as e:
            logger.warning(f"Could not close agent session during hangup: {e}")

        livekit_url = os.getenv("LIVEKIT_URL")
        livekit_key = os.getenv("LIVEKIT_API_KEY")
        livekit_secret = os.getenv("LIVEKIT_API_SECRET")
        if not all([livekit_url, livekit_key, livekit_secret]):
            return

        lkapi = api.LiveKitAPI(livekit_url, livekit_key, livekit_secret)
        try:
            await lkapi.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        except Exception as e:
            logger.warning(f"Could not delete LiveKit room during hangup: {e}")
        finally:
            await lkapi.aclose()

    async def close_after_assistant_closing():
        await asyncio.sleep(0.5)
        if ctx.room.isconnected():
            await hang_up_call("assistant closing detected")

    # Define the Agent
    agent = ExpertInstituteAgent(
        instructions=initial_ctx.messages()[0].text_content,
        llm=llm_node,
        stt=stt_node,
        tts=tts_node,
        fnc_ctx=fnc_ctx,
        rag_engine=rag_engine,
        on_user_activity=lambda text: reset_autocut_timer("final STT transcript"),
    )

    # Create the session
    # min_endpointing_delay (0.4) standard safe value so sentences aren't cut in half
    # min_interruption_duration (0.3) allows user to interrupt the agent much easier
    # preemptive_generation (False): Retained for reliable RAG delivery.
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=stt_node,
        llm=llm_node,
        tts=tts_node,
        tools=fnc_ctx.flatten() + [list_available_slots, schedule_demo_class],
        turn_handling={
            "endpointing": {"min_delay": 0.6},
            "interruption": {"min_duration": 0.5},
            "preemptive_generation": {"enabled": False},
        },
    )

    # Removed duplicate deterministic intent code and event handlers
    # since it's now embedded directly inside the STT generation loop.

    # Log LLM text to see if it's hallucinating tool calls as text
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        from livekit.agents.llm.chat_context import ChatMessage
        if isinstance(event.item, ChatMessage) and event.item.role == "assistant":
            transcript = " ".join(
                c for c in event.item.content if isinstance(c, str)
            )
            print(f"\n🤖 AGENT: {transcript}\n")
            logger.info(f"Agent (LLM) says: {transcript}")
            msg_count = len(session.history.messages())
            logger.info(f"DEBUG: History after agent response: {msg_count} messages")
            agent_is_speaking[0] = False
            reset_autocut_timer("assistant response finished")
            
            # 1. Primary: Exact phrase detection
            if is_closing_assistant_message(transcript):
                logger.info("AUTOCUT: Exact assistant closing phrase detected.")
                asyncio.create_task(close_after_assistant_closing())
            
            # 2. Fallback: User said 'bye' and agent responded with a soft closing
            elif user_requested_hangup[0] and is_soft_closing_assistant_message(transcript):
                logger.info("AUTOCUT: User requested hangup + soft assistant closing detected.")
                asyncio.create_task(close_after_assistant_closing())

    @session.on("agent_state_changed")
    def on_agent_state_changed(event: AgentStateChangedEvent):
        if event.new_state == "speaking":
            agent_is_speaking[0] = True
            logger.info("Agent STARTED speaking (Audio bits flowing)...")
        elif event.new_state == "idle":
            agent_is_speaking[0] = False
            logger.info("Agent STOPPED speaking.")
            reset_autocut_timer("agent became idle")

    @session.on("user_state_changed")
    def on_user_state_changed(event: UserStateChangedEvent):
        if event.new_state == "speaking":
            user_is_speaking[0] = True
            reset_autocut_timer("user started speaking")
            logger.info("!!! INTERRUPTION: User started speaking (interrupting agent)...")
            msg_count = len(session.history.messages())
            logger.info(f"DEBUG: History at interruption time: {msg_count} messages")
        elif event.new_state in ("listening", "idle"):
            user_is_speaking[0] = False

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:
            print(f"\n👤 USER: {event.transcript}\n")
            logger.info(f"User (STT) said: {event.transcript}")
            user_is_speaking[0] = False
            if re.search(r"\b(bye|goodbye|good bye|bas|bas itna hi|nahi|that is all|that's all)\b", event.transcript.lower()):
                user_requested_hangup[0] = True
                logger.info("AUTOCUT: user hangup intent detected.")
            reset_autocut_timer("user_input_transcribed event")

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info(
                f"Successfully SUBSCRIBED to audio track from {participant.identity}"
            )

    # Wait for the first participant to join
    print("DEBUG: WAITING FOR USER TO ANSWER...")
    participant_identity = None
    try:
        # For outbound calls, the participant joins the room only when the phone is answered
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=30)
        participant_identity = participant.identity
        logger.info(f"User answered! Identity: {participant_identity}")
        print(f"DEBUG: USER ANSWERED: {participant_identity}")
    except asyncio.TimeoutError:
        print("DEBUG: TIMEOUT WAITING FOR ANSWER - Starting session anyway")
        logger.warning("No answer detected within 30s.")

    await session.start(agent, room=ctx.room)
    print("DEBUG: SESSION STARTED. PREPARING GREETING...")

    if not greeting_text or greeting_text == DEFAULT_GREETING:
        greeting_text = DEFAULT_GREETING

    logger.info(f"Using greeting: {greeting_text}")

    try:
        if is_outbound:
            # SIP calls need a moment for the audio bridge to clear after answering
            print("DEBUG: OUTBOUND - Waiting 2.0s for audio bridge to stabilize...")
            await asyncio.sleep(2.0)

        session.say(greeting_text, allow_interruptions=True)
        print(f"DEBUG: GREETING SENT: '{greeting_text}'")
    except (RuntimeError, Exception) as e:
        logger.warning(f"Could not send initial greeting: {e}")
        print(f"DEBUG: GREETING FAILED: {e}")

    # Start autocut countdown from after greeting is sent
    reset_autocut_timer("greeting sent")

    # When the participant disconnects, trigger the summary flow
    async def send_summary():
        if summary_sent[0]:
            return

        call_end_time = datetime.datetime.now()
        duration_seconds = int((call_end_time - call_start_time).total_seconds())
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        call_duration = f"{minutes}m {seconds}s"

        # Extract phone number from LiveKit Participant Identity (format: "sip_+9174...")
        user_phone = None
        if participant_identity and "sip_" in participant_identity:
            user_phone = _normalize_phone_e164(
                participant_identity.replace("sip_", "")
            )

        # 1. Generate Custom Summary (Logging ONLY, Telegram REMOVED)
        admin_summary_text = await generate_call_summary(
            session.history.messages(), user_phone, booking_info
        )

        if not admin_summary_text:
            admin_summary_text = "Call completed but summary could not be generated."

        # 2. Save call log to Supabase
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                from supabase import create_client

                sb = create_client(SUPABASE_URL, SUPABASE_KEY)

                customer_name = booking_info.get("name", "")
                if not customer_name and admin_summary_text:
                    match = re.search(
                        r"^name:\s*(.+)$", admin_summary_text, re.MULTILINE
                    )
                    if match:
                        customer_name = match.group(1).strip()

                sb.table("call_logs").insert(
                    {
                        "phone_number": user_phone or "",
                        "customer_name": customer_name,
                        "summary": admin_summary_text,
                        "duration": call_duration,
                        "status": "booked"
                        if booking_info.get("booked")
                        else "completed",
                        "metadata": {"room_name": ctx.room.name},
                    }
                ).execute()
                logger.info("Call log saved to Supabase")
            except Exception as e:
                logger.error(f"Error saving call log to Supabase: {e}")

        admin_phone = os.getenv("DEFAULT_TRANSFER_NUMBER")

        # 3. Ziper.io: Send Summary to Admin
        await send_ziper_whatsapp(admin_phone, admin_summary_text)

        # 3. WhatsApp Follow-ups (WABridge only for caller)
        if user_phone:
            # Send WABridge Video Template
            await send_wabridge_whatsapp(user_phone)

        summary_sent[0] = True

    # We wrap the shutdown callback to ensure it doesn't block forever
    # and handles its own errors gracefully.
    async def safe_shutdown():
        try:
            await send_summary()
        except Exception as e:
            logger.error(f"Error during shutdown summary: {e}")

    ctx.add_shutdown_callback(safe_shutdown)

    # Keep the entrypoint alive while the room is connected to prevent early job exit.
    # Also runs autocut monitor to end call after prolonged user inactivity.
    logger.info("Greeting phase finished. Entrypoint persistence active.")

    async def autocut_monitor():
        """End the call after 60s of user inactivity."""
        while ctx.room.isconnected():
            await asyncio.sleep(5)

            if autocut_triggered[0]:
                continue

            if agent_is_speaking[0] or user_is_speaking[0]:
                continue

            idle_seconds = time.monotonic() - last_user_speech_time[0]
            if idle_seconds >= AUTOCUT_TIMEOUT:
                logger.info(
                    f"AUTOCUT: {int(idle_seconds)}s user inactivity. Ending call."
                )
                try:
                    handle = session.say(
                        "It seems the line has gone quiet. Goodbye!",
                        allow_interruptions=False,
                    )
                    await handle.wait_for_playout()
                except Exception:
                    pass
                await hang_up_call("inactivity timeout")

    try:
        await autocut_monitor()
    except Exception as e:
        logger.error(f"Error in autocut monitor: {e}")
    finally:
        logger.info("Room disconnected or autocut triggered - Entrypoint exiting.")


if __name__ == "__main__":
    required_env = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    missing_env = [name for name in required_env if not os.getenv(name)]
    if missing_env:
        missing_text = ", ".join(missing_env)
        raise RuntimeError(
            "Missing required LiveKit environment variables: "
            f"{missing_text}. Set them in your deployment environment before starting the agent."
        )

    cli.run_app(
        WorkerOptions(
            agent_name="outbound_caller",
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
