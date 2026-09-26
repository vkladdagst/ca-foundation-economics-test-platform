"""Brief "why is this the answer?" explanations for students' doubts.

Uses Google's Gemini API (free tier, no card). Configured by env vars:
  GEMINI_API_KEY  -- required; unset = feature is off and the app is unchanged
  GEMINI_MODEL    -- optional override, defaults to DEFAULT_MODEL

Two ways an explanation gets made, both saved on the question row so the free
quota is spent once per question:
  * In the background, ahead of time ("prepare"): patient, with long timeouts
    and retries, so students later just read saved text instantly.
  * Live, when a student taps the button on a question that has no saved text
    yet: strictly time-boxed so nobody waits long.
"""
import logging
import os
import queue
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


class _SampleQuestion:
    """A real-looking question for the Settings connection test, so the test
    exercises the same prompt (and roughly the same speed) students will hit."""
    text = "Human wants are _____ in response to satisfying their wants."
    correct_answer = "B"
    _options = {"A": "Limited", "B": "Unlimited", "C": "Scarce", "D": "Multiple"}

    def option_text(self, letter):
        return self._options.get(letter)


def sample_prompt():
    return _build_prompt(_SampleQuestion())


def check_explanation(question):
    """Automatic sanity checks on a saved explanation. Returns a list of
    plain-English warnings (empty = nothing suspicious). This cannot judge
    whether the economics is right -- it only catches the mistakes a machine
    can see, so a person can focus their reading on the flagged ones."""
    text = question.ai_explanation or ""
    if not text.strip():
        return ["no explanation yet"]
    flags = []
    match = re.search(r"Answer:\s*\(?([A-D])\)?", text)
    if not match:
        flags.append("does not start with 'Answer: (X)'")
    elif match.group(1) != (question.correct_answer or "").upper():
        flags.append(f"says the answer is ({match.group(1)}) but your key is ({question.correct_answer})")
    if "confirm this with your teacher" in text.lower():
        flags.append("the AI doubts your answer key: please check this question")
    missing = [
        letter for letter in "ABCD"
        if letter != (question.correct_answer or "").upper() and question.option_text(letter) and f"({letter})" not in text
    ]
    if missing:
        flags.append("does not discuss option " + ", ".join(missing))
    if len(text.split()) > 200:
        flags.append("longer than intended")
    return flags


# ---------------------------------------------------------------- Gemini calls

last_failure = ""
_RETRYABLE = {500, 502, 503, 504}      # Google-side hiccups worth another try
_BUSY_COOLDOWN = 120                   # first time a model is busy: skip it for 2 minutes...
_BUSY_COOLDOWN_MAX = 900               # ...doubling each time it stays busy, up to 15 minutes
_UNAVAILABLE_COOLDOWN = 1800           # skip a retired / no-quota model for 30 minutes
_MODES = {
    # A student is waiting: give up quickly (server request limit is 60s).
    "live": dict(budget=35, per_call=20, max_calls=5, alone_retries=1, alone_sleep=1.5, fallbacks=3),
    # Nobody is waiting: be patient, this only costs background time.
    "patient": dict(budget=180, per_call=30, max_calls=8, alone_retries=3, alone_sleep=10, fallbacks=5),
}
_model_cache = {"names": [], "at": 0.0}
_last_good = None                      # the model that most recently worked: tried first
_cooldown = {}                         # model -> time.time() until which to skip it
_busy_streak = {}                      # model -> how many busy answers in a row
_activity = {"ok_at": None, "ok_seconds": None, "ok_model": "", "fail_at": None, "fail_text": ""}
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
            params={"pageSize": 100}, timeout=6,
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


def _post_generate(model, api_key, prompt, timeout):
    return requests.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
        },
        timeout=timeout,
    )


