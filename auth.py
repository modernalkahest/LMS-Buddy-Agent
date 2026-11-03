"""Email one-time-code login for the Streamlit app.

Configuration (env vars / Streamlit secrets):
    ALLOWED_EMAILS   comma-separated addresses that may log in (required for OTP login)
    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (defaults to SMTP_USER)
"""

import hashlib
import hmac
import os
import secrets
import smtplib
import time
from email.message import EmailMessage

import streamlit as st

OTP_TTL = 600  # seconds a code stays valid
RESEND_COOLDOWN = 30
MAX_ATTEMPTS = 5


def otp_enabled() -> bool:
    return bool(os.getenv("ALLOWED_EMAILS") and os.getenv("SMTP_HOST") and os.getenv("SMTP_USER"))


def _allowed(email: str) -> bool:
    return email in {e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()}


def _hash(code: str, email: str) -> str:
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def _send(email: str, code: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Your LMS Buddy login code"
    msg["From"] = os.getenv("SMTP_FROM") or os.environ["SMTP_USER"]
    msg["To"] = email
    msg.set_content(f"Your LMS Buddy login code is {code}. It expires in {OTP_TTL // 60} minutes.")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)


def _request_code(email: str) -> None:
    st.session_state.otp_email = email
    st.session_state.otp_sent_at = time.time()
    st.session_state.otp_attempts = 0
    st.session_state.pop("otp_hash", None)
    # Only authorised addresses get a real code, but the UI looks identical either way.
    if _allowed(email):
        code = f"{secrets.randbelow(10**6):06d}"
        _send(email, code)
        st.session_state.otp_hash = _hash(code, email)


LOGIN_CSS = """
<style>
.block-container {padding-top: 12vh;}
.login-logo {font-size: 3rem; text-align: center; margin-bottom: 0;}
.login-title {text-align: center; font-size: 1.8rem; font-weight: 700; margin: 0;}
.login-sub {text-align: center; opacity: .7; margin: .25rem 0 1.25rem;}
.st-key-otp_code input {text-align: center; font-size: 1.8rem; letter-spacing: .6rem; font-family: monospace;}
.st-key-login_card div[data-testid="stFormSubmitButton"] button {width: 100%;}
</style>
"""


def _card_header(subtitle: str) -> None:
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    st.markdown('<p class="login-logo">📚</p><p class="login-title">LMS Buddy</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="login-sub">{subtitle}</p>', unsafe_allow_html=True)


def otp_login() -> None:
    """Render the email → code login flow and stop the script until the user is authenticated."""
    if st.session_state.get("authed"):
        return

    email = st.session_state.get("otp_email")
    _, middle, _ = st.columns([1, 1.1, 1])

    with middle:
        if not email:
            _card_header("Sign in with a one-time code sent to your email")
            with st.container(border=True, key="login_card"):
                with st.form("otp_request", border=False):
                    entered = st.text_input("Email address", placeholder="you@example.com").strip().lower()
                    send = st.form_submit_button("Send code", type="primary")
                if send and entered:
                    try:
                        with st.spinner("Sending code…"):
                            _request_code(entered)
                    except Exception as e:
                        st.error(f"Could not send the code: {e}")
                    else:
                        st.rerun()
            st.stop()

        _card_header(f"Enter the 6-digit code we sent to<br><b>{email}</b>")
        with st.container(border=True, key="login_card"):
            with st.form("otp_verify", border=False):
                code = st.text_input("Login code", max_chars=6, placeholder="••••••", key="otp_code",
                                     label_visibility="collapsed").strip()
                verify = st.form_submit_button("Log in", type="primary")
            if verify:
                expected = st.session_state.get("otp_hash")
                expired = time.time() - st.session_state.otp_sent_at > OTP_TTL
                if st.session_state.otp_attempts >= MAX_ATTEMPTS or expired:
                    st.error("This code is no longer valid. Request a new one.")
                elif expected and hmac.compare_digest(_hash(code, email), expected):
                    st.session_state.authed = True
                    for key in ("otp_email", "otp_hash", "otp_sent_at", "otp_attempts"):
                        st.session_state.pop(key, None)
                    st.rerun()
                else:
                    st.session_state.otp_attempts += 1
                    st.error("Incorrect code.")

            wait = int(RESEND_COOLDOWN - (time.time() - st.session_state.otp_sent_at))
            c1, c2 = st.columns(2)
            if c1.button("Resend code", disabled=wait > 0, use_container_width=True,
                         help=f"Available in {wait}s" if wait > 0 else None):
                try:
                    _request_code(email)
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not send the code: {e}")
            if c2.button("Change email", use_container_width=True):
                st.session_state.pop("otp_email", None)
                st.rerun()
            st.caption(f"Code expires in {OTP_TTL // 60} minutes. If the address isn't authorised, no code is sent.")
    st.stop()
