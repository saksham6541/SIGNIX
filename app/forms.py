from flask_wtf import FlaskForm
from wtforms import (
    FloatField,
    HiddenField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
)

INDIAN_STATES = [
    "Delhi",
    "Uttar Pradesh",
    "Maharashtra",
    "Karnataka",
    "Tamil Nadu",
    "Gujarat",
    "West Bengal",
    "Rajasthan",
]

PROPERTY_TYPES = [
    ("residential", "Residential home"),
    ("commercial", "Shop / office / commercial"),
    ("remote", "Remote / unreliable grid"),
]


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


class SignupForm(FlaskForm):
    display_name = StringField(
        "Display name", validators=[DataRequired(), Length(max=128)]
    )
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password")],
    )
    submit = SubmitField("Create account")


class EditProfileForm(FlaskForm):
    profile_action = HiddenField(default="profile")
    display_name = StringField(
        "Display name", validators=[DataRequired(), Length(max=128)]
    )
    email = StringField("Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Save changes")


class ChangePasswordForm(FlaskForm):
    password_action = HiddenField(default="password")
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField(
        "New password", validators=[DataRequired(), Length(min=8)]
    )
    confirm_new_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password")],
    )
    submit = SubmitField("Change password")


class SettingsForm(FlaskForm):
    default_tariff_per_kwh = FloatField(
        "Tariff per kWh (₹)",
        validators=[Optional(), NumberRange(min=0)],
    )
    default_state = SelectField(
        "State",
        choices=[("", "Select a state")] + [(state, state) for state in INDIAN_STATES],
        validators=[Optional()],
    )
    default_property_type = SelectField(
        "Property type",
        choices=PROPERTY_TYPES,
        validators=[Optional()],
    )
    submit = SubmitField("Save settings")