def _summarise(notes):
    """notes: list of (model, kind). -> 'model-a: busy x3 | model-b: not available'"""
    order, counts = [], {}
    for model, kind in notes:
        if (model, kind) not in counts:
            order.append((model, kind))
        counts[(model, kind)] = counts.get((model, kind), 0) + 1
    return " | ".join(f"{m}: {k}" + (f" x{counts[(m, k)]}" if counts[(m, k)] > 1 else "") for m, k in order)


def _kind(status, resp):
    if status is None:
        return "no answer in time"
    if status in _RETRYABLE:
        return f"busy ({status})"
    if status == 404:
        return "not available (404)"
    if status == 429:
        return "no free quota / rate limit (429)"
    return f"HTTP {status} {_google_error_message(resp)}"


def _cooling(model):
    return _cooldown.get(model, 0) > time.time()


def generate_text(prompt, patient=False):
    """Returns (text, None) on success or (None, user-facing error message).

    Fast in the normal case (one call to the model that last worked). If a
    model is busy, retired or has no free quota, it is put on a short "skip"
    list and the next current Gemini flash model is tried. `patient=True` is
    for background work and allows minutes instead of seconds. On failure the
    technical reasons are kept in `last_failure` for the teacher's Settings
    page (never shown to students)."""
    global last_failure, _last_good
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, "Explanations are not switched on yet."

    cfg = _MODES["patient" if patient else "live"]
    primary = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    first = next((m for m in (_last_good, primary) if m and not _cooling(m)), None)
    if first is None:
        first = next((m for m in _available_models(api_key) if not _cooling(m)), primary)
    queue_ = [first]
    extended = False
    alone_left = cfg["alone_retries"]
    notes = []
    saw_busy = False
    last_busy = None
    status = None
    started = time.time()
    calls = 0

    while queue_ and calls < cfg["max_calls"]:
        remaining = cfg["budget"] - (time.time() - started)
        if remaining < 4:
            break
        model = queue_.pop(0)
        calls += 1
        try:
            resp = _post_generate(model, api_key, prompt, min(cfg["per_call"], remaining))
        except requests.RequestException as exc:
            logger.warning("Gemini request failed on %s: %s", model, exc)
            status = None
            resp = None
            notes.append((model, _kind(None, None)))
        else:
            status = resp.status_code
            if status == 200:
                try:
                    parts = resp.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts).strip()
                except (KeyError, IndexError, ValueError, TypeError):
                    text = ""
                if text:
                    _last_good = model
                    _busy_streak.pop(model, None)
                    _activity.update(ok_at=time.time(), ok_seconds=round(time.time() - started, 1), ok_model=model)
                    return text, None
                notes.append((model, "empty or unexpected answer"))
            else:
                notes.append((model, _kind(status, resp)))

        last_failure = _summarise(notes)
        if status in (400, 401, 403):   # bad key / not allowed: no model will do better
            break
        if status is None or status in _RETRYABLE:
            saw_busy = True
            last_busy = model
            streak = _busy_streak.get(model, 0) + 1
            _busy_streak[model] = streak
            _cooldown[model] = time.time() + min(_BUSY_COOLDOWN * 2 ** (streak - 1), _BUSY_COOLDOWN_MAX)
        elif status != 200:
            # 404 (retired) or 429 (no quota / rate limit). A 200 with no text
            # is just one odd answer, not a reason to shun the whole model.
            _cooldown[model] = time.time() + _UNAVAILABLE_COOLDOWN
        if _last_good == model:
            _last_good = None

        if not extended:
            extended = True
            others = [m for m in [primary] + _available_models(api_key) if m != model]
            healthy = [m for m in dict.fromkeys(others) if not _cooling(m)]
            queue_ += healthy[:cfg["fallbacks"]]
        if not queue_ and last_busy and alone_left > 0:
            alone_left -= 1             # nothing else to try: give a busy model another go
            time.sleep(cfg["alone_sleep"])
            queue_.append(last_busy)

    logger.error("Gemini failed: %s", last_failure)
    _activity.update(fail_at=time.time(), fail_text=last_failure)
    if status in (400, 401, 403):
        return None, "The explanation service had a problem. Please try again later."
    if saw_busy or status == 429:
        return None, "The explanation service is busy right now. Please try again in a minute."
    return None, "The explanation service had a problem. Please try again later."


