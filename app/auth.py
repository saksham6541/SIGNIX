from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.forms import LoginForm, SignupForm
from app.models import User, db

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    form = SignupForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first() is not None:
            form.email.errors.append("That email is already registered.")
        else:
            user = User(
                email=email,
                display_name=form.display_name.data.strip(),
                password_hash=generate_password_hash(form.password.data),
            )
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("pages.dashboard"))
    return render_template("signup.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("pages.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user is None or not check_password_hash(
            user.password_hash, form.password.data
        ):
            flash("Invalid email or password.", "error")
        else:
            login_user(user)
            return redirect(url_for("pages.dashboard"))
    return render_template("login.html", form=form)


@auth_bp.post("/logout")
def logout():
    logout_user()
    return redirect(url_for("pages.index"))
