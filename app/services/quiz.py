import logging

from services.claude import get_client
from db import queries

_log = logging.getLogger(__name__)

_PERSONA_BLOCK = """You are Ram Gopal Varma (RGV) — the Indian filmmaker known for challenging conventional thinking.
Your style:
- Direct, blunt, never comforting for its own sake
- Challenge beliefs rather than validate them
- Speak from experience and observation
- Never fabricate facts
- Write in natural sentence casing"""

_ASSESSMENT_INSTRUCTION = (
    "You've reviewed this person's answers to 11 worldview questions. "
    "Assess them in 200–300 words, honest and direct, no flattery. "
    "Tell them what their answers reveal about how they actually think — "
    "their philosophical lean, where they deceive themselves, what drives them. "
    "Speak directly to them as RGV would. Do not list the questions back. "
    "Do not soften the assessment. Start with your sharpest observation."
)


def _build_answers_text(answers: list[dict]) -> str:
    lines = []
    for i, a in enumerate(answers, 1):
        q_text = a.get("question_text", f"Question {i}")
        indices = a.get("selected_option_indices", [])
        option_texts = a.get("option_texts", [])
        chosen = [option_texts[idx] for idx in indices if idx < len(option_texts)]
        answer_str = "; ".join(f'"{t}"' for t in chosen) if chosen else '"(no answer)"'
        lines.append(f"Q{i}: {q_text}\nAnswer: {answer_str}")
    return "\n\n".join(lines)


async def generate_assessment_background(
    assessment_id: str,
    user_id: str,
    answers: list[dict],
) -> None:
    try:
        question_ids = [a["question_id"] for a in answers]
        questions = await queries.get_quiz_questions_by_ids(question_ids)
        q_map = {str(q["id"]): q for q in questions}

        enriched = []
        for a in answers:
            q = q_map.get(a["question_id"], {})
            options = q.get("options", [])
            enriched.append({
                **a,
                "question_text": q.get("question_text", ""),
                "option_texts": options,
            })

        answers_text = _build_answers_text(enriched)
        user_message = f"{answers_text}\n\n{_ASSESSMENT_INSTRUCTION}"

        client = get_client()
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": _PERSONA_BLOCK,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )

        text_blocks = [b for b in response.content if b.type == "text"]
        assessment_text = text_blocks[0].text if text_blocks else ""

        if not assessment_text:
            await queries.update_assessment_status(assessment_id, "failed")
            return

        await queries.update_assessment_ready(assessment_id, assessment_text)
        _log.info("[quiz] assessment ready for user %s", user_id)

    except Exception as e:
        _log.error("[quiz] assessment generation failed for %s: %r", user_id, e)
        try:
            await queries.update_assessment_status(assessment_id, "failed")
        except Exception:
            pass
