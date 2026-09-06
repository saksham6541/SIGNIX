from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.forms import ChangePasswordForm, EditProfileForm, LoginForm, SignupForm
from app.models import User, UserLocation, db

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    profile_form = EditProfileForm(
        display_name=current_user.display_name,
        email=current_user.email,
    )
    password_form = ChangePasswordForm()

    if (
        request.form.get("profile_action") == "profile"
        and profile_form.validate_on_submit()
    ):
        email = profile_form.email.data.strip().lower()
        other_user = User.query.filter(
            User.email == email,
            User.id != current_user.id,
        ).first()
        if other_user is not None:
            profile_form.email.errors.append("That email is already registered.")
        else:
            current_user.display_name = profile_form.display_name.data.strip()
            current_user.email = email
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("auth.profile"))

    if (
        request.form.get("password_action") == "password"
        and password_form.validate_on_submit()
    ):
        if not check_password_hash(
            current_user.password_hash, password_form.current_password.data
        ):
            password_form.current_password.errors.append(
                "Current password is incorrect."
            )
        else:
            current_user.password_hash = generate_password_hash(
                password_form.new_password.data
            )
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("auth.profile"))

    location_count = UserLocation.query.filter_by(user_id=current_user.id).count()
    return render_template(
        "profile.html",
        profile_form=profile_form,
        password_form=password_form,
        location_count=location_count,
    )


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
        flash("You're already logged in.", "info")
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
