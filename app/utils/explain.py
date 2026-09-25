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
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash"

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


last_failure = ""
_RETRYABLE = {500, 502, 503, 504}      # Google-side hiccups worth another try
_MAX_ATTEMPTS = 4
_TOTAL_BUDGET_SECONDS = 40             # stay well inside the 60s request limit
_model_cache = {"names": [], "at": 0.0}


def _google_error_message(resp):
    try:
        return resp.json()["error"]["message"]
    except (KeyError, ValueError, TypeError):
        return resp.text[:200]


def _available_models(api_key):
    """Current text models this key can use, best first: stable 'flash' models
    (newest name first), then 'flash-lite' ones as a last resort. Model names
    get retired or overloaded over time, so we ask Google instead of hardcoding."""
    if _model_cache["names"] and time.time() - _model_cache["at"] < 3600:
        return _model_cache["names"]
    try:
        resp = requests.get(
            GEMINI_MODELS_URL, headers={"x-goog-api-key": api_key},
            params={"pageSize": 100}, timeout=15,
        )
        if resp.status_code != 200:
            return []
        flash, lite = [], []
        for m in resp.json().get("models", []):
            name = m.get("name", "").split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []) or "flash" not in name:
                continue
            if any(bad in name for bad in ("image", "tts", "live", "audio", "preview", "exp", "thinking")):
                continue
            (lite if "lite" in name else flash).append(name)
        names = sorted(flash, reverse=True) + sorted(lite, reverse=True)
        _model_cache.update(names=names, at=time.time())
        return names
    except (requests.RequestException, ValueError):
        logger.exception("Gemini model discovery failed")
        return []


def _post_generate(model, api_key, prompt):
    return requests.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
        },
        timeout=15,
    )


def generate_text(prompt):
    """Returns (text, None) on success or (None, user-facing error message).

    Copes with a busy or retired model: retries, and switches to another
    current Gemini model. On failure the technical reason is kept in
    `last_failure` for the teacher's Settings test (never shown to students)."""
    global last_failure
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, "Explanations are not switched on yet."

    models = [os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL]
    started = time.time()
    resp = None
    pos = 0
    for attempt in range(_MAX_ATTEMPTS):
        model = models[pos % len(models)]
        try:
            resp = _post_generate(model, api_key, prompt)
            failure = None
        except requests.RequestException as exc:
            resp = None
            failure = f"Could not reach Google: {exc}"
            logger.warning("Gemini request failed on %s: %s", model, exc)

        if resp is not None and resp.status_code == 200:
            break
        if resp is not None:
            failure = f"HTTP {resp.status_code} ({model}): {_google_error_message(resp)}"
        last_failure = failure

        gone = resp is not None and resp.status_code == 404
        transient = resp is None or resp.status_code in _RETRYABLE
        if not (gone or transient):
            break
        if time.time() - started > _TOTAL_BUDGET_SECONDS:
            break
        # Widen the list of models to fall back on (first time only).
        widened = False
        if len(models) == 1:
            extras = [m for m in _available_models(api_key) if m != models[0]][:2]
            if extras:
                models = extras + models if gone else models + extras
                widened = gone  # retired: start over from the best replacement
        pos = 0 if widened else pos + 1
        if attempt < _MAX_ATTEMPTS - 1 and transient:
            time.sleep(1.5)
    else:
        resp = None

    if resp is None or resp.status_code != 200:
        if resp is not None and resp.status_code == 429:
            return None, "Lots of students are asking at once — please try again in a minute."
        logger.error("Gemini failed: %s", last_failure)
        if resp is None or resp.status_code in _RETRYABLE:
            return None, "The explanation service is busy right now. Please try again in a minute."
        return None, "The explanation service had a problem. Please try again later."

    try:
        parts = resp.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError, TypeError):
        last_failure = f"Unexpected response: {resp.text[:200]}"
        logger.error(last_failure)
        return None, "The explanation service returned something unexpected. Please try again."
    if not text:
        last_failure = "Google returned an empty answer."
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
