import secrets
import string
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def _gen_code(length=8, alphabet=string.ascii_uppercase + string.digits):
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Teacher(UserMixin, db.Model):
    __tablename__ = "teachers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tests = db.relationship("Test", backref="teacher", lazy="dynamic", cascade="all, delete-orphan")
    students = db.relationship("Student", backref="teacher", lazy="dynamic", cascade="all, delete-orphan")

    def get_id(self):
        return f"teacher-{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Test(db.Model):
    __tablename__ = "tests"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)

    title = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(120), default="CA Foundation Economics")
    test_number = db.Column(db.String(50))
    description = db.Column(db.Text)
    test_date = db.Column(db.Date)
    duration_minutes = db.Column(db.Integer)

    marks_per_question_default = db.Column(db.Float, default=1.0)
    negative_marks_default = db.Column(db.Float, default=0.0)

    questions_visible = db.Column(db.Boolean, default=False)
    allow_answer_change = db.Column(db.Boolean, default=True)
    allow_skip = db.Column(db.Boolean, default=True)
    show_result_immediately = db.Column(db.Boolean, default=True)
    one_attempt_only = db.Column(db.Boolean, default=True)
    strict_timer = db.Column(db.Boolean, default=False)
    allow_review = db.Column(db.Boolean, default=True)

    status = db.Column(db.String(20), default="draft")  # draft | active | closed
    access_code = db.Column(db.String(20), unique=True, nullable=False, index=True, default=lambda: _gen_code(8))

    question_paper_path = db.Column(db.String(500))
    question_paper_original_name = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_recalculated_at = db.Column(db.DateTime)

    questions = db.relationship(
        "Question", backref="test", lazy="dynamic",
        cascade="all, delete-orphan", order_by="Question.order_index"
    )
    submissions = db.relationship(
        "Submission", backref="test", lazy="dynamic", cascade="all, delete-orphan"
    )

    @property
    def total_questions(self):
        return self.questions.count()

    @property
    def max_marks(self):
        return sum(q.marks for q in self.questions) or 0.0

    @property
    def submitted_count(self):
        return self.submissions.filter_by(status="submitted").count()

    def next_question_number(self):
        last = self.questions.order_by(Question.order_index.desc()).first()
        return (last.order_index + 1) if last else 1


class Student(UserMixin, db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)

    roll_number = db.Column(db.String(100), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    batch = db.Column(db.String(100))
    reg_number = db.Column(db.String(100))
    email = db.Column(db.String(255))
    mobile = db.Column(db.String(30))

    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    submissions = db.relationship("Submission", backref="student", lazy="dynamic")

    __table_args__ = (
        db.UniqueConstraint("teacher_id", "roll_number", name="uq_teacher_roll_number"),
    )

    def get_id(self):
        return f"student-{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @staticmethod
    def generate_pin(length=6):
        return "".join(secrets.choice(string.digits) for _ in range(length))


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)

    order_index = db.Column(db.Integer, nullable=False)
    q_number = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text)  # optional; paper may be physical
    option_a = db.Column(db.Text)
    option_b = db.Column(db.Text)
    option_c = db.Column(db.Text)
    option_d = db.Column(db.Text)
    correct_answer = db.Column(db.String(1), nullable=False)  # A/B/C/D
    marks = db.Column(db.Float, nullable=False, default=1.0)
    negative_marks = db.Column(db.Float, nullable=False, default=0.0)
    explanation = db.Column(db.Text)

    responses = db.relationship("Response", backref="question", lazy="dynamic", cascade="all, delete-orphan")

    def option_text(self, letter):
        return {
            "A": self.option_a, "B": self.option_b,
            "C": self.option_c, "D": self.option_d,
        }.get(letter)


class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)

    reference_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    dup_guard_token = db.Column(db.String(64), index=True)

    student_name = db.Column(db.String(255))
    roll_number = db.Column(db.String(100), index=True)
    reg_number = db.Column(db.String(100))
    batch = db.Column(db.String(100))
    email = db.Column(db.String(255))
    mobile = db.Column(db.String(30))

    status = db.Column(db.String(20), default="in_progress")  # in_progress | submitted
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime)

    score = db.Column(db.Float, default=0.0)
    max_score = db.Column(db.Float, default=0.0)
    correct_count = db.Column(db.Integer, default=0)
    wrong_count = db.Column(db.Integer, default=0)
    unanswered_count = db.Column(db.Integer, default=0)
    percentage = db.Column(db.Float, default=0.0)

    regraded_at = db.Column(db.DateTime)

    responses = db.relationship(
        "Response", backref="submission", lazy="dynamic", cascade="all, delete-orphan"
    )

    @staticmethod
    def new_reference_number():
        return "SUB-" + _gen_code(10, string.ascii_uppercase + string.digits)


class Response(db.Model):
    __tablename__ = "responses"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)

    selected_answer = db.Column(db.String(1))  # A/B/C/D or None
    is_correct = db.Column(db.Boolean)  # None if unanswered
    marks_awarded = db.Column(db.Float, default=0.0)

    __table_args__ = (
        db.UniqueConstraint("submission_id", "question_id", name="uq_submission_question"),
    )


class PushSubscription(db.Model):
    """A single browser/device's Web Push subscription. Spans both user types,
    so we store owner_type ('teacher'|'student') + owner_id rather than a FK."""
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    owner_type = db.Column(db.String(10), nullable=False, index=True)
    owner_id = db.Column(db.Integer, nullable=False, index=True)

    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def as_dict(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}
