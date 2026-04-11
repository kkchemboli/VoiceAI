import os
import logging
from fastapi import FastAPI, HTTPException, Body, UploadFile, File
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
- Use Roman script (English letters) for all responses.
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
    return {"success": True, "message": "Bulk dialing campaign started in background."}


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
async def get_logs():
    """Fetch call logs"""
    if not supabase:
        # RETURN MOCK DATA FOR TESTING
        return [
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
