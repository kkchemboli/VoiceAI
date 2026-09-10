import asyncio
import csv
import io
import logging
import datetime

import aiohttp

from services.supabase_client import execute_query, get_supabase_client
from services.runtime_config import get_sheet_url

logger = logging.getLogger("bulk-dialer")

outbound_queue = []


async def fetch_sheet_data(url):
    """
    Fetches the CSV data from the Google Sheet URL.
    Handles automatic conversion from sharing link to export link.
    """
    if "docs.google.com/spreadsheets" in url and "/edit" in url:
        logger.info("Auto-converting standard sharing link to CSV export link...")
        url = url.split("/edit")[0] + "/export?format=csv"
    elif "docs.google.com/spreadsheets" in url and "export?" not in url:
        if not url.endswith("/"):
            url += "/"
        url += "export?format=csv"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                text = await resp.text()
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
    sheet_url = get_sheet_url()
    if not sheet_url:
        logger.error("GOOGLE_SHEET_URL not found in runtime configuration!")
        logger.error("Set the Google Sheet URL from the admin panel, or provide GOOGLE_SHEET_URL in the .env file")
        return

    logger.info("Fetching student list from Google Sheet...")
    csv_data = await fetch_sheet_data(sheet_url)
    if not csv_data:
        return

    f = io.StringIO(csv_data)
    reader = csv.DictReader(f)

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

    supabase = get_supabase_client()
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
        logger.info("Bulk dialer prompt generated", extra={"prompt": prompt})

        call_record_id = None
        error_msg = None

        try:
            if supabase:
                try:
                    response = await execute_query(
                        supabase.table("outbound_calls").insert(
                            {
                                "phone_number": phone,
                                "status": "pending",
                            }
                        )
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
                call_id = len(outbound_queue) + 1
                call_record_id = str(call_id)
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

            await asyncio.sleep(2)

    logger.info("Bulk calls dispatched", extra={"count": count})
    if failed_count > 0:
        logger.warning("Bulk calls failed", extra={"failed_count": failed_count})


if __name__ == "__main__":
    asyncio.run(run_bulk_dialer())