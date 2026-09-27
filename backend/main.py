import os
import logging
import time
from fastapi import FastAPI, HTTPException, Body, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List, Optional
import datetime
import asyncio
from services.calendar_api import Calendar, FakeCalendar, CalComCalendar
from services.celery_worker import app as celery_app
from services.supabase_client import execute_query, get_supabase_client
from services.runtime_config import get_sheet_url, set_sheet_url
from utils.distributed_lock import BULK_IMPORT_LOCK, is_lock_held, redis_client
from core.config import settings
from core.observability import (
    celery_task_headers,
    configure_logging,
    configure_otel,
    current_otel_context,
    init_sentry,
    metrics,
    new_correlation_id,
    observe_request,
    request_id_context,
    service_context,
    set_correlation,
    start_span,
)
import sys
import json
from dotenv import load_dotenv

load_dotenv()

configure_logging("api")
configure_otel("api")
init_sentry("api")
logger = logging.getLogger("voice-backend")

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
    request_token = request_id_context.set(request_id)
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
            # Low-cardinality metric label: the matched FastAPI route template
            # (e.g. "/api/knowledge/{filename}") instead of the raw request path,
            # which would otherwise explode label cardinality with per-request IDs.
            route = getattr(request.scope.get("route"), "path", None)
            observe_request(request.method, route or request.url.path, response.status_code, duration_ms)
            response.headers["X-Request-ID"] = request_id
            trace_id, _ = current_otel_context()
            response.headers["X-Trace-ID"] = trace_id if trace_id != "-" else request_id
        request_id_context.reset(request_token)
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
        set_correlation(call_id=str(call_record_id))
        task = celery_app.send_task(
            "services.celery_worker.process_outbound_call",
            args=[call_record_id],
            headers=celery_task_headers(call_id=str(call_record_id)),
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
            set_correlation(call_id=str(call_record_id))
            task = celery_app.send_task(
                "services.celery_worker.process_outbound_call",
                args=[call_record_id],
                headers=celery_task_headers(call_id=str(call_record_id)),
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
    if not await get_sheet_url():
        raise HTTPException(
            status_code=400,
            detail="GOOGLE_SHEET_URL is not configured. Set it from the admin panel or provide GOOGLE_SHEET_URL in the .env file.",
        )
    if is_lock_held(redis_client, BULK_IMPORT_LOCK):
        raise HTTPException(status_code=409, detail="A bulk import is already running.")
    task = celery_app.send_task(
        "services.celery_worker.import_campaign_leads",
        headers=celery_task_headers(),
    )
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

    await set_sheet_url(url)
    return {"status": "success", "message": "Google Sheet URL updated and saved."}


@app.get("/api/knowledge/status")
async def get_knowledge_status():
    """Report what the agent will actually retrieve from.

    This used to list .txt/.pdf files in the backend directory. Those are gone,
    so it always returned an empty list while looking like a working feature.
    It now reports the Supabase rows that are the real source of truth.

    sheet_url stays in this response because outboundService.getSheetUrl() reads
    it from here. The Sheet is the outbound call list, not knowledge, so that
    coupling is wrong -- but moving it is a frontend contract change, not dead
    code removal.
    """
    if not supabase:
        raise HTTPException(
            status_code=503, detail="Knowledge store unavailable (Supabase not configured)"
        )

    # `language` and `content` only. `updated_at` was verified to exist and be
    # populated on 2026-09-27, so it is not selected for being unknown, but for
    # being unowned: nothing here creates or migrates this table, and PostgREST
    # fails the whole query on one unknown column, so depending on a column this
    # repo does not own would turn a table recreation into a broken endpoint.
    sources = []
    try:
        response = await execute_query(
            supabase.table("knowledge_base").select("language, content")
        )
        for row in response.data or []:
            content = row.get("content") or ""
            if not content.strip():
                continue
            sources.append(
                {
                    "language": row.get("language") or "en",
                    "characters": len(content),
                    "source": "supabase",
                }
            )
    except Exception as e:
        logger.exception("Error reading knowledge sources")
        return {
            "sources": [],
            "source_of_truth": "supabase.knowledge_base",
            "total_sources": 0,
            "sheet_url": await get_sheet_url(),
            "error": str(e),
        }

    return {
        "sources": sources,
        "source_of_truth": "supabase.knowledge_base",
        "total_sources": len(sources),
        "sheet_url": await get_sheet_url(),
    }


    # Removed: POST /api/knowledge/upload and DELETE /api/knowledge/file/{name}.
    # Both existed only to manage local TXT/PDF files, and knowledge now lives
    # exclusively in Supabase (see GET/POST /api/knowledge below), so they had
    # no reader left. Uploads reported success while writing to an ephemeral
    # container filesystem nothing consulted.
    #
    # The delete endpoint was worse than dead: it accepted any bare filename in
    # the backend directory with no extension allowlist, and the API is proxied
    # unauthenticated through frontend/nginx.conf on a published port. So
    # DELETE /api/knowledge/file/main.py removed the API's own entrypoint, and
    # with restart: unless-stopped the container could not come back. Removed
    # rather than patched, since the capability it provided is gone.


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
    """Fetch knowledge base content.

    Supabase is the only knowledge store. There is no local file mirror: it used
    to be written on every save and read as a fallback, which meant the agent
    could be serving a stale on-disk copy instead of what the admin just saved.
    """
    if not supabase:
        raise HTTPException(
            status_code=503, detail="Knowledge store unavailable (Supabase not configured)"
        )

    try:
        response = await execute_query(
            supabase.table("knowledge_base")
            .select("content")
            .eq("language", lang)
        )
        if response.data and len(response.data) > 0:
            return {"content": response.data[0]["content"]}
        return {"content": ""}
    except Exception as e:
        logger.exception("Error fetching knowledge")
        return {"content": "", "error": str(e)}


@app.post("/api/knowledge/{lang}")
async def update_knowledge(lang: str, data: KnowledgeUpdate):
    """Update knowledge base content in Supabase.

    Takes effect for the next call: the agent reconciles its index against
    Supabase on entry, so no restart or cache purge is needed.
    """
    if not supabase:
        raise HTTPException(
            status_code=503, detail="Knowledge store unavailable (Supabase not configured)"
        )

    try:
        await execute_query(
            supabase.table("knowledge_base").upsert(
                {"language": lang, "content": data.content}, on_conflict="language"
            )
        )
        return {
            "status": "success",
            "message": f"Knowledge base ({lang}) updated; live on the next call.",
        }
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
