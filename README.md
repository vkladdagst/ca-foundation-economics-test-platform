# CA Foundation Economics — Test Management & Auto-Evaluation Platform

A lightweight web app for running MCQ tests where students mark answers online
(often against a printed question paper) and get instantly, automatically
graded — with negative marking, Excel exports, and per-question analytics.

## Why this stack

The machine this was built on has Node.js 6.10.1 (from 2017) — too old for
any modern JS framework — but a current Python 3.14 install. Rather than
fight the environment, the app is built as a **server-rendered Flask app**:

- **Flask + SQLite (via SQLAlchemy)** — zero-config database that's just a
  file; no separate DB server to install or maintain, which matters for a
  small institute running this on a laptop or a small VPS.
- **Server-rendered Jinja2 templates + a little vanilla JS** — no build step,
  no npm, no bundler. One `pip install`, and it runs.
- **openpyxl** for reading/writing `.xlsx` — no heavier dependency needed.
- **Flask-Login** for teacher sessions, **Flask-WTF** for CSRF protection.

This keeps the whole stack to seven small dependencies, deployable anywhere
that runs Python (a school's own server, PythonAnywhere, Render, Railway, a
Windows machine via `waitress`, etc.).

All grading is done **server-side only** — the client never computes or
sends a score. Question correct-answers are never sent to the student's
browser when `questions_visible` is off, and are never included in any
response the student's browser can read.

## 1. Install

```bash
python -m venv venv
```

Activate it:
- Windows (PowerShell): `venv\Scripts\Activate.ps1`
- Windows (Git Bash): `source venv/Scripts/activate`
- macOS/Linux: `source venv/bin/activate`

```bash
pip install -r requirements.txt
```

## 2. Configure environment variables

Copy `.env.example` to `.env` and edit as needed (optional — sensible
defaults are baked in for local use):

```bash
cp .env.example .env
```

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Flask session signing key — **set a real random value in production** | dev key (insecure) |
| `DATABASE_URL` | SQLAlchemy DB URI | `sqlite:///instance/econ_test.db` |

## 3. Initialize the database & seed demo data

The database schema is created automatically the first time the app runs.
To also load a ready-to-explore demo test:

```bash
python seed.py
```

This creates:
- A demo teacher account
- **"CA Foundation Economics – Demo Test"** with 12 MCQs, negative marking
  (−0.25/wrong), and a full answer key
- 6 sample student submissions with a realistic mix of correct, wrong, and
  unanswered responses, already graded

Safe to re-run — it replaces only the demo test it owns.

## 4. Run locally

```bash
python run.py
```

By default this runs on port 5000. If that port is already in use on your
machine (as it was during development here — a background service had
claimed it), override it:

```bash
PORT=8077 python run.py            # bash
$env:PORT=8077; python run.py      # PowerShell
```

Open **http://127.0.0.1:5000** (or your chosen port).

## 5. Demo credentials

| Role | Login |
|---|---|
| Teacher | `demo@institute.com` / `Demo@1234` |
| Student | Open `/test/ECONDEMO` — no login needed |

## 6. Core workflow

### As a teacher
1. Log in → **Dashboard**.
2. **+ New Test** → fill in name, subject, marking scheme, negative marking,
   whether questions should be visible on screen, which student fields are
   required, then **Create Test & Add Questions**.
3. On the **Questions** page: add questions manually, or **Import from
   Excel** (download the template first — it shows the exact expected
   columns). Bad rows are rejected with clear messages before anything is
   imported.
4. **Export Questions** / **Export Answer Key** any time — professional
   `.xlsx` output, ready for Excel or Google Sheets.
5. **Publish Test** — this generates/activates the access code and student
   link, shown at the top of the Questions page with a **Copy Link** button.
6. Share the link or code (e.g. `/test/ECONDEMO`) with students.
7. Once students submit, open **Results** for live stats (attempted,
   average, highest, lowest), a sortable/searchable student table, and
   **Question Analytics** for per-question accuracy.
8. Click any student row → full question-by-question breakdown (their
   answer, correct answer, status, marks gained/lost).
9. **Export Results (Excel)** for a ranked scoreboard, or **Export Detailed
   Responses** for one row per student per question (analysis-friendly).
10. Found an error in the answer key after students already submitted? Fix
    the question's correct answer on the Questions page, then hit
    **Recalculate Results** on the Results page — every submission is
    re-graded from the current answer key, and the page shows when it was
    last recalculated.
11. **Duplicate** any test from the Tests list to reuse its questions and
    settings for the next test — edit only what changed.

### As a student
1. Open the link/code your teacher shared (e.g. `/test/ECONDEMO`).
2. Enter name + whichever fields the teacher required (roll number, batch,
   etc.).
