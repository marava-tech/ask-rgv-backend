import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from core.auth import get_current_user
from db import queries
from models.schemas import QuizAnswerIn, QuizAssessmentOut, QuizQuestionOut, QuizSubmitRequest, QuizSubmitResponse
from services.quiz import generate_assessment_background

router = APIRouter(prefix="/quiz", tags=["quiz"])


@router.get("/questions", response_model=list[QuizQuestionOut])
async def get_quiz_questions(user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    rows = await queries.get_quiz_questions()
    return [QuizQuestionOut(**dict(r)) for r in rows]


@router.post("/submit", response_model=QuizSubmitResponse, status_code=202)
async def submit_quiz(
    body: QuizSubmitRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user_id = user["sub"]

    existing = await queries.get_user_valid_assessment(user_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "assessment_not_expired", "expires_at": existing["expires_at"].isoformat()},
        )

    # Validate indices against allows_multiple_select per question
    question_ids = [str(a.question_id) for a in body.answers]
    questions = await queries.get_quiz_questions_by_ids(question_ids)
    q_map = {str(q["id"]): q for q in questions}

    for a in body.answers:
        qid = str(a.question_id)
        q = q_map.get(qid)
        if q and not q["allows_multiple_select"] and len(a.selected_option_indices) > 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Question {qid} is single-select but received {len(a.selected_option_indices)} indices",
            )

    session_id = str(_uuid.uuid4())
    version = await queries.get_next_assessment_version(user_id)

    raw_answers = [
        {
            "question_id": str(a.question_id),
            "selected_option_indices": a.selected_option_indices,
        }
        for a in body.answers
    ]

    assessment_id = await queries.create_assessment(
        user_id=user_id,
        session_id=session_id,
        raw_answers=raw_answers,
        version=version,
    )
    await queries.insert_quiz_responses(user_id, session_id, body.answers)

    background_tasks.add_task(
        generate_assessment_background,
        assessment_id=assessment_id,
        user_id=user_id,
        answers=raw_answers,
    )

    return QuizSubmitResponse(session_id=session_id, status="processing")


@router.get("/assessment", response_model=QuizAssessmentOut)
async def get_assessment(user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    row = await queries.get_latest_assessment(user["sub"])
    if not row:
        return QuizAssessmentOut(status="none", can_reassess=True)
    now = datetime.now(timezone.utc)
    can_reassess = row["expires_at"].replace(tzinfo=timezone.utc) < now
    return QuizAssessmentOut(
        status=row["status"],
        assessment_text=row["assessment_text"] or None,
        assessed_at=row["assessed_at"],
        expires_at=row["expires_at"],
        version=row["version"],
        can_reassess=can_reassess,
    )
