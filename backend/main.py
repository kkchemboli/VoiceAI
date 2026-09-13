import os
import logging
import time
from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Optional
import datetime
import asyncio
from services.calendar_api import Calendar, FakeCalendar, CalComCalendar
from services.celery_worker import app as celery_app
from services.supabase_client import execute_query, get_supabase_client
from services.runtime_config import get_sheet_url, set_sheet_url
from utils.paths import InvalidFilenameError, safe_knowledge_path
from core.config import settings
from core.observability import (
    configure_logging,
    configure_otel,
    metrics,
    new_correlation_id,
    observe_request,
    request_id_context,
    service_context,
    start_span,
    trace_id_context,
)
import sys
import json

configure_logging("api")
configure_otel("api")
logger = logging.getLogger("voice-backend")

# Load environment variables
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize services
    app.state.startup_complete = False
    logger.info("Initializing backend services...")
    try:
        if hasattr(calendar_service, 'initialize'):
            await calendar_service.initialize()
        app.state.startup_complete = True
        logger.info("Services initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        
    yield
    
    # Shutdown: Clean up resources
    logger.info("Shutting down backend services...")
    # Add any explicit cleanup for database connections or sessions here
    logger.info("Shutdown complete.")

app = FastAPI(title="Voice Agent API", lifespan=lifespan)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or new_correlation_id()
    trace_id = request.headers.get("X-Trace-ID") or request_id
    request_token = request_id_context.set(request_id)
    trace_token = trace_id_context.set(trace_id)
    service_token = service_context.set("api")
    started = time.perf_counter()
    response = None
    try:
        with start_span(
            "http.request",
            {"http.request.method": request.method, "url.path": request.url.path},
        ) as span:
            response = await call_next(request)
            if span is not None:
                span.set_attribute("http.response.status_code", response.status_code)
        return response
    except Exception:
        metrics.increment("http_requests_5xx_total")
        raise
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        if response is not None:
            observe_request(request.method, request.url.path, response.status_code, duration_ms)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace_id
        request_id_context.reset(request_token)
        trace_id_context.reset(trace_token)
        service_context.reset(service_token)


@app.get("/health/live")
async def liveness_check():
    """Confirm that the API process is running and able to serve requests."""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness_check():
    """Report whether startup dependencies have completed initialization."""
    startup_complete = getattr(app.state, "startup_complete", False)
    payload = {
        "status": "ready" if startup_complete else "not_ready",
        "dependencies": {
            "calendar": "initialized" if startup_complete else "initializing",
            "supabase": "configured" if SUPABASE_URL and SUPABASE_KEY else "optional_or_unconfigured",
        },
    }
    if not startup_complete:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/metrics")
async def metrics_check():
    """Return process-local counters for the deployment metrics collector."""
    return {"service": "api", "metrics": metrics.snapshot()}

# Enable CORS for frontend development
cors_origins = list(settings.cors_allowed_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase Setup
SUPABASE_URL = settings.supabase_url
SUPABASE_KEY = settings.supabase_key

supabase = get_supabase_client()
if supabase:
    logger.info("Connected to Supabase")
else:
    logger.info("Supabase not configured or unavailable")

# Calendar Service Setup
timezone = settings.timezone
cal_api_key = settings.cal_api_key

from prompts import (
    DEFAULT_GREETING,
    DEFAULT_SYSTEM_PROMPT,
    OUTBOUND_SYSTEM_PROMPT,
)

DEFAULT_PROMPT = DEFAULT_SYSTEM_PROMPT
DEFAULT_OUTBOUND_GREETING = "Hi, am I speaking with [Name]?"
DEFAULT_OUTBOUND_PROMPT = OUTBOUND_SYSTEM_PROMPT


cal_api_key = settings.cal_api_key
cal_event_id = settings.cal_event_id

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


@app.post("/api/outbound/call")
async def trigger_outbound_call(request: CallRequest):
    """Persist and enqueue a single outbound call."""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase is required for queued outbound calls")
    try:
        response = await execute_query(
            supabase.table("outbound_calls").insert({
                "phone_number": request.phone_number,
                "status": "pending",
            })
        )
        call_record_id = response.data[0]["id"]
        task = celery_app.send_task(
            "services.celery_worker.process_outbound_call",
            args=[call_record_id],
        )
        return {"success": True, "call_id": call_record_id, "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/outbound/batch")
async def trigger_batch_calls(request: BatchCallRequest):
    """Persist and enqueue multiple outbound calls."""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase is required for queued outbound calls")
    try:
        task_ids = []
        for phone in request.phone_numbers:
            response = await execute_query(
                supabase.table("outbound_calls").insert({
                    "phone_number": phone,
                    "status": "pending",
                })
            )
            call_record_id = response.data[0]["id"]
            task = celery_app.send_task(
                "services.celery_worker.process_outbound_call",
                args=[call_record_id],
            )
            task_ids.append(task.id)
        return {"success": True, "task_ids": task_ids, "count": len(task_ids)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/outbound/queue")
async def get_outbound_queue():
    """Get the status of outbound calls"""
    if supabase:
        try:
            response = await execute_query(
                supabase.table("outbound_calls")
                .select("*")
                .order("created_at", desc=True)
                .limit(20)
            )
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
            logger.exception("Error fetching outbound queue from Supabase")
            return []

    # Fallback when Supabase is not available
    return []


@app.post("/api/outbound/bulk-dial")
async def trigger_bulk_dialer():
    """Enqueue the campaign lead import, which inserts leads and dispatches per-call dial tasks."""
    if not get_sheet_url():
        raise HTTPException(
            status_code=400,
            detail="GOOGLE_SHEET_URL is not configured. Set it from the admin panel or provide GOOGLE_SHEET_URL in the .env file.",
        )
    task = celery_app.send_task("services.celery_worker.import_campaign_leads")
    return {"success": True, "task_id": task.id, "message": "Campaign lead import enqueued in background."}


@app.post("/api/config/sheet-url")
async def update_sheet_url(update: SheetUrlUpdate):
    """Save the GOOGLE_SHEET_URL to runtime config (never mutates .env)."""
    url = update.url.strip()
    if not url.startswith("https://docs.google.com/spreadsheets/"):
        raise HTTPException(
            status_code=400,
            detail="Only Google Sheets URLs are allowed (https://docs.google.com/spreadsheets/...).",
        )

    set_sheet_url(url)
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
            
    sheet_url = get_sheet_url()
    return {
        "files": files,
        "sheet_url": sheet_url,
        "total_files": len(files)
    }


@app.post("/api/knowledge/upload")
async def upload_knowledge_file(file: UploadFile = File(...)):
    """Upload a PDF or TXT knowledge file to the backend directory"""
    try:
        file_path = safe_knowledge_path(
            os.path.dirname(__file__), file.filename or ""
        )
    except InvalidFilenameError:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    if not file_path.endswith((".pdf", ".txt")):
        raise HTTPException(status_code=400, detail="Only .pdf and .txt files are allowed.")

    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": f"Uploaded {os.path.basename(file_path)} successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")


@app.delete("/api/knowledge/file/{filename}")
async def delete_knowledge_file(filename: str):
    """Delete a specific knowledge file from the backend directory"""
    try:
        file_path = safe_knowledge_path(os.path.dirname(__file__), filename)
    except InvalidFilenameError:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return {"status": "success", "message": f"Deleted {os.path.basename(file_path)}"}
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
        response = await execute_query(
            supabase.table("agent_config").select("key", "value")
        )

        if response.data:
            for item in response.data:
                key = item.get("key")
                value = item.get("value")
                if key in config:
                    config[key] = value

        return config
    except Exception as e:
        logger.exception("Error fetching config")
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
            await execute_query(supabase.table("agent_config").upsert(item))

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
        response = await execute_query(
            supabase.table("call_logs")
            .select("*", count="exact")
            .order("created_at", desc=True)
            .range(start, end)
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
        logger.exception("Error fetching logs")
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
        response = await execute_query(
            supabase.table("knowledge_base")
            .select("content")
            .eq("language", lang)
        )
        if response.data and len(response.data) > 0:
            return {"content": response.data[0]["content"]}
        
        # If not in Supabase, try local as a secondary fallback
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return {"content": f.read()}
                
        return {"content": ""}
    except Exception as e:
        logger.exception("Error fetching knowledge")
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
        await execute_query(
            supabase.table("knowledge_base").upsert(
                {"language": lang, "content": data.content}, on_conflict="language"
            )
        )
        
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
