import asyncio
import csv
import io
import os
import logging
import datetime
from dotenv import load_dotenv
import aiohttp
from vobiz_outbound import make_outbound_call
from supabase import create_client, Client
from typing import Optional

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bulk-dialer")

load_dotenv()

# Supabase Setup (shared with main.py)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Connected to Supabase from bulk_dialer")
    except Exception as e:
        logger.error(f"Failed to connect to Supabase from bulk_dialer: {e}")

# In-memory fallback queue (shared with main.py - populated even when Supabase unavailable)
outbound_queue = []


async def fetch_sheet_data(url):
    """
    Fetches the CSV data from the Google Sheet URL.
    Handles automatic conversion from sharing link to export link.
    """
    # AUTO-FIX: Convert common sharing link format to CSV export link
    if "docs.google.com/spreadsheets" in url and "/edit" in url:
        logger.info("Auto-converting standard sharing link to CSV export link...")
        url = url.split("/edit")[0] + "/export?format=csv"
    elif "docs.google.com/spreadsheets" in url and not "export?" in url:
        # Handle links without /edit but needing /export
        if not url.endswith("/"):
            url += "/"
        url += "export?format=csv"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                text = await resp.text()
                # Basic check if we got HTML instead of CSV
                if "<!DOCTYPE html>" in text or "<html" in text.lower():
                    logger.error(
                        "Fetched content is HTML, not CSV. Please ensure the sheet is 'Published to the web' or use a public sharing link."
                    )
                    return None
                return text
            else:
                logger.error(f"Failed to fetch sheet: Status {resp.status}")
                return None


async def run_bulk_dialer():
    sheet_url = os.getenv("GOOGLE_SHEET_URL")
    if not sheet_url:
        logger.error("GOOGLE_SHEET_URL not found in .env file!")
        print("\n!!! ERROR: GOOGLE_SHEET_URL is missing !!!")
        print("Please publish your Google Sheet as CSV and add the link to .env")
        return

    logger.info("Fetching student list from Google Sheet...")
    csv_data = await fetch_sheet_data(sheet_url)
    if not csv_data:
        return

    f = io.StringIO(csv_data)
    reader = csv.DictReader(f)

    # Identify column names (Case-insensitive search)
    headers = reader.fieldnames
    name_col = next((h for h in headers if "name" in h.lower()), None)
    phone_col = next(
        (
            h
            for h in headers
            if "phone" in h.lower() or "number" in h.lower() or "contact" in h.lower()
        ),
        None,
    )
    course_col = next(
        (h for h in headers if "course" in h.lower() or "interest" in h.lower()), None
    )

    if not phone_col:
        logger.error(f"Could not find a 'Phone' column in headers: {headers}")
        return

    logger.info(
        f"Found columns: Name='{name_col}', Phone='{phone_col}', Course='{course_col}'"
    )

    count = 0
    failed_count = 0
    for row in reader:
        name = row.get(name_col, "Student") if name_col else "Student"
        phone = row.get(phone_col)
        course = (
            row.get(course_col, "our training programs")
            if course_col
            else "our training programs"
        )

        if not phone or not phone.strip():
            continue

        prompt = f"Dialing {name} ({phone}) for course: {course}..."
        print(f"\n[BULK] {prompt}")
        logger.info(prompt)

        call_record_id = None
        status = "failed"
        error_msg = None

        try:
            # Step 1: Create queue entry in Supabase (or in-memory fallback)
            if supabase:
                try:
                    response = (
                        supabase.table("outbound_calls")
                        .insert(
                            {
                                "phone_number": phone,
                                "status": "pending",
                            }
                        )
                        .execute()
                    )
                    if response.data:
                        call_record_id = response.data[0]["id"]
                        logger.info(
                            f"Created queue entry for {name} (ID: {call_record_id})"
                        )
                        count += 1
                except Exception as e:
                    logger.error(f"Failed to create Supabase record for {name}: {e}")
                    failed_count += 1
            else:
                # Fallback to in-memory queue
                call_id = len(outbound_queue) + 1
                call_record_id = str(call_id)  # Use ID as string for in-memory
                outbound_queue.append(
                    {
                        "id": call_record_id,
                        "phone": phone,
                        "status": "pending",
                        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        "name": name,
                        "course": course
                    }
                )
                logger.info(f"Added {name} to in-memory queue (ID: {call_record_id})")
                count += 1

        except Exception as e:
            error_msg = str(e)
            failed_count += 1
            logger.error(f"Failed to process {name}: {e}")

            # Small 2 second gap for system cleanup before the next dial
            await asyncio.sleep(2)

    print(f"\n✅ COMPLETED: Successfully dispatched {count} calls from the sheet.")
    if failed_count > 0:
        print(f"⚠️  {failed_count} calls failed. Check logs for details.")


if __name__ == "__main__":
    asyncio.run(run_bulk_dialer())