3. Mark answers using the tap-friendly A/B/C/D tiles for each question
   (question text/options only appear if the teacher enabled it — otherwise
   it's a pure OMR-style answer sheet for use alongside a printed paper).
   Use the question navigator to jump around; answered questions turn green.
   Every tap autosaves immediately (and retries from local storage if the
   connection drops).
4. **Review & Submit** — see how many questions are answered/unanswered,
   confirm.
5. Get a unique reference number, and (if the teacher enabled it) an
   instant score.

## 7. How evaluation works

On submission, the server (never the browser) walks every question in the
test, compares the student's stored answer to `Question.correct_answer`,
and applies:

- **Correct** → `+marks`
- **Wrong** → `−negative_marks`
- **Unanswered** → `0`

Total score, correct/wrong/unanswered counts, and percentage are stored on
the submission. **Recalculate Results** re-runs this exact logic for every
submitted student against the *current* answer key — so a post-hoc fix
never requires manual database edits, and nothing is silently lost (the
recalculation timestamp is always visible).

## 8. Excel import/export

- **Question import template**: `Question No, Question, Option A–D, Correct
  Answer, Marks, Negative Marks, Explanation`. Import validates every row
  (valid A–D answer, at least one option filled, numeric marks) and reports
  every problem before touching the database — nothing is partially
  imported.
- **Answer key export**: a clean, print-ready `Q. No. / Correct Answer /
  Marks / Negative Marks` sheet.
- **Results export**: ranked scoreboard with correct/wrong/unanswered,
  score, percentage.
- **Detailed responses export**: one row per student per question, ideal
  for pivot-table analysis in Excel or Sheets.

All exports are standard `.xlsx` (openpyxl), opening cleanly in both
Microsoft Excel and Google Sheets.

## 9. Security notes

- Teacher routes require login (`Flask-Login`) and every test/question/
  result lookup is scoped to `current_user` — one teacher cannot see or
  edit another teacher's tests (403 if attempted).
- All forms are CSRF-protected (`Flask-WTF`), including the student
  autosave endpoint (token sent via header).
- Scoring is 100% server-side; nothing the client sends can affect marks.
- SQL access goes through the SQLAlchemy ORM (parameterized queries) —
  no raw string SQL anywhere.
- Duplicate submissions are blocked at the database level (checked by
  roll number + test, not just a cookie) when "one attempt only" is on —
  this holds even if a student switches browsers or devices.
- Access codes are random 8-character tokens, not sequential IDs.

## 10. Deployment

For anything beyond local testing, run behind a production WSGI server
instead of Flask's dev server:

```bash
pip install waitress   # already in requirements.txt
python -c "from waitress import serve; from run import app; serve(app, host='0.0.0.0', port=8000)"
```

Or on Linux, `gunicorn -w 4 -b 0.0.0.0:8000 run:app` (install `gunicorn`
separately — it doesn't work on Windows).

Set a strong, random `SECRET_KEY` and point `DATABASE_URL` at a persistent
path (or swap to Postgres/MySQL by changing the URL — SQLAlchemy supports
it with no code changes) before going live. Any host that runs Python works:
PythonAnywhere, Render, Railway, a school's own Linux server, etc.

## 11. Project structure

```
app/
  __init__.py          # app factory
  models.py             # Teacher, Test, Question, Submission, Response
  auth.py                # teacher login/logout
  teacher/routes.py    # dashboard, test/question CRUD, results, exports
  student/routes.py    # public test-taking flow
  utils/
    grading.py          # server-side evaluation engine
    excel_io.py         # import/export helpers (openpyxl)
templates/               # Jinja2 templates (teacher/, student/, auth/)
static/                   # CSS + vanilla JS (student answer-sheet interactivity)
seed.py                    # demo data generator
run.py                     # entry point
```

## 12. What was tested

Verified end-to-end in this environment (see conversation for details):
teacher login, test creation, Excel question import (including rejecting a
malformed row before import), publish/unpublish, student attempt flow on
desktop and a 375px mobile viewport, autosave, review screen, submission,
server-side grading (including negative marking — spot-checked the exact
arithmetic), duplicate-submission blocking from a fresh session using the
same roll number, unauthenticated access correctly redirected away from
every teacher route, answer-key edit + Recalculate Results updating a
previously-submitted score, all five Excel exports (parsed back with
openpyxl to confirm valid structure), and test duplication.

## 13. Known limitations (by design, for this MVP)

- No OCR of uploaded question papers (P2 — upload/store/display only, as
  specified).
- No student login accounts — access is via test link/code, matching the
  "hard-copy paper + online answer sheet" workflow this was built for.
- Client-side countdown timer is informational only; it does not
  force-submit when time runs out (avoids ever silently losing a student's
  answers due to a client clock or network hiccup).
- Single-teacher-per-test-ownership model — no shared/multi-teacher test
  editing in this version (noted as a natural extension in the prompt's
  "future extensibility" section).

## 14. Suggested next steps

- Add a `waitress`-based `serve.py` for one-command production start on
  Windows.
- Add topic/chapter tags to questions for cross-test performance trends.
- Optional teacher password reset / multi-teacher accounts if the institute
  grows beyond one faculty member using the platform.
