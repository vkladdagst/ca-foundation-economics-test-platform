"""Server-side authoritative grading engine.

Nothing about a student's score is ever trusted from the client. Every time a
submission is finalized (or a test is recalculated after an answer-key edit),
we re-derive the result purely from Question.correct_answer / marks /
negative_marks in the database against the stored Response.selected_answer.
"""
from datetime import datetime

from app.extensions import db
from app.models import Question, Response, Submission


def grade_submission(submission: Submission, commit=True):
    """(Re)compute a submission's score from current answer key + responses."""
    test = submission.test
    questions = {q.id: q for q in test.questions}

    responses_by_q = {r.question_id: r for r in submission.responses}

    correct = wrong = unanswered = 0
    score = 0.0
    max_score = 0.0

    for qid, question in questions.items():
        max_score += question.marks
        resp = responses_by_q.get(qid)
        selected = resp.selected_answer if resp else None

        if resp is None:
            resp = Response(submission_id=submission.id, question_id=qid, selected_answer=None)
            db.session.add(resp)

        if not selected:
            resp.is_correct = None
            resp.marks_awarded = 0.0
            unanswered += 1
            continue

        if selected == question.correct_answer:
            resp.is_correct = True
            resp.marks_awarded = question.marks
            score += question.marks
            correct += 1
        else:
            resp.is_correct = False
            resp.marks_awarded = -abs(question.negative_marks)
            score += resp.marks_awarded
            wrong += 1

    submission.score = round(score, 2)
    submission.max_score = round(max_score, 2)
    submission.correct_count = correct
    submission.wrong_count = wrong
    submission.unanswered_count = unanswered
    submission.percentage = round((score / max_score * 100), 2) if max_score > 0 else 0.0

    if commit:
        db.session.commit()

    return submission


def recalculate_test(test):
    """Re-grade every submitted submission of a test (e.g. after answer key edit)."""
    count = 0
    for submission in test.submissions.filter_by(status="submitted"):
        grade_submission(submission, commit=False)
        submission.regraded_at = datetime.utcnow()
        count += 1
    test.last_recalculated_at = datetime.utcnow()
    db.session.commit()
    return count


def compute_ranks(submissions):
    """Assign dense ranks (1-based, ties share rank) to a list of submissions
    sorted by score descending. Returns list of (rank, submission) tuples."""
    ranked = sorted(submissions, key=lambda s: s.score, reverse=True)
    result = []
    prev_score = None
    rank = 0
    for i, s in enumerate(ranked, start=1):
        if s.score != prev_score:
            rank = i
            prev_score = s.score
        result.append((rank, s))
    return result


def test_statistics(test):
    subs = list(test.submissions.filter_by(status="submitted"))
    if not subs:
        return {
            "attempted": 0, "average": 0, "highest": 0, "lowest": 0,
            "median": 0, "max_marks": test.max_marks,
        }
    scores = sorted(s.score for s in subs)
    n = len(scores)
    mid = n // 2
    median = scores[mid] if n % 2 == 1 else (scores[mid - 1] + scores[mid]) / 2
    return {
        "attempted": n,
        "average": round(sum(scores) / n, 2),
        "highest": round(max(scores), 2),
        "lowest": round(min(scores), 2),
        "median": round(median, 2),
        "max_marks": test.max_marks,
    }


def question_statistics(test):
    stats = []
    for q in test.questions:
        responses = list(q.responses.join(Submission).filter(Submission.status == "submitted"))
        total = len(responses)
        correct = sum(1 for r in responses if r.is_correct is True)
        wrong = sum(1 for r in responses if r.is_correct is False)
        unanswered = sum(1 for r in responses if r.selected_answer is None)
        accuracy = round((correct / total * 100), 1) if total else 0.0
        stats.append({
            "question": q, "total": total, "correct": correct,
            "wrong": wrong, "unanswered": unanswered, "accuracy": accuracy,
        })
    return stats
