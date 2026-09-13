import asyncio
import csv
import io
import logging

import aiohttp

from services.supabase_client import execute_query, get_supabase_client
from services.runtime_config import get_sheet_url
from utils.normalizers import normalize_phone_e164

logger = logging.getLogger("bulk-dialer")


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
    """
    Import leads from the Google Sheet into the outbound_calls queue.

    Each validated row is inserted as a 'pending' outbound call and the
    created call_id is returned so the caller can enqueue that call's task.
    """
    sheet_url = get_sheet_url()
    if not sheet_url:
        logger.error("GOOGLE_SHEET_URL not found in runtime configuration!")
        logger.error("Set the Google Sheet URL from the admin panel, or provide GOOGLE_SHEET_URL in the .env file")
        return []

    logger.info("Fetching student list from Google Sheet...")
    csv_data = await fetch_sheet_data(sheet_url)
    if not csv_data:
        return []

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
        return []

    logger.info(
        f"Found columns: Name='{name_col}', Phone='{phone_col}', Course='{course_col}'"
    )

    supabase = get_supabase_client()
    if not supabase:
        logger.error("Supabase client is not initialized. Cannot import leads.")
        return []

    call_ids = []
    count = 0
    failed_count = 0
    for row in reader:
        name = row.get(name_col, "Student") if name_col else "Student"
        raw_phone = row.get(phone_col)
        course = (
            row.get(course_col, "our training programs")
            if course_col
            else "our training programs"
        )

        if not raw_phone or not str(raw_phone).strip():
            continue

        phone = normalize_phone_e164(raw_phone)
        if not phone:
            failed_count += 1
            logger.error(f"Skipped row with invalid phone number: {name} ({raw_phone})")
            continue

        prompt = f"Dialing {name} ({phone}) for course: {course}..."
        logger.info("Bulk dialer prompt generated", extra={"prompt": prompt})

        try:
            response = await execute_query(
                supabase.table("outbound_calls").insert(
                    {
                        "name": name,
                        "course": course,
                        "phone_number": phone,
                        "status": "pending",
                    }
                )
            )
            if response.data:
                call_ids.append(response.data[0]["id"])
                count += 1
                logger.info(
                    f"Created queue entry for {name} (ID: {response.data[0]['id']})"
                )
        except Exception as e:
            failed_count += 1
            logger.error(f"Failed to create Supabase record for {name}: {e}")

    logger.info("Bulk call import complete", extra={"count": count})
    if failed_count > 0:
        logger.warning("Bulk call import had failures", extra={"failed_count": failed_count})

    return call_ids


if __name__ == "__main__":
    asyncio.run(run_bulk_dialer())