def _ago(ts):
    if not ts:
        return "never"
    seconds = int(time.time() - ts)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def status_snapshot():
    """For the teacher's Settings page."""
    a = _activity
    return {
        "last_ok": (f"{_ago(a['ok_at'])} — took {a['ok_seconds']}s ({a['ok_model']})" if a["ok_at"] else "no successful call yet"),
        "last_fail": (f"{_ago(a['fail_at'])} — {a['fail_text']}" if a["fail_at"] else ""),
    }


# ------------------------------------------------------------ live (student) use

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
        db.session.refresh(question)  # another student (or the background job) may have just made it
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


# ------------------------------------------------- background preparation

_PACE_SECONDS = 6              # gentle on the free tier's per-minute limit
_ABORT_AFTER_FAILURES = 5      # this many in a row: give up on this test for now
_FAILURE_PAUSE_SECONDS = 90

_prep_queue = queue.Queue()
_prep_queued = set()
_prep_lock = threading.Lock()
_prep_thread = None
_prep = {"current": "", "done": 0, "failed": 0, "last_error": ""}


def prep_status():
    return dict(_prep, waiting=len(_prep_queued), active=bool(_prep_thread and _prep_thread.is_alive() and _prep_queued))


def enqueue_tests(app, test_ids):
    """Queue tests whose questions should get explanations prepared in the
    background. Returns how many tests were newly queued (0 if the AI is off)."""
    global _prep_thread
    if not ai_configured():
        return 0
    added = 0
    with _prep_lock:
        for test_id in test_ids:
            if test_id not in _prep_queued:
                _prep_queued.add(test_id)
                _prep_queue.put(test_id)
                added += 1
        if added and not (_prep_thread and _prep_thread.is_alive()):
            _prep_thread = threading.Thread(target=_prep_worker, args=(app,), daemon=True)
            _prep_thread.start()
    return added


def _prep_worker(app):
    while True:
        try:
            test_id = _prep_queue.get(timeout=600)
        except queue.Empty:
            return                      # idle for 10 minutes: let the thread end; it restarts when needed
        try:
            with app.app_context():
                _prepare_test(test_id)
        except Exception:
            logger.exception("Preparing explanations failed for test %s", test_id)
        finally:
            with _prep_lock:
                _prep_queued.discard(test_id)
            try:
                db.session.remove()
            except Exception:
                pass


def _prepare_test(test_id):
    from app.models import Question

    ids = [
        q.id for q in Question.query.filter(Question.test_id == test_id).order_by(Question.order_index)
        if (q.text or "").strip() and not q.ai_explanation
    ]
    _prep["current"] = f"test {test_id}"
    in_a_row = 0
    retried = set()
    pending = list(ids)
    while pending:
        qid = pending.pop(0)
        with _question_locks[qid]:
            question = db.session.get(Question, qid)
            if question is None or question.ai_explanation:
                continue
            text, error = generate_text(_build_prompt(question), patient=True)
            if text:
                question.ai_explanation = text
                db.session.commit()
                _prep["done"] += 1
                in_a_row = 0
            else:
                db.session.rollback()
                _prep["failed"] += 1
                _prep["last_error"] = last_failure or error
                in_a_row += 1
                if qid not in retried:
                    retried.add(qid)
                    pending.append(qid)      # one more go, after the others
        db.session.remove()
        if in_a_row >= _ABORT_AFTER_FAILURES:
            time.sleep(_FAILURE_PAUSE_SECONDS)   # the service is struggling: rest, then move on
            break
        time.sleep(_PACE_SECONDS)
    _prep["current"] = ""
