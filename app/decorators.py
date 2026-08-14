from functools import wraps

from flask import redirect, request, url_for
from flask_login import current_user


def teacher_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        from app.models import Teacher
        if not current_user.is_authenticated or not isinstance(current_user, Teacher):
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def student_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        from app.models import Student
        if not current_user.is_authenticated or not isinstance(current_user, Student):
            return redirect(url_for("student_auth.login", next=request.path))
        return f(*args, **kwargs)
    return wrapper
