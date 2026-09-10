import asyncio
import os
import datetime
import pytz
import logging
from celery import Celery
from dotenv import load_dotenv

from services.supabase_client import get_supabase_client
from services.vobiz_outbound import make_outbound_call
from core.config import settings
from core.observability import configure_logging, configure_otel, metrics, start_span

load_dotenv()

logger = logging.getLogger("celery-worker")
configure_logging("celery-worker")
configure_otel("celery-worker")

redis_url = settings.redis_url
app = Celery("campaign_worker", broker=redis_url)
app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

app.conf.beat_schedule = {
    'process-campaign-queue-every-minute': {
        'task': 'services.celery_worker.process_campaign_queue',
        'schedule': 60.0,
    },
}
app.conf.timezone = 'UTC'

SUPABASE_URL = settings.supabase_url
SUPABASE_KEY = settings.supabase_key

supabase = get_supabase_client()


@app.task(bind=True)
def process_campaign_queue(self):
    logger.info("Cron triggered: Checking Campaign Queue (task_id=%s)...", self.request.id)
    metrics.increment("celery_tasks_started_total")
    
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.datetime.now(ist)
    
    if now_ist.hour < 8 or now_ist.hour >= 20:
        logger.info(f"[{now_ist.strftime('%H:%M:%S')} IST] Outside of allowed calling window (08:00 - 20:00). Skipping.")
        return
        
    logger.info(f"[{now_ist.strftime('%H:%M:%S')} IST] Inside calling window. Querying pending calls...")
    
    if not supabase:
        logger.error("Supabase client is not initialized. Cannot process queue.")
        return

    try:
        response = supabase.table("outbound_calls").select("*").eq("status", "pending").order("created_at").limit(1).execute()
        
        if not response.data:
            logger.info("No pending calls found in the queue.")
            return
            
        record = response.data[0]
        call_id = record["id"]
        phone = record["phone_number"]
        name = record.get("name", "Student")
        course = record.get("course", "our training programs")
        
        logger.info(f"Picked up call {call_id} for {phone}. Starting call...")

        supabase.table("outbound_calls").update({"status": "calling"}).eq("id", call_id).execute()

        async def run_call():
            await make_outbound_call(phone, name, course, wait_for_completion=True)
            
        asyncio.run(run_call())
        
        supabase.table("outbound_calls").update({"status": "success"}).eq("id", call_id).execute()
        logger.info(f"Successfully finished call {call_id}.")
        
    except Exception as e:
        logger.error(f"Error processing campaign queue: {e}")
        if 'call_id' in locals():
            try:
                supabase.table("outbound_calls").update({"status": "failed", "error_message": str(e)}).eq("id", call_id).execute()
            except Exception as update_err:
                logger.error(f"Failed to update status to 'failed': {update_err}")


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def process_outbound_call(self, call_id: str):
    """Claim and execute one durable outbound call exactly once per status transition."""
    if not supabase:
        raise RuntimeError("Supabase client is not initialized")

    response = supabase.table("outbound_calls").select("*").eq("id", call_id).limit(1).execute()
    if not response.data:
        logger.warning("Outbound call record not found (call_id=%s)", call_id)
        return {"status": "missing", "call_id": call_id}

    record = response.data[0]
    if record.get("status") != "pending":
        logger.info(
            "Skipping outbound call that is no longer pending (call_id=%s, status=%s)",
            call_id,
            record.get("status"),
        )
        return {"status": "skipped", "call_id": call_id}

    claimed = (
        supabase.table("outbound_calls")
        .update({"status": "calling"})
        .eq("id", call_id)
        .eq("status", "pending")
        .execute()
    )
    if not claimed.data:
        logger.info("Outbound call was claimed by another worker (call_id=%s)", call_id)
        return {"status": "skipped", "call_id": call_id}

    try:
        with start_span(
            "celery.process_outbound_call",
            {"celery.task_id": self.request.id, "call.id": call_id},
        ) as span:
            asyncio.run(
                make_outbound_call(
                    record["phone_number"],
                    record.get("name", "Student"),
                    record.get("course", "our training programs"),
                    wait_for_completion=True,
                )
            )
            if span is not None:
                span.set_attribute("celery.task.status", "success")
        supabase.table("outbound_calls").update({"status": "success"}).eq("id", call_id).execute()
        logger.info("Outbound call completed (call_id=%s, task_id=%s)", call_id, self.request.id)
        return {"status": "success", "call_id": call_id}
    except Exception as error:
        supabase.table("outbound_calls").update(
            {"status": "failed", "error_message": str(error)}
        ).eq("id", call_id).execute()
        logger.exception("Outbound call failed (call_id=%s, task_id=%s)", call_id, self.request.id)
        raise
