import json
import logging
import os
import aiohttp
from utils.normalizers import _normalize_phone_e164

logger = logging.getLogger("voice-agent")

# WABridge Credentials
WABRIDGE_AUTH_KEY = os.getenv("WABRIDGE_AUTH_KEY")
WABRIDGE_APP_KEY = os.getenv("WABRIDGE_APP_KEY")
WABRIDGE_DEVICE_ID = os.getenv("WABRIDGE_DEVICE_ID")
WABRIDGE_TEMPLATE_ID = os.getenv("WABRIDGE_TEMPLATE_ID")
WABRIDGE_MEDIA_TYPE = os.getenv("WABRIDGE_MEDIA_TYPE", "text")


async def send_ziper_whatsapp(phone_number, message_text):
    """Ziper.io WhatsApp API integration using standard URL parameters."""
    if not phone_number:
        return

    clean_phone = _normalize_phone_e164(phone_number)

    logger.info(f"Preparing to send Ziper.io WhatsApp message to {clean_phone}...")

    access_token = os.getenv("ZIPER_ACCESS_TOKEN") or os.getenv("ZIPER_API_TOKEN")
    instance_id = os.getenv("ZIPER_INSTANCE_ID")

    if not access_token or not instance_id:
        logger.error(
            "Ziper.io credentials missing! Please check that ZIPER_ACCESS_TOKEN and ZIPER_INSTANCE_ID are correctly set in your .env file."
        )
        return

    try:
        url = "https://ziper.io/api/send.php"
        params = {
            "access_token": access_token,
            "instance_id": instance_id,
            "type": "text",
            "number": clean_phone,
            "message": message_text,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, ssl=False) as response:
                if response.status in [200, 201]:
                    logger.info(f"Successfully sent Ziper.io WhatsApp to {clean_phone}")
                else:
                    resp_text = await response.text()
                    logger.error(f"Ziper.io API failed: {resp_text}")
    except Exception as e:
        logger.error(f"Error calling Ziper.io: {e}")


async def send_wabridge_whatsapp(phone_number, template_id=None):
    """
    WABridge WhatsApp API integration to send template messages.
    Uses GET request with query params matching the WABridge createmessage endpoint.
    """
    if not phone_number:
        return

    clean_phone = _normalize_phone_e164(phone_number)
    actual_template = template_id or WABRIDGE_TEMPLATE_ID

    logger.info(
        f"Sending WABridge template to {clean_phone} | template={actual_template} | media_type={WABRIDGE_MEDIA_TYPE}"
    )

    if not all([WABRIDGE_AUTH_KEY, WABRIDGE_APP_KEY, WABRIDGE_DEVICE_ID, WABRIDGE_TEMPLATE_ID]):
        logger.error("WABridge credentials or Template ID missing! Please check your .env file.")
        return

    try:
        url = "https://web.wabridge.com/api/createmessage"
        params = {
            "auth-key": WABRIDGE_AUTH_KEY,
            "app-key": WABRIDGE_APP_KEY,
            "device_id": WABRIDGE_DEVICE_ID,
            "destination_number": clean_phone,
            "template_id": actual_template,
            "variables": "[]",
            "media_type": WABRIDGE_MEDIA_TYPE,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, ssl=False) as response:
                resp_text = await response.text()
                logger.info(f"WABridge HTTP {response.status} | Raw: {resp_text}")

                try:
                    resp_json = json.loads(resp_text)
                    api_status = resp_json.get("status")
                    api_message = resp_json.get("message")
                    message_id = resp_json.get("data", {}).get("messageid")
                    from_number = resp_json.get("data", {}).get("from")
                    to_number = resp_json.get("data", {}).get("to")

                    logger.info(
                        f"WABridge response: status={api_status} message={api_message} "
                        f"messageid={message_id} from={from_number} to={to_number}"
                    )

                    if api_status and not message_id:
                        logger.warning(
                            "WABridge returned status=true but no messageid — delivery may fail"
                        )

                    if not api_status:
                        logger.error(f"WABridge API reported failure: {api_message}")

                except json.JSONDecodeError:
                    logger.error(f"WABridge returned non-JSON response: {resp_text[:200]}")

                if response.status not in [200, 201]:
                    logger.error(f"WABridge HTTP error {response.status}")

    except Exception as e:
        logger.error(f"Error calling WABridge: {e}")
