"""On-demand "why is this option right and the others wrong" explanations.

Uses Google's Gemini API (free tier, no card). Configured by env vars:
  GEMINI_API_KEY  -- required; unset = feature is off and the app is unchanged
  GEMINI_MODEL    -- optional override, defaults to DEFAULT_MODEL

Each question is explained once and the text is saved on the question row, so
the free-tier quota is only spent on the first student to ask about it.
"""
import logging
import os
import re
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
_MAX_CALLS = 6
_TOTAL_BUDGET_SECONDS = 40             # stay well inside the 60s request limit
_MAX_FALLBACKS = 3
_model_cache = {"names": [], "at": 0.0}
_PLAIN_FLASH = re.compile(r"^gemini-(\d+(?:\.\d+)*)-flash(-lite)?$")


def _google_error_message(resp):
    try:
        message = resp.json()["error"]["message"]
    except (KeyError, ValueError, TypeError):
        message = resp.text
    return message.split(". ")[0].splitlines()[0][:110] if message.strip() else ""


def _available_models(api_key):
    """Ordinary Gemini 'flash' text models this key can list, best first:
    newest version first, 'flash-lite' after the full-size ones. Anything that
    isn't a plain gemini-<version>-flash[-lite] (omni, preview, image, live,
    pro...) is ignored -- those often have no free-tier quota at all."""
    if _model_cache["names"] and time.time() - _model_cache["at"] < 3600:
        return _model_cache["names"]
    try:
        resp = requests.get(
            GEMINI_MODELS_URL, headers={"x-goog-api-key": api_key},
            params={"pageSize": 100}, timeout=15,
        )
        if resp.status_code != 200:
            return []
        found = []
        for m in resp.json().get("models", []):
            name = m.get("name", "").split("/")[-1]
            match = _PLAIN_FLASH.match(name)
            if match and "generateContent" in m.get("supportedGenerationMethods", []):
                version = tuple(int(part) for part in match.group(1).split("."))
                found.append((bool(match.group(2)), tuple(-v for v in version), name))
        names = [name for _, _, name in sorted(found)]
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

    Copes with a busy, retired, or quota-less model: retries a busy one once,
    then moves on to other current Gemini flash models. On failure the
    technical reasons are kept in `last_failure` for the teacher's Settings
    test (never shown to students)."""
    global last_failure
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, "Explanations are not switched on yet."

    primary = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    queue = [primary, primary]          # second entry = one retry if it's just busy
    fallbacks_added = False
    notes = []
    saw_busy = False
    started = time.time()
    calls = 0

    while queue and calls < _MAX_CALLS and time.time() - started < _TOTAL_BUDGET_SECONDS:
        model = queue.pop(0)
        calls += 1
        try:
            resp = _post_generate(model, api_key, prompt)
        except requests.RequestException as exc:
            logger.warning("Gemini request failed on %s: %s", model, exc)
            resp, status = None, None
            notes.append(f"{model}: could not reach Google")
        else:
            status = resp.status_code
            if status == 200:
                try:
                    parts = resp.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts).strip()
                except (KeyError, IndexError, ValueError, TypeError):
                    text = ""
                if text:
                    return text, None
                notes.append(f"{model}: empty or unexpected answer")
            else:
                notes.append(f"{model}: HTTP {status} {_google_error_message(resp)}")

        last_failure = " | ".join(notes)
        if status in (400, 401, 403):   # bad key / not allowed: no model will do better
            break
        if status is None or status in _RETRYABLE:
            saw_busy = True
            if queue and queue[0] == model:
                time.sleep(1.5)
        else:
            # 404 (retired), 429 (no quota / rate limit) or an odd answer:
            # retrying the same model is pointless.
            queue = [m for m in queue if m != model]
        if not fallbacks_added:
            fallbacks_added = True
            queue += [m for m in _available_models(api_key) if m != primary][:_MAX_FALLBACKS]

    logger.error("Gemini failed: %s", last_failure)
    if status in (400, 401, 403):
        return None, "The explanation service had a problem. Please try again later."
    if saw_busy or (status == 429):
        return None, "The explanation service is busy right now. Please try again in a minute."
    return None, "The explanation service had a problem. Please try again later."


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
