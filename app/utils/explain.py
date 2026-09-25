"""On-demand "why is this option right and the others wrong" explanations.

Uses Google's Gemini API (free tier, no card). Configured by env vars:
  GEMINI_API_KEY  -- required; unset = feature is off and the app is unchanged
  GEMINI_MODEL    -- optional override, defaults to DEFAULT_MODEL

Each question is explained once and the text is saved on the question row, so
the free-tier quota is only spent on the first student to ask about it.
"""
import logging
import os
import threading
import time
from collections import defaultdict, deque

import requests

from app.extensions import db

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"

MAX_NEW_PER_STUDENT_PER_HOUR = 25
MAX_CONCURRENT_GENERATIONS = 2

_question_locks = defaultdict(threading.Lock)
_generation_slots = threading.BoundedSemaphore(MAX_CONCURRENT_GENERATIONS)
_recent_by_student = defaultdict(deque)


def ai_configured():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _build_prompt(question):
    options = "\n".join(
        f"({letter}) {question.option_text(letter)}"
        for letter in "ABCD" if question.option_text(letter)
    )
    correct = question.correct_answer
    return (
        "You are a CA Foundation (ICAI) Business Economics teacher. A student has just finished "
        "this multiple-choice test and believes an option other than the answer key is correct. "
        "Briefly show them why the key answer is right, using elimination logic on the rest.\n\n"
        f"Question: {question.text}\n{options}\n\n"
        f"The correct answer, as fixed by the teacher, is ({correct}). Treat this as the answer key.\n\n"
        "Be BRIEF: at most about 120 words in total. Simple English, PLAIN TEXT only (no markdown "
        "symbols such as ** or #). Use exactly this layout:\n\n"
        f"Answer: ({correct}) <the option, then one or two sentences on why it is correct>\n"
        "Why not the others:\n"
        "(X) <one short line saying exactly what makes this option fail, one line per wrong option>\n\n"
        "If a calculation is involved, show only the key steps. Do not mention that you are an AI. "
        "If you genuinely believe the answer key is wrong, still present it as the key and add a "
        "last line: 'Note: please confirm this with your teacher.'"
    )


def generate_text(prompt):
    """Returns (text, None) on success or (None, user-facing error message)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, "Explanations are not switched on yet."
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    try:
        resp = requests.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
            },
            timeout=30,
        )
    except requests.RequestException:
        logger.exception("Gemini request failed")
        return None, "Could not reach the explanation service. Please try again."

    if resp.status_code == 429:
        logger.warning("Gemini rate limit hit")
        return None, "Lots of students are asking at once — please try again in a minute."
    if resp.status_code != 200:
        logger.error("Gemini error HTTP %s: %s", resp.status_code, resp.text[:300])
        return None, "The explanation service had a problem. Please try again later."

    try:
        parts = resp.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError, TypeError):
        logger.error("Unexpected Gemini response: %s", resp.text[:300])
        return None, "The explanation service returned something unexpected. Please try again."
    if not text:
        return None, "No explanation was produced. Please try again."
    return text, None


def _over_student_limit(student_id):
    now = time.time()
    recent = _recent_by_student[student_id]
    while recent and now - recent[0] > 3600:
        recent.popleft()
    return len(recent) >= MAX_NEW_PER_STUDENT_PER_HOUR


def explanation_for(question, student_id):
    """Returns (text, None) or (None, error message). Reuses the saved
    explanation when there is one; otherwise generates and saves it."""
    if question.ai_explanation:
        return question.ai_explanation, None
    if not (question.text or "").strip():
        return None, "This question wasn't entered on screen, so an explanation can't be generated."
    if not ai_configured():
        return None, "Explanations are not switched on yet."

    with _question_locks[question.id]:
        db.session.refresh(question)  # another student may have just generated it
        if question.ai_explanation:
            return question.ai_explanation, None

        if _over_student_limit(student_id):
            return None, "You've asked for a lot of new explanations this hour — please try again later."
        if not _generation_slots.acquire(blocking=False):
            return None, "Lots of students are asking at once — please try again in a few seconds."
        try:
            text, error = generate_text(_build_prompt(question))
        finally:
            _generation_slots.release()
        if error:
            return None, error

        _recent_by_student[student_id].append(time.time())
        question.ai_explanation = text
        db.session.commit()
        return text, None
