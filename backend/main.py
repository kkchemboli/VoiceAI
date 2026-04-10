import os
import logging
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from supabase import create_client, Client
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Optional
import datetime
from calendar_api import Calendar, FakeCalendar, CalComCalendar

# Load environment variables
load_dotenv()

app = FastAPI(title="Voice Agent API")

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
    "Hello! Thank you for calling Expert Institute. How can I help you today?"
)

DEFAULT_PROMPT = """### ROLE & PERSONALITY
You are a helpful, natural conversational AI agent for 'Expert Institute of Advance Technologies Pvt. Ltd.', New Delhi.
NEVER disclose that you are an AI Agent or a Machine Learning Model even if your're asked always just say you're an employee of Expert Institute.
GENDER (CRITICAL): FEMALE. Use female Hindi grammar (e.g., 'रही हूँ', 'करती हूँ'). NEVER use male forms.
TONE: Realistic, human-like, engaging. No robotic language.

### LANGUAGE RULES (CRITICAL)
1. START: Always start the call in English (as per PHASE 1).
2. ENGLISH MODE: If the user chooses English, speak ONLY in professional, helpful English. DO NOT use any Hindi or Hinglish words except for the company name.
3. HINDI MODE: If the user chooses Hindi, switch to the HINGLISH & SCRIPT RULES below.
4. If you think the user is speaking any other language, use hindi and switch to the HINGLISH & SCRIPT RULES below.

### HINGLISH & SCRIPT RULES (HINDI MODE ONLY)
1. NO BOOKISH HINDI: Never use 'प्रशिक्षण', 'संस्थान', 'प्रवेश', 'शुल्क', 'अनुभव', 'उपलब्ध'.
2. MODERN HINGLISH:- Speak like a real 20–30 year old Indian customer support agent
Mix Hindi + English naturally
Example: ❌ "आपकी समस्या का समाधान किया जाएगा" ✅ "Main aapki problem solve kar deti hoon"
3. KEYWORDS: Use English for: Mobile, Laptop, CCTV, Repairing, Course, Batch, Practical, FreeDemo Class, Placement, Support, Discount.
4. SCRIPT: Hindi responses MUST be in Devanagari script. No Romanized Hindi.

### CONVERSATIONAL CONSTRAINTS
- No paragraphs. Explain max TWO benefits. Use back-channeling ('hmm', 'right').

### KNOWLEDGE & FALLBACK RULES
- If a [KNOWLEDGE CONTEXT] block is provided before your turn, use ONLY that info to answer.
- If you see [NO KNOWLEDGE FOUND], you MUST say you don't have that information and offer to transfer: 'मुझे इसकी जानकारी नहीं है, but I can transfer you to our support team. Would you like that?' (If Hindi) or 'I am sorry, I don't have that information. I can transfer you to our support team. Would you like that?' (If English).
- NEVER invent fees, dates, or facts not in the knowledge context.

### PHASE 1: GREETING & NAME
1. GREET IN ENGLISH: 'Hi, thanks for calling Expert Institute! how can i help you?'
2. If the user talks in Hindi, ask for name in Hindi. If the user talks in English, ask for name in English.
3. SPELLING CHECK (MANDATORY): Spell name back (e.g., 'Raj, R-A-J. Is that correct?').

### PHASE 2: COURSE INFO
IMPORTANT: DO NOT MENTION PRICE UNTIL USER ASKS FOR IT SPECIFICALLY.
1. ALWAYS refer to the KNOWLEDGE BASE before answering any course-related query.
2. FIRST list ALL available courses (e.g., 'We offer Mobile Repairing Course, iPhone Repairing Course, Laptop Repairing Course, MacBook Repairing Course, CCTV Camera Training, LED, LCD & Smart TV Repairing Course, and AC PCB Repairing Course.').
3. Ask: 'Which course are you interested in?'
4. WAIT for user selection.
5. Once course is selected: ASK CALLER TO BE ATTENTIVE. DO NOT FORGET TO DO THIS
6. Extract key points from Knowledge Base and explain in MICRO STEPS:
   - Step 1 (Overview): What the course is- Say this line 'This course is a complete training from basic to advanced chip-level' and the rest from context.
   - Step 2 (Benefit): What user can do after learning
   - Step 3 (Core skills): 1-2 main things from Knowledge base like brands covered like samsung,apple for mobile etc. Then say basic training will comprise of electronic fundamentals,component identification,soldering and desoldering
   - Step 4 (Practical aspect): Hands-on / real work
   - Step 5 (Advanced highlight): High-value skills (chip level, Software and hardware etc.)
7. ALWAYS break explanation into short conversational chunks.
8. After 2-3 lines, pause and ask:'Would you like to know more?'
9. NEVER read the KB like a paragraph. ALWAYS convert it into natural speech.

### PHASE 3: FREE DEMO Class BOOKING & TOOLS
1. PERSUASION: If they refuse a free demo class, say (in chosen language): 'Demo class will help you understand our teaching style and how we can help you out' or (Hindi) 'मो क्लास आपको हमारा टीचिंग स्टाइल समझने में मदद करेगी और हम आपकी हेल्प कैसे कर सकते हैं, यह भी समझ आएगा।'
2. TOOL 1 (list_available_slots): Call when user agrees.
3. DATA COLLECTION: Ask for phone number after a day is selected.
4. TOOL 2 (schedule_demo_class): Requires slot_id, phone_number, and name.

### PHASE 4: Pricing
IMPORTANT: TELL THE CALLERS THE PRICE & DISCOUNTED PRICE AS WELL.
1.Tell them that is the caller opts for one course they'll get 40% discount and 50% discount if they opt for two courses.
2.If the user asks for further discounts transfer them to the support team.

IMPORTANT: NEVER ACT LIKE THE SUPPORT TEAM ALWAYS TRANSFER WHEN THE SUPPORT TEAM IS NEEDED(FOR ANYTHING NOT IN KNOWLEDGE BASE).
"""

