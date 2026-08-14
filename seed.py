"""Seed demo data: a teacher account, a CA Foundation Economics demo test
with 10+ MCQs, and a handful of sample student submissions (including
wrong answers, unanswered questions, and negative marking) so the app is
immediately explorable.

Run with:  python seed.py
Safe to re-run — it wipes and recreates only the demo records it owns.
"""
import random
from datetime import date, datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import Teacher, Test, Question, Submission, Response, Student
from app.utils.grading import grade_submission

DEMO_EMAIL = "demo@institute.com"
DEMO_PASSWORD = "Demo@1234"
DEMO_STUDENT_PASSWORD = "Student@123"

QUESTIONS = [
    dict(q_number=1, text="Which of the following is NOT a factor of production?",
         option_a="Land", option_b="Labour", option_c="Capital", option_d="Price",
         correct_answer="D", explanation="Price is not a factor of production; the four factors are land, labour, capital, and entrepreneurship."),
    dict(q_number=2, text="The law of demand states that, other things being equal, price and quantity demanded are:",
         option_a="Directly related", option_b="Inversely related", option_c="Unrelated", option_d="Equal",
         correct_answer="B", explanation="As price rises, quantity demanded falls, and vice versa (ceteris paribus)."),
    dict(q_number=3, text="Which market structure is characterized by a single seller and no close substitutes?",
         option_a="Perfect competition", option_b="Monopolistic competition", option_c="Monopoly", option_d="Oligopoly",
         correct_answer="C", explanation="A monopoly has a single seller controlling the entire market supply."),
    dict(q_number=4, text="Gross Domestic Product (GDP) measures:",
         option_a="Total value of goods and services produced within a country in a given period",
         option_b="Total value of exports only", option_c="Total money supply in the economy",
         option_d="Total government expenditure only",
         correct_answer="A", explanation="GDP is the market value of all final goods and services produced within a country's borders in a specific time period."),
    dict(q_number=5, text="Which of the following best describes 'elasticity of demand'?",
         option_a="The total quantity demanded at a given price",
         option_b="Responsiveness of quantity demanded to a change in price",
         option_c="The slope of the supply curve", option_d="The equilibrium price level",
         correct_answer="B", explanation="Elasticity of demand measures how much quantity demanded changes in response to a price change."),
    dict(q_number=6, text="Inflation refers to:",
         option_a="A sustained fall in the general price level", option_b="A sustained rise in the general price level",
         option_c="A rise in the exchange rate", option_d="A fall in unemployment",
         correct_answer="B", explanation="Inflation is a persistent increase in the general price level of goods and services."),
    dict(q_number=7, text="The Central Bank of India is:",
         option_a="State Bank of India", option_b="NABARD", option_c="Reserve Bank of India", option_d="NITI Aayog",
         correct_answer="C", explanation="The Reserve Bank of India (RBI) is India's central bank, established in 1935."),
    dict(q_number=8, text="Which of the following is an example of indirect tax?",
         option_a="Income Tax", option_b="Corporate Tax", option_c="Goods and Services Tax (GST)", option_d="Wealth Tax",
         correct_answer="C", explanation="GST is levied on goods/services and its burden can be shifted, making it an indirect tax."),
    dict(q_number=9, text="Opportunity cost is best defined as:",
         option_a="The monetary cost of production",
         option_b="The value of the next best alternative foregone",
         option_c="The fixed cost of a firm", option_d="The total revenue of a firm",
         correct_answer="B", explanation="Opportunity cost is what you give up (the next best alternative) when making a choice."),
    dict(q_number=10, text="A Public Good is characterized by:",
         option_a="Excludability and rivalry", option_b="Non-excludability and non-rivalry",
         option_c="High price and low demand", option_d="Only private consumption",
         correct_answer="B", explanation="Public goods are non-excludable and non-rivalrous, e.g. national defence, street lighting."),
    dict(q_number=11, text="Which of the following shifts the demand curve to the right?",
         option_a="Increase in price of the good", option_b="Decrease in consumer income (normal good)",
         option_c="Increase in consumer income (normal good)", option_d="Increase in supply",
         correct_answer="C", explanation="For a normal good, a rise in consumer income increases demand at every price, shifting the curve right."),
    dict(q_number=12, text="Fiscal policy primarily involves changes in:",
         option_a="Money supply and interest rates", option_b="Government spending and taxation",
         option_c="Exchange rates only", option_d="Bank reserve requirements",
         correct_answer="B", explanation="Fiscal policy is government use of spending and taxation to influence the economy."),
]

