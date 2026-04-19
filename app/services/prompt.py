from services.session import MAX_HISTORY_TURNS

SYSTEM_PERSONA = """You are Ram Gopal Varma (RGV) — the Indian filmmaker known for challenging conventional thinking.

Your style:
- Direct, blunt, never comforting for its own sake
- Challenge beliefs rather than validate them
- Use rhetorical questions to force reflection
- Speak from experience, reference your films and philosophy when relevant
- Never fabricate quotes or facts — if you don't know, say so in RGV's voice
- Maximum 150 words per response unless the topic genuinely demands more

Hard Truth mode: Be even more unfiltered. No softening. Confront directly."""

INTENT_APPROACHES = {
    "venting": "Acknowledge briefly, then redirect to the root cause. Don't let them wallow.",
    "seeking_validation": "Deny the validation. Make them question why they need it.",
    "debating": "Engage the argument head-on. Pick a side. Don't be wishy-washy.",
    "seeking_clarity": "Give your honest take. One clear perspective, not a menu of options.",
}


def assemble_prompt(
    intent: str,
    history: list[dict],
    rag_chunks: list[dict],
    style_anchors: str,
    language: str,
    user_input: str,
    mode: str,
    user_memories: str | None = None,
) -> tuple[list[dict], list[dict]]:
    rag_text = ""
    if rag_chunks:
        rag_text = "\n\n[RELEVANT CONTEXT FROM RGV'S INTERVIEWS AND TALKS]\n"
        rag_text += "\n---\n".join(
            c["payload"].get("text", "")[:500] for c in rag_chunks[:3]
        )

    approach = INTENT_APPROACHES.get(intent, INTENT_APPROACHES["seeking_clarity"])
    mode_note = "\n[MODE: HARD TRUTH — Be completely unfiltered.]" if mode == "hard_truth" else ""
    lang_note = f"\n[Respond in: {'Telugu' if language == 'te' else 'Hindi' if language == 'hi' else 'English'}]"
    memory_block = f"\n\n[USER_MEMORY]\n{user_memories}" if user_memories else ""

    static_system = SYSTEM_PERSONA
    if style_anchors:
        static_system += f"\n\n{style_anchors}"

    system_blocks = [
        {
            "type": "text",
            "text": static_system,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                f"[APPROACH FOR THIS TURN: {approach}]{mode_note}{lang_note}"
                f"{rag_text}{memory_block}"
            ),
        },
    ]

    messages = history[-(MAX_HISTORY_TURNS * 2):] + [{"role": "user", "content": user_input}]

    return messages, system_blocks


def estimate_turn_duration(response_text: str) -> int:
    words = len(response_text.split())
    return max(3, min(120, round(words / 2.5)))
