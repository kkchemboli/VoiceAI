import logging
from livekit.agents import llm
from rag.intents import is_question_turn, classify_intent

logger = logging.getLogger("voice-agent")


async def handle_user_turn_completed(
    turn_ctx: llm.ChatContext,
    new_message: llm.ChatMessage,
    rag_engine: any,
    agent: any = None,
) -> None:
    """
    Evaluates the latest user message with Intent-First Routing.
    Synchronizes AgentState and injects matching knowledge context or fallback directive into turn_ctx.
    """
    user_text = ""
    content = getattr(new_message, "content", "")
    if isinstance(content, list):
        user_text = " ".join([str(c) for c in content if isinstance(c, str)])
    else:
        user_text = str(content).strip()

    if not user_text:
        return

    # 1. INTENT CLASSIFICATION FIRST
    intent = classify_intent(user_text)
    logger.info(f"Router: Classified user intent as '{intent}' for text: '{user_text}'")

    # 2. RUNTIME AGENT STATE SYNCHRONIZATION
    if agent and hasattr(agent, "state") and agent.state:
        state = agent.state

        # Update course selection if mentioned
        state.update_course_selection(user_text)

        # Detect confirmation keywords in booking flow
        lower = user_text.lower()
        confirm_words = ["yes", "yeah", "haan", "sahi", "correct", "right", "sure"]
        if any(w in lower for w in confirm_words):
            if state.booking_stage == "NAME_COLLECTION":
                state.name_confirmed = True
                logger.info("AgentState: Name confirmed by user.")
            elif state.booking_stage == "PHONE_COLLECTION":
                state.phone_confirmed = True
                logger.info("AgentState: Phone confirmed by user.")

        # Inject concise state summary into ChatContext if active
        if summary := state.get_state_summary():
            turn_ctx.items.insert(
                1,
                llm.ChatMessage(role="system", content=[summary]),
            )

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

