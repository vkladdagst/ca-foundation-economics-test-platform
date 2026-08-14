# CA Foundation Economics — Test Management & Auto-Evaluation Platform

A lightweight web app for running MCQ tests where students mark answers online
(often against a printed question paper) and get instantly, automatically
graded — with negative marking, student login, Excel exports, per-question
analytics, and optional email notifications.

## Why this stack

The machine this was built on has Node.js 6.10.1 (from 2017) — too old for
any modern JS framework — but a current Python 3.14 install. Rather than
fight the environment, the app is built as a **server-rendered Flask app**:

- **Flask + SQLite locally / Postgres in production (via SQLAlchemy)** —
  SQLite is zero-config for local use; in production the same code talks to
  a managed Postgres database over `DATABASE_URL` with no code changes.
- **Server-rendered Jinja2 templates + a little vanilla JS** — no build step,
  no npm, no bundler. One `pip install`, and it runs.
- **openpyxl** for reading/writing `.xlsx` — no heavier dependency needed.
- **Flask-Login** for both teacher and student sessions, **Flask-WTF** for
  CSRF protection.
- **pg8000** as the Postgres driver — pure Python, no C build step, so it
  installs cleanly on any machine (unlike `psycopg2`, which needs compiler
  tooling this machine didn't have).

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

Copy `.env.example` to `.env` and edit as needed (optional for local use —
sensible defaults are baked in):

```bash
cp .env.example .env
```

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Flask session signing key — **set a real random value in production** | dev key (insecure) |
| `DATABASE_URL` | SQLAlchemy DB URI (SQLite locally, Postgres in production) | `sqlite:///instance/econ_test.db` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | Email notifications (optional) | unset = email disabled, app works fine without it |

## 3. Initialize the database

The schema is created automatically the first time the app runs. You have
two ways to get your first login:

**Option A — demo data (fastest way to explore the app):**
```bash
python seed.py
```
Creates a demo teacher, a full demo test with 12 MCQs and graded sample
submissions, and a 7-student roster (see Demo Credentials below). Safe to
re-run — it replaces only the demo records it owns.

**Option B — your own account (for real use):**
Skip `seed.py` and open `/setup` in the browser after starting the app —
it's a one-time account-creation page that **disables itself** the moment
any teacher account exists, so it can't be used to create rogue accounts
later. This is how a fresh production deployment gets its first login
without needing shell/console access on the host.

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

To test production-like behavior locally (no debug/reloader, real WSGI
server): `python serve.py` instead.

## 5. Demo credentials

| Role | Login |
|---|---|
| Teacher | `demo@institute.com` / `Demo@1234` |
| Student | Roll number `ECO101`–`ECO107` / `Student@123` (log in at `/student/login`) |

`ECO107` has no submission yet, to demo the "not attempted → Start" state.
The other six have graded results with a realistic mix of correct, wrong,
and unanswered responses.

## 6. Core workflow

### As a teacher
1. Log in → **Dashboard**.
2. **Students** → add your roster before creating tests: one at a time, or
   bulk **Import from Excel** (roll number, name, batch, email, mobile). A
   login password is generated automatically for each student and shown
   **once**, on screen and as an Excel download — copy it now, it's never
   stored in readable form again. Use **Reset Password** later if a student
   loses theirs.
3. **+ New Test** → fill in name, subject, marking scheme, negative marking,
   whether questions should be visible on screen, whether to strictly
   enforce the timer, then **Create Test & Add Questions**.
4. On the **Questions** page: add questions manually, or **Import from
   Excel** (download the template first). Bad rows are rejected with clear
   messages before anything is imported.
5. **Export Questions** / **Export Answer Key** any time — professional
   `.xlsx` output, ready for Excel or Google Sheets.
6. **Publish Test** — activates the access link (e.g. `/test/ECON07`) and,
   if email is configured, notifies every roster student automatically.
7. Once students submit, open **Results** for live stats, a sortable/
   searchable student table, and **Question Analytics** for per-question
   accuracy. **Email Reminder to Non-Attempters** nudges anyone who hasn't
   submitted yet.
8. Click any student row → full question-by-question breakdown.
9. **Export Results (Excel)** or **Export Detailed Responses** (one row per
   student per question, analysis-friendly).
10. Found an error in the answer key after students already submitted? Fix
    the question's correct answer, then hit **Recalculate Results** — every
    submission is re-graded, and the page shows when it was last
    recalculated.
11. **Duplicate** any test to reuse its questions/settings for the next one.

### As a student
1. Log in at `/student/login` with the roll number and password your
   teacher gave you. This is what stops anyone else from submitting under
   your name, and lets you come back anytime — **My Tests** lists every
   test your teacher has published, with your result once you've attempted
   it.
2. Open a test → **Start Test**. Your name/roll number are already known
   from login, so there's no form to fill in.
3. Mark answers using the tap-friendly A/B/C/D tiles for each question
   (question text/options only appear if the teacher enabled it — otherwise
   it's a pure OMR-style answer sheet for use alongside a printed paper).
   Use the question navigator to jump around; answered questions turn green.
   Every tap autosaves immediately (and retries from local storage if the
   connection drops). If the teacher turned on the strict timer, the sheet
   locks itself the instant time runs out — no further answers are accepted
   client- or server-side.
4. **Review & Submit** — see how many questions are answered/unanswered,
   confirm.
5. Get a unique reference number, and (if the teacher enabled it) an
   instant score plus a **Review My Answers** link — see exactly which
   questions you got wrong and the correct answer, whenever you want to
   revise (if the teacher allowed review for that test).

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

## 8. The strict timer

A test's Duration is always shown to students as an on-screen countdown.
Turning on **"Enforce the duration strictly"** additionally makes it real:

- The server computes each student's personal deadline as
  `their own start time + duration` (not a shared clock for the whole
  class — everyone gets the full duration from when *they* started).
- Once that deadline passes, `/attempt` stops rendering the answer sheet at
  all — the student is bounced straight to the Review screen — and the
  `/answer` autosave endpoint independently rejects any further change.
  Both checks happen server-side, so a client clock, a paused tab, or
  editing the page can't extend the time.
- Whatever was answered before the deadline can still be submitted and
  graded normally; nothing is discarded.

Verified in this build by rewinding a live submission's start time and
confirming the server correctly refused new answers, redirected to review,
and still finalized the score from what had been answered.

## 9. Excel import/export

- **Question import template**: `Question No, Question, Option A–D, Correct
  Answer, Marks, Negative Marks, Explanation`.
- **Student roster import template**: `Roll Number, Name, Batch, Registration
  Number, Email, Mobile`.
- Both imports validate every row and report every problem before touching
  the database — nothing is partially imported. Roster import skips (and
  reports) roll numbers that already exist rather than overwriting them.
- **Answer key export**: a clean, print-ready sheet.
- **Results export**: ranked scoreboard. **Detailed responses export**: one
  row per student per question, ideal for pivot-table analysis.
- **Student login credentials export**: shown once, right after adding or
  importing students — the only time plaintext passwords ever exist outside
  a student's own memory.

All exports are standard `.xlsx` (openpyxl), opening cleanly in both
Microsoft Excel and Google Sheets.

## 10. Email notifications (optional)

Unset `SMTP_HOST` and the app works exactly as before — nothing breaks,
notification buttons just tell you email isn't configured instead of
sending. Set it up (see `.env.example` — any SMTP provider works, Gmail
example included) to get:

- **New test published** → every roster student with an email on file is
  notified automatically.
- **Student submits** → the teacher gets an email with the score, in
  addition to the dashboard updating live on next page load.
- **Email Reminder to Non-Attempters** → one click from the Results page
  to nudge everyone in the roster who hasn't submitted yet.

Sending is synchronous (fine at small-institute volume — tens to low
hundreds of students) and never crashes a request if SMTP fails; failures
are logged and reported as a skipped count, not an error page.

## 11. Security notes

- Teacher and student are genuinely separate account types (not just a
  role flag) — every route is gated by `@teacher_required` or
  `@student_required`, which checks both "is logged in" **and** "is the
  right kind of account." A logged-in student cannot reach any teacher
  route, and vice versa.
- Every test/question/result lookup is additionally scoped to
  `current_user` — one teacher cannot see or edit another teacher's tests
  (403 if attempted); one student cannot view another student's results.
- Student passwords are generated server-side (never chosen/guessable),
  hashed with Werkzeug's `generate_password_hash`, and shown in plaintext
  exactly once at creation time — never stored or displayed again.
- All forms are CSRF-protected (`Flask-WTF`), including the student
  autosave endpoint (token sent via header).
- Scoring and timer enforcement are 100% server-side; nothing the client
  sends can affect marks or bypass a strict deadline.
- SQL access goes through the SQLAlchemy ORM (parameterized queries) —
  no raw string SQL anywhere.
- One-attempt-only is enforced by a real foreign key (`student_id` +
  `test_id`), not a roll-number string match — holds even across browsers
  or devices, and is what stops one student submitting as another.
- Access codes are random 8-character tokens, not sequential IDs.
- `/setup` (first-account bootstrap) hard-404s the instant a teacher
  account exists — it cannot be used a second time.

## 12. Deployment

**This app was only ever run locally during development** — if you shared
the localhost link with anyone, it went down whenever the process stopped
(machine sleep, terminal closed, etc.). That's expected: `python run.py` is
a local dev server, not a hosted service. To get a link that's actually
reliable for students, deploy it. Recommended: **Render**, free tier to
start (a small always-on paid tier removes the free tier's cold-start delay
later, if you outgrow it).

**What you need to do yourself** (account creation and clicking "Deploy"
aren't things that can be automated on your behalf):

1. **Push this repo to your own GitHub account.** A local git repo is
   already initialized and committed here — create an empty repo on
   GitHub, then:
   ```bash
   git remote add origin https://github.com/<you>/<repo-name>.git
   git push -u origin main
   ```
2. **Create a free Render account** at render.com (sign in with GitHub is
   easiest).
3. **New → PostgreSQL** — free tier. Once created, copy its **Internal
   Database URL**.
4. **New → Web Service** → connect the GitHub repo you just pushed.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn run:app`
   - Add environment variables:
     - `SECRET_KEY` — generate any long random string
     - `DATABASE_URL` — paste the Postgres Internal Database URL from step 3
     - (optional) the `SMTP_*` variables from `.env.example` if you want
       email notifications live
5. Deploy. Once it's up, open `https://<your-app>.onrender.com/setup` to
   create your real teacher account (see section 3, Option B) — you do not
   need shell access, and `seed.py` is only for local demo purposes.
6. Share `https://<your-app>.onrender.com/student/login` with students, and
   `https://<your-app>.onrender.com/login` for yourself.

**Free-tier caveat:** Render's free web service sleeps after ~15 minutes of
no traffic; the first request after that takes ~30-60 seconds to wake up
(subsequent requests are instant). If that delay matters for a live test
session, upgrade the web service to a paid always-on plan (~$7/month) —
no other change needed. The database recommendation above already avoids
the bigger risk: **never store real student data in SQLite on free-tier
Render** — its disk isn't guaranteed to persist across redeploys, which
would silently lose submitted results. Postgres, as set up above, persists
properly.

## 13. Custom link / domain

Once deployed, your app already has a stable link
(`https://<your-app>.onrender.com`) instead of a localhost port — that
alone fixes the reliability problem. If you want your own domain (e.g.
`tests.yourinstitute.com`) on top of that, buy the domain from any
registrar and point it at Render under the service's **Settings → Custom
Domains** — Render's own docs walk through the DNS step, and it works on
the free tier too. This is optional and adds a small yearly domain cost;
the Render subdomain works fine without it.

## 14. Project structure

```
app/
  __init__.py           # app factory
  models.py              # Teacher, Student, Test, Question, Submission, Response
  auth.py                 # teacher login/logout + /setup bootstrap
  student_auth.py        # student login/logout/portal
  decorators.py           # @teacher_required / @student_required
  teacher/routes.py     # dashboard, test/question/roster CRUD, results, exports
  student/routes.py     # login-gated test-taking flow
  utils/
    grading.py           # server-side evaluation engine
    excel_io.py          # import/export helpers (openpyxl)
    mail.py               # SMTP notification helper
templates/                # Jinja2 templates (teacher/, student/, auth/)
static/                    # CSS + vanilla JS (student answer-sheet interactivity)
seed.py                     # demo data generator
run.py                      # local dev entry point
serve.py                    # local production-mode entry point (waitress)
Procfile                    # `gunicorn run:app` — used by Render/Heroku-style hosts
runtime.txt                 # pins Python 3.12 for the deploy target
```

## 15. What was tested

Verified end-to-end in this environment: teacher login, roster management
(add/edit/reset-password/bulk import with duplicate-skip logic), test
creation, Excel question import (including rejecting a malformed row before
import), publish/unpublish (with email-notify path both configured-off and
mock-verified-on), student login and portal, the full login-gated attempt
flow, autosave, review, submission, server-side grading (negative marking
arithmetic spot-checked by hand), strict-timer enforcement (simulated an
expired deadline on a live submission and confirmed the server refused
further answers while still finalizing the score), student self-review of
past answers with per-student access isolation, duplicate-submission
blocking, unauthenticated **and** wrong-account-type access correctly
redirected away from every protected route, answer-key edit + Recalculate
Results updating a previously-submitted score, all Excel exports (parsed
back with openpyxl to confirm valid structure), test duplication, the
`/setup` first-account bootstrap (confirmed it works on an empty database
and 404s once an account exists), and the email-sending code path via a
mocked SMTP server (STARTTLS, auth, message construction, bulk skip
counting all verified).

## 16. Known limitations (by design, for this version)

- No OCR of uploaded question papers (P2 — upload/store/display only, as
  originally specified).
- Email sending is synchronous — fine at small-institute volume, but would
  need a background queue (e.g. Celery) if the roster grew into the
  thousands.
- Single-teacher-per-test-ownership model — no shared/multi-teacher test
  editing yet.
- No automated graphical revision-summary content yet — parked pending
  further discussion on what it should actually show (auto-generated
  performance charts by chapter vs. teacher-authored study material are
  quite different features).
- WhatsApp notifications not built — requires a paid WhatsApp Business API
  (Meta or Twilio) with business verification; email was built first as
  the free/simple option per your preference.

## 17. Recommended next steps

- Decide on the revision-content feature (section 16) so it can be scoped.
- If WhatsApp notifications become worth the cost/setup, it's a fairly
  contained addition alongside the existing email code in
  `app/utils/mail.py`.
- Add topic/chapter tags to questions — this becomes the foundation for
  both cross-test performance trends and any future auto-generated
  revision charts.
- Consider a custom domain once the Render deployment is live and stable
  (section 13).