# (student_name, roll_number, batch, answers-by-qnum dict; missing q => unanswered)
STUDENTS = [
    ("Aarav Sharma", "ECO101", "Batch A", {1: "D", 2: "B", 3: "C", 4: "A", 5: "B", 6: "B", 7: "C", 8: "C", 9: "B", 10: "B", 11: "C", 12: "B"}),
    ("Priya Nair", "ECO102", "Batch A", {1: "D", 2: "B", 3: "C", 4: "A", 5: "B", 6: "B", 7: "C", 8: "A", 9: "B", 10: "B", 11: "C", 12: "A"}),
    ("Rohan Mehta", "ECO103", "Batch A", {1: "A", 2: "B", 3: "C", 4: "D", 5: "B", 6: "A", 7: "C", 8: "C", 9: "B", 10: "B"}),
    ("Sneha Iyer", "ECO104", "Batch B", {1: "D", 2: "A", 3: "C", 4: "A", 5: "C", 6: "B", 7: "C", 8: "C", 9: "A", 10: "B", 11: "C", 12: "B"}),
    ("Kabir Singh", "ECO105", "Batch B", {1: "D", 2: "B", 3: "C", 4: "A", 5: "B", 6: "B", 7: "C", 8: "C", 9: "B", 10: "B", 11: "C", 12: "B"}),
    ("Ananya Gupta", "ECO106", "Batch B", {1: "B", 2: "B", 3: "A", 4: "A", 5: "B", 6: "D", 7: "C", 8: "C", 9: "B"}),
]

# Roster-only student with no submission yet — demonstrates the "not attempted" portal state.
NOT_YET_ATTEMPTED = ("Ishaan Verma", "ECO107", "Batch B")


def run():
    app = create_app()
    with app.app_context():
        teacher = Teacher.query.filter_by(email=DEMO_EMAIL).first()
        if not teacher:
            teacher = Teacher(name="Demo Teacher", email=DEMO_EMAIL)
            teacher.set_password(DEMO_PASSWORD)
            db.session.add(teacher)
            db.session.commit()
            print(f"Created demo teacher: {DEMO_EMAIL} / {DEMO_PASSWORD}")
        else:
            print(f"Demo teacher already exists: {DEMO_EMAIL}")

        existing = Test.query.filter_by(teacher_id=teacher.id, test_number="Demo Test").first()
        if existing:
            db.session.delete(existing)
            db.session.commit()
            print("Removed previous demo test to reseed cleanly.")

        test = Test(
            teacher_id=teacher.id,
            title="CA Foundation Economics – Demo Test",
            subject="CA Foundation Economics",
            test_number="Demo Test",
            description=(
                "Attempt all questions. Each question carries 1 mark. "
                "0.25 marks will be deducted for every wrong answer. "
                "This is a demo test preloaded with sample data."
            ),
            test_date=date.today(),
            duration_minutes=30,
            marks_per_question_default=1.0,
            negative_marks_default=0.25,
            questions_visible=True,
            allow_answer_change=True,
            allow_skip=True,
            show_result_immediately=True,
            one_attempt_only=True,
            strict_timer=True,
            allow_review=True,
            status="active",
            access_code="ECONDEMO",
        )
        db.session.add(test)
        db.session.flush()

        qid_by_number = {}
        for i, q in enumerate(QUESTIONS, start=1):
            question = Question(
                test_id=test.id, order_index=i, marks=1.0, negative_marks=0.25, **q,
            )
            db.session.add(question)
            db.session.flush()
            qid_by_number[q["q_number"]] = question.id

        db.session.commit()
        print(f"Created demo test '{test.title}' with {len(QUESTIONS)} questions. Access code: {test.access_code}")

        base_time = datetime.utcnow() - timedelta(hours=2)
        roster_rows = []
        for i, (name, roll, batch, answers) in enumerate(STUDENTS):
            student = Student(
                teacher_id=teacher.id, roll_number=roll, name=name, batch=batch,
            )
            student.set_password(DEMO_STUDENT_PASSWORD)
            db.session.add(student)
            db.session.flush()
            roster_rows.append(student)

            submission = Submission(
                test_id=test.id,
                student_id=student.id,
                reference_number=Submission.new_reference_number(),
                dup_guard_token="seed",
                student_name=name, roll_number=roll, batch=batch,
                status="submitted",
                started_at=base_time + timedelta(minutes=i * 5),
                submitted_at=base_time + timedelta(minutes=i * 5 + 18),
            )
            db.session.add(submission)
            db.session.flush()

            for qnum, qid in qid_by_number.items():
                selected = answers.get(qnum)  # None -> left unanswered
                db.session.add(Response(
                    submission_id=submission.id, question_id=qid, selected_answer=selected,
                ))
            db.session.flush()
            grade_submission(submission, commit=False)

        not_yet = Student(
            teacher_id=teacher.id, roll_number=NOT_YET_ATTEMPTED[1],
            name=NOT_YET_ATTEMPTED[0], batch=NOT_YET_ATTEMPTED[2],
        )
        not_yet.set_password(DEMO_STUDENT_PASSWORD)
        db.session.add(not_yet)
        roster_rows.append(not_yet)

        db.session.commit()
        print(f"Created {len(STUDENTS)} student roster accounts with submitted, graded attempts (mixed correct/wrong/unanswered).")
        print(f"Plus 1 roster account with no submission yet ({NOT_YET_ATTEMPTED[1]}) to demo the 'not attempted' state.")
        print("\nDemo is ready:")
        print(f"  Teacher login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"  Student login: any roll number below / {DEMO_STUDENT_PASSWORD}")
        print("  Roll numbers: " + ", ".join(s.roll_number for s in roster_rows))
        print(f"  Student test link: /test/{test.access_code}")


if __name__ == "__main__":
    run()
