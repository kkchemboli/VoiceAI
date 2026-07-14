import asyncio
import os
import datetime
import pytz
import logging
from celery import Celery
from dotenv import load_dotenv

# We need to import make_outbound_call, but it's an async function
from vobiz_outbound import make_outbound_call
from supabase import create_client

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("celery-worker")

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
app = Celery("campaign_worker", broker=redis_url)

app.conf.beat_schedule = {
    'process-campaign-queue-every-minute': {
        'task': 'celery_worker.process_campaign_queue',
        'schedule': 60.0,
    },
}
app.conf.timezone = 'UTC'

# Setup Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Failed to connect to Supabase: {e}")

@app.task
def process_campaign_queue():
    logger.info("Cron triggered: Checking Campaign Queue...")
    
    # Enforce IST time window (8 AM to 8 PM)
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
        # Fetch up to 1 pending call
        # We only take 1 to avoid Celery workers tripping over each other if we had multiple.
        response = supabase.table("outbound_calls").select("*").eq("status", "pending").order("created_at").limit(1).execute()
        
        if not response.data:
            logger.info("No pending calls found in the queue.")
            return
            
        record = response.data[0]
        call_id = record["id"]
        phone = record["phone_number"]
        name = record.get("name", "Student") # Fallback since bulk_dialer might not insert name
        course = record.get("course", "our training programs")
        
        logger.info(f"Picked up call {call_id} for {phone}. Starting call...")

        # Update status to 'calling' temporarily so another worker doesn't pick it up
        supabase.table("outbound_calls").update({"status": "calling"}).eq("id", call_id).execute()

        # Execute the call synchronously wrapping the asyncio call
        async def run_call():
            await make_outbound_call(phone, name, course, wait_for_completion=True)
            
        asyncio.run(run_call())
        
        # After call succeeds
        supabase.table("outbound_calls").update({"status": "success"}).eq("id", call_id).execute()
        logger.info(f"Successfully finished call {call_id}.")
        
    except Exception as e:
        logger.error(f"Error processing campaign queue: {e}")
        # If call_id was fetched but failed, we mark as failed
        if 'call_id' in locals():
            try:
                supabase.table("outbound_calls").update({"status": "failed", "error_message": str(e)}).eq("id", call_id).execute()
            except Exception as update_err:
                logger.error(f"Failed to update status to 'failed': {update_err}")
