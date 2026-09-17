from flask_wtf import FlaskForm
from flask_babel import lazy_gettext as _
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
    email = StringField(_("Email"), validators=[DataRequired(), Email()])
    password = PasswordField(_("Password"), validators=[DataRequired()])
    submit = SubmitField(_("Log in"))


class SignupForm(FlaskForm):
    display_name = StringField(
        _("Display name"), validators=[DataRequired(), Length(max=128)]
    )
    email = StringField(_("Email"), validators=[DataRequired(), Email()])
    password = PasswordField(_("Password"), validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        _("Confirm password"),
        validators=[DataRequired(), EqualTo("password")],
    )
    submit = SubmitField(_("Create account"))


class EditProfileForm(FlaskForm):
    profile_action = HiddenField(default="profile")
    display_name = StringField(
        _("Display name"), validators=[DataRequired(), Length(max=128)]
    )
    email = StringField(_("Email"), validators=[DataRequired(), Email()])
    submit = SubmitField(_("Save changes"))


class ChangePasswordForm(FlaskForm):
    password_action = HiddenField(default="password")
    current_password = PasswordField(_("Current password"), validators=[DataRequired()])
    new_password = PasswordField(
        _("New password"), validators=[DataRequired(), Length(min=8)]
    )
    confirm_new_password = PasswordField(
        _("Confirm new password"),
        validators=[DataRequired(), EqualTo("new_password")],
    )
    submit = SubmitField(_("Change password"))


class SettingsForm(FlaskForm):
    default_tariff_per_kwh = FloatField(
        _("Tariff per kWh (₹)"),
        validators=[Optional(), NumberRange(min=0)],
    )
    default_state = SelectField(
        _("State"),
        choices=[("", _("Select a state"))]
        + [(state, state) for state in INDIAN_STATES],
        validators=[Optional()],
    )
    default_property_type = SelectField(
        _("Property type"),
        choices=[(value, _(label)) for value, label in PROPERTY_TYPES],
        validators=[Optional()],
    )
    submit = SubmitField(_("Save settings"))
