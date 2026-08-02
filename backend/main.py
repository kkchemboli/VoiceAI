import os
import logging
from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from supabase import create_client, Client
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Optional
import datetime
import asyncio
from calendar_api import Calendar, FakeCalendar, CalComCalendar
from vobiz_outbound import make_outbound_call
import sys
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("voice-backend")

# Load environment variables
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize services
    logger.info("Initializing backend services...")
    try:
        if hasattr(calendar_service, 'initialize'):
            await calendar_service.initialize()
        logger.info("Services initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        
    yield
    
    # Shutdown: Clean up resources
    logger.info("Shutting down backend services...")
    # Add any explicit cleanup for database connections or sessions here
    logger.info("Shutdown complete.")

app = FastAPI(title="Voice Agent API", lifespan=lifespan)

# Enable CORS for frontend development
cors_origins = os.getenv(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase Setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("Connected to Supabase")
    except Exception as e:
        print(f"Failed to connect to Supabase: {e}")

# Calendar Service Setup
timezone = "Asia/Kolkata"
cal_api_key = os.getenv("CAL_API_KEY")

DEFAULT_GREETING = (
    "Hi, thanks for calling Expert Institute! How can I help you today?"
)

DEFAULT_PROMPT = """### ROLE & PERSONALITY (Updated 2026)

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
Would you like to know more, or would you like to attend a Free Demo Class to see our teaching style firsthand?

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
Would you like to know more, or would you like to attend a Free Demo Class to see our teaching style firsthand?

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

### SPOKEN OUTPUT (CRITICAL)

- NEVER output stage directions, internal thoughts, or parenthetical actions (e.g., (Wait for confirmation), (Waiting for user's phone number)).
- The text you generate is sent directly to a Text-to-Speech engine and spoken aloud.
- ONLY output the exact words you intend to speak to the user.

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

DEFAULT_OUTBOUND_GREETING = "Hi, am I speaking with [Name]?"

DEFAULT_OUTBOUND_PROMPT = """### ROLE & PERSONALITY
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
   "Hi! I'm Neha calling from Expert Institute, New Delhi. I'm calling because you recently showed interest in our technical training programs. Is this a good time to speak?"
3. If they are busy: "No problem! When would be a better time to call you back?"
4. If they are free: Proceed to list the courses.

### PHASE 2: COURSE LISTING & INTEREST CHECK
1. INTRODUCE COURSES:
   "Great! As you might know, we offer specialized courses in Mobile Repairing, iPhone, Laptop, MacBook, CCTV, LED TV, and AC PCB repairing."
2. CHECK INTEREST:
   "Was there a specific course you were thinking about starting?"
3. Refer to [KNOWLEDGE CONTEXT] for any specific course details or benefits.

### PHASE 3: THE HOOK (FREE DEMO CLASS)
1. After providing initial info, PUSH for the demo:
   "Since you've already inquired, I'd highly recommend booking a FREE demo class. It's the best way to see our practical labs and teaching style. Should I check the available slots for you?"

### CONVERSATIONAL CONSTRAINTS
- No paragraphs. Keep it light and interactive.
- Use back-channeling ('hmm', 'right').

### SPOKEN OUTPUT (CRITICAL)
- NEVER output stage directions, internal thoughts, or parenthetical actions (e.g., (Wait for confirmation), (Waiting for user's phone number)).
- The text you generate is sent directly to a Text-to-Speech engine and spoken aloud.
- ONLY output the exact words you intend to speak to the user.
"""

cal_api_key = os.getenv("CAL_API_KEY")
cal_event_id = os.getenv("CAL_EVENT_ID")
if cal_event_id:
    try:
        cal_event_id = int(cal_event_id)
    except ValueError:
        cal_event_id = None

if cal_api_key:
    calendar_service = CalComCalendar(
        api_key=cal_api_key, timezone=timezone, event_id=cal_event_id
    )
else:
    calendar_service = FakeCalendar(timezone=timezone)


# Models and Schemas


class ConfigUpdate(BaseModel):
    system_prompt: Optional[str] = None
    opening_greeting: Optional[str] = None
    outbound_system_prompt: Optional[str] = None
    outbound_opening_greeting: Optional[str] = None


class KnowledgeUpdate(BaseModel):
    content: str


class CallRequest(BaseModel):
    phone_number: str


class BatchCallRequest(BaseModel):
    phone_numbers: List[str]

class SheetUrlUpdate(BaseModel):
    url: str


async def managed_outbound_call(phone: str, call_id: Optional[str] = None):
    """
    Initiates a call and updates the status in Supabase if available.
    """
    status = "success"
    error = None
    
    try:
        await make_outbound_call(phone)
    except Exception as e:
        status = "failed"
        error = str(e)
        print(f"Call failed for {phone}: {e}")

    if supabase and call_id:
        try:
            supabase.table("outbound_calls").update({
                "status": status,
                "error_message": error
            }).eq("id", call_id).execute()
        except Exception as e:
            print(f"Failed to update outbound status in Supabase: {e}")

# In-memory fallback queue (only used if Supabase is disconnected)
outbound_queue = []


@app.post("/api/outbound/call")
async def trigger_outbound_call(request: CallRequest):
    """Trigger a single outbound call"""
    call_record_id = None
    try:
        if supabase:
            # Create persistent record in Supabase
            response = supabase.table("outbound_calls").insert({
                "phone_number": request.phone_number,
                "status": "calling"
            }).execute()
            if response.data:
                call_record_id = response.data[0]["id"]
        else:
            # Fallback to in-memory queue
            call_id = len(outbound_queue) + 1
            outbound_queue.append({
                "id": str(call_id),
                "phone": request.phone_number,
                "status": "calling",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
            })
        
        # Initiate call in background
        asyncio.create_task(managed_outbound_call(request.phone_number, call_record_id))
        
        return {"success": True, "message": f"Call to {request.phone_number} initiated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/outbound/batch")
async def trigger_batch_calls(request: BatchCallRequest):
    """Trigger multiple outbound calls"""
    try:
        for phone in request.phone_numbers:
            call_record_id = None
            if supabase:
                # Create persistent record
                response = supabase.table("outbound_calls").insert({
                    "phone_number": phone,
                    "status": "pending"
                }).execute()
                if response.data:
                    call_record_id = response.data[0]["id"]
            else:
                # Fallback to in-memory
                call_id = len(outbound_queue) + 1
                outbound_queue.append({
                    "id": str(call_id),
                    "phone": phone,
                    "status": "pending",
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
                })

            # Throttle and trigger
            async def delayed_call(p, rid):
                await asyncio.sleep(2)
                await managed_outbound_call(p, rid)
            
            asyncio.create_task(delayed_call(phone, call_record_id))

        return {"success": True, "message": f"Started {len(request.phone_numbers)} calls"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/outbound/queue")
async def get_outbound_queue():
    """Get the status of outbound calls"""
    if supabase:
        try:
            response = supabase.table("outbound_calls").select("*").order("created_at", desc=True).limit(20).execute()
            transformed = []
            for item in response.data:
                transformed.append({
                    "id": str(item["id"]),
                    "phone": item["phone_number"],
                    "status": item["status"],
                    "timestamp": item["created_at"],
                    "error": item.get("error_message")
                })
            return transformed
        except Exception as e:
            print(f"Error fetching outbound queue from Supabase: {e}")
            return outbound_queue[-20:]

    # Fallback to in-memory queue
    return outbound_queue[-20:]


@app.post("/api/outbound/bulk-dial")
async def trigger_bulk_dialer():
    """Trigger the specialized bulk_dialer.py logic in a background task"""
    from bulk_dialer import run_bulk_dialer
    
    # We run it in the background as it can take a long time (sequential calling)
    asyncio.create_task(run_bulk_dialer())
    return {"success": True, "message": "Campaign queue loaded in background."}


@app.post("/api/config/sheet-url")
async def update_sheet_url(update: SheetUrlUpdate):
    """Save the GOOGLE_SHEET_URL to the .env file"""
    env_path = ".env"
    lines = []
    
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
    
    found = False
    new_line = f"GOOGLE_SHEET_URL={update.url}\n"
    
    for i, line in enumerate(lines):
        if line.startswith("GOOGLE_SHEET_URL="):
            lines[i] = new_line
            found = True
            break
            
    if not found:
        lines.append(f"\n{new_line}")
        
    with open(env_path, "w") as f:
        f.writelines(lines)
        
    # Reload env and notify
    os.environ["GOOGLE_SHEET_URL"] = update.url
    return {"status": "success", "message": "Google Sheet URL updated and saved."}


@app.get("/api/knowledge/status")
async def get_knowledge_status():
    """List detected knowledge sources (PDF, TXT, Sheet)"""
    base_dir = os.path.dirname(__file__)
    files = []
    for f in os.listdir(base_dir):
        if f.endswith((".txt", ".pdf")):
            files.append({
                "name": f,
                "type": "file",
                "format": "pdf" if f.endswith(".pdf") else "txt",
                "size": os.path.getsize(os.path.join(base_dir, f))
            })
            
    sheet_url = os.getenv("GOOGLE_SHEET_URL", "")
    return {
        "files": files,
        "sheet_url": sheet_url,
        "total_files": len(files)
    }


@app.post("/api/knowledge/upload")
async def upload_knowledge_file(file: UploadFile = File(...)):
    """Upload a PDF or TXT knowledge file to the backend directory"""
    if not file.filename.endswith((".pdf", ".txt")):
        raise HTTPException(status_code=400, detail="Only .pdf and .txt files are allowed.")
    
    base_dir = os.path.dirname(__file__)
    file_path = os.path.join(base_dir, file.filename)
    
    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": f"Uploaded {file.filename} successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")


@app.delete("/api/knowledge/file/{filename}")
async def delete_knowledge_file(filename: str):
    """Delete a specific knowledge file from the backend directory"""
    base_dir = os.path.dirname(__file__)
    file_path = os.path.join(base_dir, filename)
    
    if ".." in filename or os.path.isabs(filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")
        
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return {"status": "success", "message": f"Deleted {filename}"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error deleting file: {e}")
    else:
        raise HTTPException(status_code=404, detail="File not found.")




@app.get("/api/config")
async def get_config():
    """Fetch agent configuration (system prompt and opening greeting)"""
    config = {
        "system_prompt": DEFAULT_PROMPT,
        "opening_greeting": DEFAULT_GREETING,
        "outbound_system_prompt": DEFAULT_OUTBOUND_PROMPT,
        "outbound_opening_greeting": DEFAULT_OUTBOUND_GREETING,
    }
    
    if not supabase:
        return config

    try:
        response = supabase.table("agent_config").select("key", "value").execute()

        if response.data:
            for item in response.data:
                key = item.get("key")
                value = item.get("value")
                if key in config:
                    config[key] = value

        return config
    except Exception as e:
        print(f"Error fetching config: {e}")
        config["error"] = str(e)
        return config


@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    """Update agent configuration"""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        updates = []
        if config.system_prompt is not None:
            updates.append({"key": "system_prompt", "value": config.system_prompt})
        if config.opening_greeting is not None:
            updates.append({"key": "opening_greeting", "value": config.opening_greeting})
        if config.outbound_system_prompt is not None:
            updates.append({"key": "outbound_system_prompt", "value": config.outbound_system_prompt})
        if config.outbound_opening_greeting is not None:
            updates.append({"key": "outbound_opening_greeting", "value": config.outbound_opening_greeting})

        for item in updates:
            supabase.table("agent_config").upsert(item).execute()

        return {"status": "success", "message": "Configuration updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs")
async def get_logs(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500)):
    """Fetch call logs with pagination"""
    if not supabase:
        # RETURN MOCK DATA FOR TESTING
        mock = [
            {
                "id": "mock-1",
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "time": "10:30 AM",
                "phone": "+919876543210",
                "customer": "Rahul Sharma",
                "summary": "Interested in MacBook repairing course. Asked about placement support.",
                "duration": "4m 12s",
                "status": "completed",
                "transcript": "User: Hi, I want to know about MacBook repairing. Agent: Sure, we offer basic to advanced chip-level training..."
            },
            {
                "id": "mock-2",
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "time": "02:15 PM",
                "phone": "+918800554433",
                "customer": "Priya Singh",
                "summary": "Booked a demo class for Mobile Repairing. Confirmed for tomorrow.",
                "duration": "6m 45s",
                "status": "completed",
                "transcript": "User: aapke yahan mobile repairing course hai? Agent: Haan bilkul, basic to advanced training milti hai..."
            }
        ]
        return {"data": mock, "total": len(mock)}

    try:
        start = (page - 1) * page_size
        end = start + page_size - 1
        response = (
            supabase.table("call_logs")
            .select("*", count="exact")
            .order("created_at", desc=True)
            .range(start, end)
            .execute()
        )
        transformed = []
        for log in response.data:
            created = datetime.datetime.fromisoformat(
                log["created_at"].replace("Z", "+00:00")
            )
            transformed.append(
                {
                    "id": log["id"],
                    "date": created.strftime("%Y-%m-%d"),
                    "time": created.strftime("%I:%M %p"),
                    "phone": log.get("phone_number", ""),
                    "customer": log.get("customer_name", ""),
                    "summary": log.get("summary", ""),
                    "duration": log.get("duration", ""),
                    "status": log.get("status", "completed"),
                    "transcript": log.get("transcript", ""),
                }
            )
        return {"data": transformed, "total": response.count}
    except Exception as e:
        print(f"Error fetching logs: {e}")
        return {"data": [], "total": 0}


@app.get("/api/appointments")
async def get_appointments(year: int = None, month: int = None):
    """Fetch confirmed appointments from the calendar"""
    logger = logging.getLogger("calendar-api")

    if year is not None and month is not None:
        target_date = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)
    else:
        target_date = datetime.datetime.now(datetime.timezone.utc)

    start_time = target_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_time = (start_time + datetime.timedelta(days=32)).replace(day=1)

    if not cal_api_key:
        # RETURN MOCK APPOINTMENTS IF CAL.COM IS NOT CONFIGURED
        return [
            {
                "start": (datetime.datetime.now() + datetime.timedelta(days=1)).replace(hour=11, minute=0).isoformat() + "Z",
                "attendee": {"name": "Test User 1", "phone": "+919999888877"},
                "seats_booked": 1
            },
            {
                "start": (datetime.datetime.now() + datetime.timedelta(days=2)).replace(hour=14, minute=30).isoformat() + "Z",
                "attendee": {"name": "Test User 2", "phone": "+917766554433"},
                "seats_booked": 1
            }
        ]

    try:
        bookings = await calendar_service.list_bookings(
            start_time=start_time, end_time=end_time
        )
        transformed = []
        for b in bookings:
            attendees = b.get("attendees", [])
            transformed.append(
                {
                    "start": b.get("start"),
                    "attendee": {
                        "name": attendees[0].get("name", "Unknown")
                        if attendees
                        else "Unknown",
                        "phone": attendees[0].get("phoneNumber", "")
                        if attendees
                        else "",
                    },
                    "seats_booked": len(attendees),
                }
            )
        logger.info(f"Fetched {len(transformed)} bookings from Cal.com")
        return transformed
    except Exception as e:
        logger.error(f"Error fetching appointments from Cal.com: {e}")
        return []


@app.get("/api/knowledge")
async def get_knowledge(lang: str = "en"):
    """Fetch knowledge base content"""
    file_map = {"en": "knowledge.txt", "hi": "knowledge_hi.txt"}
    filename = file_map.get(lang, "knowledge.txt")
    path = os.path.join(os.path.dirname(__file__), filename)

    if not supabase:
        # Fallback to local files if supabase is missing
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return {"content": f.read()}
            except Exception as e:
                return {"content": "", "error": f"Error reading local file: {e}"}
        return {"content": f"New knowledge base for {lang}. Start typing to create {filename}."}

    try:
        response = (
            supabase.table("knowledge_base")
            .select("content")
            .eq("language", lang)
            .execute()
        )
        if response.data and len(response.data) > 0:
            return {"content": response.data[0]["content"]}
        
        # If not in Supabase, try local as a secondary fallback
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return {"content": f.read()}
                
        return {"content": ""}
    except Exception as e:
        print(f"Error fetching knowledge: {e}")
        return {"content": "", "error": str(e)}


@app.post("/api/knowledge/{lang}")
async def update_knowledge(lang: str, data: KnowledgeUpdate):
    """Update knowledge base content (Supabase or Local)"""
    file_map = {"en": "knowledge.txt", "hi": "knowledge_hi.txt"}
    filename = file_map.get(lang, "knowledge.txt")
    path = os.path.join(os.path.dirname(__file__), filename)

    if not supabase:
        # Save to local file
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data.content)
            return {"status": "success", "message": f"Knowledge base ({lang}) saved locally to {filename}"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save locally: {e}")

    try:
        # Save to Supabase
        supabase.table("knowledge_base").upsert(
            {"language": lang, "content": data.content}, on_conflict="language"
        ).execute()
        
        # Also sync to local file for agent reliability
        with open(path, "w", encoding="utf-8") as f:
            f.write(data.content)
            
        return {"status": "success", "message": f"Knowledge base ({lang}) updated in cloud and local sync"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# Serve frontend static files
dist_path = os.path.join(os.path.dirname(__file__), "../frontend/dist")
if os.path.exists(dist_path):
    app.mount("/assets", StaticFiles(directory=os.path.join(dist_path, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Prevent intercepting API routes (API routes start with /api)
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
            
        file_path = os.path.join(dist_path, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        
        # Fallback to index.html for SPA routing
        return FileResponse(os.path.join(dist_path, "index.html"))

if __name__ == "__main__":
    import uvicorn

    # Disable reload for maximum stability on Windows during testing
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False
    )