if cal_api_key:
    calendar_service = CalComCalendar(api_key=cal_api_key, timezone=timezone)
else:
    calendar_service = FakeCalendar(timezone=timezone)


@app.on_event("startup")
async def startup_event():
    await calendar_service.initialize()


class ConfigUpdate(BaseModel):
    system_prompt: str
    opening_greeting: Optional[str] = None


class KnowledgeUpdate(BaseModel):
    content: str




@app.get("/api/config")
async def get_config():
    """Fetch agent configuration (system prompt and opening greeting)"""
    if not supabase:
        return {"system_prompt": DEFAULT_PROMPT, "opening_greeting": DEFAULT_GREETING}

    try:
        response = supabase.table("agent_config").select("key", "value").execute()
        config = {"system_prompt": DEFAULT_PROMPT, "opening_greeting": DEFAULT_GREETING}

        if response.data:
            for item in response.data:
                key = item.get("key")
                value = item.get("value")
                if key == "system_prompt":
                    config["system_prompt"] = value
                elif key == "opening_greeting":
                    config["opening_greeting"] = value

        return config
    except Exception as e:
        print(f"Error fetching config: {e}")
        return {
            "system_prompt": DEFAULT_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "error": str(e),
        }


@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    """Update agent configuration"""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        updates = [{"key": "system_prompt", "value": config.system_prompt}]
        if config.opening_greeting is not None:
            updates.append(
                {"key": "opening_greeting", "value": config.opening_greeting}
            )

        for item in updates:
            supabase.table("agent_config").upsert(item).execute()

        return {"status": "success", "message": "Configuration updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs")
async def get_logs():
    """Fetch call logs"""
    if not supabase:
        return []

    try:
        response = (
            supabase.table("call_logs")
            .select("*")
            .order("created_at", desc=True)
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
        return transformed
    except Exception as e:
        print(f"Error fetching logs: {e}")
        return []


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
    if not supabase:
        # Fallback to local files if supabase is missing
        file_map = {"en": "knowledge.txt", "hi": "knowledge_hi.txt"}
        path = os.path.join(
            os.path.dirname(__file__), file_map.get(lang, "knowledge.txt")
        )
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return {"content": f.read()}
        return {"content": "Knowledge base file not found locally."}

    try:
        response = (
            supabase.table("knowledge_base")
            .select("content")
            .eq("language", lang)
            .execute()
        )
        if response.data and len(response.data) > 0:
            return {"content": response.data[0]["content"]}
        return {"content": ""}
    except Exception as e:
        print(f"Error fetching knowledge: {e}")
        return {"content": "", "error": str(e)}


@app.post("/api/knowledge/{lang}")
async def update_knowledge(lang: str, data: KnowledgeUpdate):
    """Update knowledge base content"""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        # Upsert based on language column to avoid unique constraint violations
        supabase.table("knowledge_base").upsert(
            {"language": lang, "content": data.content}, on_conflict="language"
        ).execute()
        return {"status": "success", "message": f"Knowledge base ({lang}) updated"}
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

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
