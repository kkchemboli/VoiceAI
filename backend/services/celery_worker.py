import asyncio
import logging
from celery import Celery
from dotenv import load_dotenv

from services.supabase_client import get_supabase_client
from services.bulk_dialer import run_bulk_dialer
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

SUPABASE_URL = settings.supabase_url
SUPABASE_KEY = settings.supabase_key

supabase = get_supabase_client()


@app.task(bind=True)
def import_campaign_leads(self):
    """Import leads from the campaign sheet and enqueue one dial task per lead."""
    logger.info("Campaign lead import triggered (task_id=%s)...", self.request.id)
    metrics.increment("celery_tasks_started_total")

    try:
        with start_span(
            "celery.import_campaign_leads",
            {"celery.task_id": self.request.id},
        ) as span:
            call_ids = asyncio.run(run_bulk_dialer())
            for call_id in call_ids:
                task = process_outbound_call.apply_async(args=[call_id])
                logger.info(
                    "Enqueued outbound call task (call_id=%s, task_id=%s)",
                    call_id,
                    task.id,
                )
            if span is not None:
                span.set_attribute("celery.leads_imported", len(call_ids))
        logger.info("Campaign lead import completed (task_id=%s)", self.request.id)
        return {"imported": len(call_ids), "enqueued": len(call_ids)}
    except Exception as error:
        logger.exception("Campaign lead import failed (task_id=%s)", self.request.id)
        raise


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
