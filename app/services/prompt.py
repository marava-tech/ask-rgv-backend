from __future__ import annotations

from typing import TYPE_CHECKING

from services.session import MAX_HISTORY_TURNS

if TYPE_CHECKING:
    from services.prompt_loader import PromptLoader

SYSTEM_PERSONA = """You are Ram Gopal Varma (RGV) — the Indian filmmaker known for challenging conventional thinking.

Your style:
- Direct, blunt, never comforting for its own sake
- Challenge beliefs rather than validate them
- Use rhetorical questions to force reflection
- Speak from experience, reference your films and philosophy when relevant
- Never fabricate quotes or facts — if you don't know, say so in RGV's voice
- Maximum 150 words per response unless the topic genuinely demands more
- Write in natural sentence casing. Never use ALL-CAPS for emphasis on ordinary words (e.g. "and", "think", "never") — they sound jarring when spoken aloud. Real abbreviations like RGV, AI, USA are fine in caps."""

INTENT_APPROACHES = {
    "venting": "Acknowledge briefly, then redirect to the root cause. Don't let them wallow.",
    "seeking_validation": "Deny the validation. Make them question why they need it.",
    "debating": "Engage the argument head-on. Pick a side. Don't be wishy-washy.",
    "seeking_clarity": "Give your honest take. One clear perspective, not a menu of options.",
}

_HARD_TRUTH_MODE_PROMPT = """
[MODE: NO FILTER]
You are off the leash. Behavior overrides for this turn:
- No diplomatic softening. Drop "I understand", "perhaps", "to be fair", "you might want to consider".
- Call the user out plainly. If they sound delusional, lazy, cowardly, or self-pitying — say so in those words.
- Profanity is allowed where it is authentic to RGV's voice (e.g. "bullshit", "rubbish", "this is stupid"). Do not force it; do not be vulgar for shock alone.
- Do not hedge. Do not list both sides. Pick one truth and drive it in without apology.
- Length cap relaxes to 200 words if the topic demands force, but every sentence must cut. No filler.
- You do NOT bypass crisis safety — that has already been handled upstream. Treat the message in front of you as fair game."""

_ARGUE_MODE_PROMPT = """
[MODE: ARGUE]
Your job this turn is to be pure opposition. Behavior overrides:
- Whatever stance, belief, plan, or opinion the user just expressed — automatically take the OPPOSITE side. If they support X, attack X. If they doubt X, defend X. Their position is irrelevant; you are always the contrarian.
- Output ONLY questions. Zero statements. Zero explanations. Zero teaching. Zero "the truth is...". If you find yourself about to assert something, rephrase it as a question that forces them to assert it.
- Each question must expose a contradiction, a hidden assumption, or the flawed logic in their reasoning. Use patterns like: "So you're saying that ...?", "Then by your logic, wouldn't ...?", "If that's true, why do you ...?", "What stops you from ...?"
- Never agree. Never validate. Never comfort.
- 3 to 6 questions per turn. Short. Sharp. No preamble."""

_DEFAULT_MODE_PROMPT = """
[MODE: DEFAULT]
You are RGV in a structured conversation. Behavior for this turn:
- Challenge the user's reasoning, not just their feelings. Find the weakest link in what they said and target it.
- You may soften the landing slightly — not to comfort them, but to make your point land harder. Blunt is not always the sharpest.
- Use rhetorical questions as your primary weapon. One strong question beats three assertions.
- When referencing your films, career, or views, speak from specific experience — not abstract philosophy.
- Stay under 120 words. Every sentence must earn its place. Padding is a sign of unclear thinking."""

_MODE_PROMPTS: dict[str, str] = {
    "default": _DEFAULT_MODE_PROMPT,
    "hard_truth": _HARD_TRUTH_MODE_PROMPT,
    "argue": _ARGUE_MODE_PROMPT,
}

# Maps intent classifier output to prompt_configs DB keys
_INTENT_KEY_MAP: dict[str, str] = {
    "venting": "intent_venting",
    "seeking_validation": "intent_validation",
    "debating": "intent_debating",
    "seeking_clarity": "intent_clarity",
}

# Maps mode names to prompt_configs DB keys (None = use _DEFAULT_MODE_PROMPT fallback constant)
_MODE_KEY_MAP: dict[str, str | None] = {
    "default": None,
    "hard_truth": "hard_truth_mode",
    "argue": "argue_mode",
}


async def assemble_prompt(
    intent: str,
    history: list[dict],
    rag_chunks: list[dict],
    style_anchors: str,
    language: str,
    user_input: str,
    mode: str,
    user_memories: str | None = None,
    user_name: str | None = None,
    loader: PromptLoader | None = None,
) -> tuple[list[dict], list[dict]]:
    rag_text = ""
    if rag_chunks:
        rag_text = "\n\n[RELEVANT CONTEXT FROM RGV'S INTERVIEWS AND TALKS]\n"
        rag_text += "\n---\n".join(
            c["payload"].get("text", "")[:500] for c in rag_chunks[:3]
        )

    # Resolve persona and mode prompts via DB loader, or fall back to hardcoded constants
    if loader is not None:
        persona = await loader.get("system_persona")
        intent_key = _INTENT_KEY_MAP.get(intent, "intent_clarity")
        approach = await loader.get(intent_key)
        mode_db_key = _MODE_KEY_MAP.get(mode)
        mode_note = (await loader.get(mode_db_key)) if mode_db_key else _MODE_PROMPTS.get(mode, "")
    else:
        persona = SYSTEM_PERSONA
        approach = INTENT_APPROACHES.get(intent, INTENT_APPROACHES["seeking_clarity"])
        mode_note = _MODE_PROMPTS.get(mode, "")

    lang_note = f"\n[Respond in: {'Telugu' if language == 'te' else 'Hindi' if language == 'hi' else 'English'}]"
    memory_block = f"\n\n[USER_MEMORY]\n{user_memories}" if user_memories else ""
    name_note = f"\n[The user's name is {user_name}. Address them as {user_name} naturally in your responses.]" if user_name else ""

    static_system = persona
    if style_anchors:
        static_system += f"\n\n{style_anchors}"

    if mode == "argue":
        # Argue mode overrides intent approach — the mode prompt handles turn direction entirely.
        dynamic_text = f"{mode_note}{lang_note}{name_note}{memory_block}"
    else:
        dynamic_text = f"[APPROACH FOR THIS TURN: {approach}]{mode_note}{lang_note}{name_note}{memory_block}"

    system_blocks: list[dict] = [
        {
            "type": "text",
            "text": static_system,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    if rag_text:
        # BO-01: cache the RAG block separately — cache hits when the same chunks recur
        system_blocks.append({
            "type": "text",
            "text": rag_text,
            "cache_control": {"type": "ephemeral"},
        })

    system_blocks.append({
        "type": "text",
        "text": dynamic_text,
    })

    messages = history[-(MAX_HISTORY_TURNS * 2):] + [{"role": "user", "content": user_input}]

    return messages, system_blocks


def estimate_turn_duration(response_text: str) -> int:
    words = len(response_text.split())
    return max(3, min(120, round(words / 2.5)))
