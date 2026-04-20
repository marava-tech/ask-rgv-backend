import json
from db.pool import get_pool
from services.claude import haiku_call

_RUBRIC_SYSTEM = """You are evaluating AI responses from a chatbot persona modeled on Ram Gopal Varma (RGV), Indian filmmaker known for direct, philosophical, provocative thinking.

Evaluate the AI response against these criteria:
1. directness - Does it avoid hedging, beat-around-the-bush answers? (1-5)
2. persona_consistency - Does it sound like RGV? Confrontational, philosophical, not comforting? (1-5)
3. depth - Does it provide substance or just surface-level response? (1-5)
4. no_cliches - Does it avoid generic AI-sounding language, corporate speak? (1-5)
5. authenticity - Does it feel like genuine RGV perspective or fabricated? (1-5)

Respond ONLY with valid JSON in this format:
{"directness": N, "persona_consistency": N, "depth": N, "no_cliches": N, "authenticity": N, "issue": "brief note if any score < 3, else null"}"""


async def run_monitor(sample_size: int = 50) -> dict:
    pool = get_pool()

    rows = await pool.fetch(
        """
        SELECT t.user_input, t.response, s.language
        FROM turns t
        JOIN sessions s ON s.id = t.session_id
        WHERE t.response IS NOT NULL AND t.response != ''
        ORDER BY t.created_at DESC
        LIMIT $1
        """,
        sample_size,
    )

    if not rows:
        return {"status": "no_data"}

    dim_scores: dict[str, list[float]] = {
        "directness": [], "persona_consistency": [], "depth": [],
        "no_cliches": [], "authenticity": [],
    }
    flagged = []
    improvement_notes: list[str] = []

    for row in rows:
        prompt = (
            f"USER QUESTION:\n{row['user_input']}\n\n"
            f"AI RESPONSE:\n{row['response'][:800]}\n\n"
            "Evaluate this response against the RGV persona rubric."
        )
        try:
            raw = await haiku_call(prompt, system=_RUBRIC_SYSTEM)
            # Extract JSON from response
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                continue
            scores = json.loads(raw[start:end])
            for dim in dim_scores:
                dim_scores[dim].append(float(scores.get(dim, 3)))
            issue = scores.get("issue")
            if issue and any(float(scores.get(d, 3)) < 3.0 for d in dim_scores):
                flagged.append({
                    "question": row["user_input"][:200],
                    "response": row["response"][:300],
                    "issue": issue,
                })
        except Exception:
            for dim in dim_scores:
                dim_scores[dim].append(3.0)

    averages = {d: (sum(v) / len(v) if v else 0.0) for d, v in dim_scores.items()}

    weak_dims = [d for d, avg in averages.items() if avg < 3.5]
    if weak_dims:
        improvement_notes.append(
            f"Weak dimensions (avg < 3.5): {', '.join(weak_dims)}. "
            "Consider tightening the system prompt in these areas."
        )
    if flagged:
        improvement_notes.append(
            f"{len(flagged)} out of {len(rows)} responses flagged for persona issues."
        )

    report = {**averages, "flagged": flagged, "sample_size": len(rows)}
    notes_text = "\n".join(improvement_notes) if improvement_notes else "No major issues found."

    await pool.execute(
        "INSERT INTO monitor_runs (sample_size, report_json, improvement_notes) VALUES ($1, $2, $3)",
        len(rows), json.dumps(report), notes_text,
    )

    return report
