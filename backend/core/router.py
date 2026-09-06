import logging
from livekit.agents import llm
from rag.intents import is_question_turn, classify_intent
from utils.normalizers import extract_digits_from_spoken_text, format_digits_english, _normalize_phone_e164

logger = logging.getLogger("voice-agent")


async def handle_user_turn_completed(
    turn_ctx: llm.ChatContext,
    new_message: llm.ChatMessage,
    rag_engine: any,
    agent: any = None,
) -> None:
    """
    Evaluates the latest user message with STATE > INTENT > RAG hierarchy.
    Intercepts booking stage turns (PHONE_COLLECTION, SLOT_SELECTION, CONFIRMATION) before intent/RAG,
    validates digits code-side, and injects state guidance into turn_ctx.
    """
    user_text = ""
    content = getattr(new_message, "content", "")
    if isinstance(content, list):
        user_text = " ".join([str(c) for c in content if isinstance(c, str)])
    else:
        user_text = str(content).strip()

    if not user_text:
        return

    # 1. HIGHEST PRIORITY: STATE INTERCEPTION (STATE > INTENT > RAG)
    if agent and hasattr(agent, "state") and agent.state:
        state = agent.state

        # Update course selection if mentioned
        state.update_course_selection(user_text)

        # STATE INTERCEPTION: PHONE_COLLECTION Stage
        if state.booking_stage == "PHONE_COLLECTION":
            digits = extract_digits_from_spoken_text(user_text)
            logger.info(f"Router [PHONE_COLLECTION]: Extracted raw digits '{digits}' from '{user_text}'")

            if len(digits) == 10 or (len(digits) == 12 and digits.startswith("91")):
                clean = _normalize_phone_e164(digits)
                state.phone_number = "+" + clean if not clean.startswith("+") else clean
                state.update_booking_stage("CONFIRMATION")
                formatted_digits = format_digits_english(clean[-10:])
                logger.info(f"Router: Valid 10-digit phone normalized to {state.phone_number}. Transitioning to CONFIRMATION.")

                notice = (
                    f"[SYSTEM NOTICE: Recorded valid 10-digit mobile number '{state.phone_number}'. "
                    f"Ask the user to confirm this number aloud, reading the digits in ENGLISH: '{formatted_digits}'. "
                    "NEVER use Hindi words for digits (never say 'सात चार नौ आठ').]"
                )
                turn_ctx.items.insert(1, llm.ChatMessage(role="system", content=[notice]))
                return
            elif len(digits) > 0 and len(digits) != 10:
                logger.warning(f"Router: Incomplete phone input detected ({len(digits)} digits: '{digits}')")
                notice = (
                    f"[SYSTEM NOTICE: The user provided only {len(digits)} digits ({digits}). "
                    "An Indian mobile number requires exactly 10 digits. "
                    "DO NOT invent, guess, or complete any missing digits. "
                    f"Politely tell the user: 'I received {len(digits)} digits ({digits}). Please provide your complete ten-digit mobile number.']"
                )
                turn_ctx.items.insert(1, llm.ChatMessage(role="system", content=[notice]))
                return

        # STATE INTERCEPTION: CONFIRMATION Stage
        elif state.booking_stage == "CONFIRMATION":
            lower = user_text.lower()
            confirm_words = ["yes", "yeah", "haan", "sahi", "correct", "right", "sure", "book"]
            if any(w in lower for w in confirm_words):
                state.phone_confirmed = True
                state.update_booking_stage("COMPLETED")
                logger.info("Router [CONFIRMATION]: Booking confirmed by user.")
                notice = (
                    f"[SYSTEM NOTICE: Appointment booking confirmed by user for {state.selected_slot or 'selected slot'}. "
                    f"Call schedule_demo_class immediately with selected_slot='{state.selected_slot}', "
                    f"phone_number='{state.phone_number}', name='{state.caller_name}'.]"
                )
                turn_ctx.items.insert(1, llm.ChatMessage(role="system", content=[notice]))
                return

        # Inject concise state summary into ChatContext
        if summary := state.get_state_summary():
            turn_ctx.items.insert(
                1,
                llm.ChatMessage(role="system", content=[summary]),
            )

    # 2. INTENT CLASSIFICATION
    intent = classify_intent(user_text)
    logger.info(f"Router: Classified user intent as '{intent}' for text: '{user_text}'")

    if len(user_text) < 8:
        return

    # 3. DOMAIN INTENT ROUTING (Bypasses generic RAG refusal for known prompts)
    if intent in ("COURSE_QUERY", "DEMO_BOOKING_INTENT"):
        logger.info(f"Router: Bypassing RAG-miss refusal for domain intent '{intent}'. Course/Booking prompt will answer.")
        return

    if intent == "ADDRESS_QUERY":
        logger.info("Router: Address query detected. Location safety rule will handle response.")
        return

    # 4. RAG RETRIEVAL (For General Queries, Pricing, or detailed knowledge lookup)
    if not rag_engine:
        return

    result = await rag_engine.retrieve(user_text)

    if result.found:
        turn_ctx.items.insert(
            1,
            llm.ChatMessage(role="system", content=[result.context]),
        )
    else:
        # Only inject [NO KNOWLEDGE FOUND] fallback for GENERAL_QUERY question turns
        if intent == "GENERAL_QUERY" and is_question_turn(user_text):
            logger.info("Router: General query RAG miss on question turn. Injecting [NO KNOWLEDGE FOUND] fallback.")
            turn_ctx.items.insert(
                1,
                llm.ChatMessage(
                    role="system",
                    content=[
                        "[NO KNOWLEDGE FOUND] You do not have those specific details directly in front of you. "
                        "Politely inform the user: 'I don't have those exact details right in front of me, but I can connect you with our support team who can provide full information. Would you like me to connect you?'"
                    ],
                ),
            )


