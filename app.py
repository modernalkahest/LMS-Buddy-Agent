"""Streamlit frontend for LMS Buddy. Configuration comes from Streamlit secrets (see .streamlit/secrets.toml.example)."""

import asyncio
import hmac
import json
import os
import time

import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # local runs; on Streamlit Cloud there is no .env and secrets are used instead

st.set_page_config(page_title="LMS Buddy", page_icon="📚", layout="wide")


def load_secrets_into_env() -> None:
    """Expose Streamlit secrets as environment variables before agent.py reads them at import time.

    Streamlit Cloud already exports top-level string secrets; this also covers local `streamlit run`
    and the [google_token] table, which is serialized into the GOOGLE_TOKEN_JSON variable drive.py reads.
    """
    try:
        secrets = dict(st.secrets)
    except Exception:  # no secrets file locally: fall back to .env via agent.py's load_dotenv
        return
    for key, value in secrets.items():
        if key == "google_token":
            os.environ["GOOGLE_TOKEN_JSON"] = json.dumps(dict(value))
        elif isinstance(value, (str, int, float, bool)):
            os.environ.setdefault(key, str(value))


def check_password() -> None:
    """Gate the app behind APP_PASSWORD when set; the app exposes the owner's Drive, so set it on public deploys."""
    expected = os.getenv("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered, expected):
            st.session_state.authed = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


load_secrets_into_env()

if not os.getenv("OPENAI_API_KEY"):
    st.error("`OPENAI_API_KEY` is missing. Add it under **Settings → Secrets** (see README).")
    st.stop()

check_password()

import agent  # noqa: E402  (must import after secrets are in the environment)
from drive import get_drive_service, list_course_tree  # noqa: E402


CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1400px;}
.pill {display:inline-block; padding:2px 12px; border-radius:999px; font-size:.85rem;
       background:rgba(76,175,80,.18); color:#4caf50;}
div[data-testid="stButton"] > button {text-align:left; justify-content:flex-start; border-radius:12px;}
.suggest div[data-testid="stButton"] > button {background:#000; color:inherit; border:none; width:100%; padding:.8rem 1rem;}
.course div[data-testid="stButton"] > button {width:100%; padding:1rem; white-space:pre-line;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

SUGGESTIONS = [
    "Why am I learning {course}?",
    "What are the prerequisites for this module?",
    "Explain the learning objectives.",
    "How is this topic used in industry?",
]


@st.cache_resource(show_spinner="Starting LMS Buddy…")
def get_agent():
    return agent.build_agent()


@st.cache_resource
def _courses_cache() -> dict:
    return {"at": 0.0, "value": None}


def _crawl_courses() -> dict[str, list[dict]]:
    return list_course_tree(get_drive_service(), agent.FOLDER_ID)


def load_courses() -> dict[str, list[dict]]:
    """Crawling Drive is slow, so the result is shared across sessions and reused for 10 minutes."""
    cache = _courses_cache()
    if cache["value"] is None or time.time() - cache["at"] > 600:
        cache["value"], cache["at"] = _crawl_courses(), time.time()
    return cache["value"]


async def _sync_and_crawl() -> tuple[int, dict]:
    # The index rebuild and the course crawl are independent, so run them at the same time.
    return await asyncio.gather(asyncio.to_thread(agent.index.sync), asyncio.to_thread(_crawl_courses))


def sync_drive() -> int:
    count, courses = asyncio.run(_sync_and_crawl())
    cache = _courses_cache()
    cache["value"], cache["at"] = courses, time.time()
    return count


def tool_calls(messages) -> list[str]:
    return [call["name"] for m in messages if getattr(m, "tool_calls", None) for call in m.tool_calls]


def ask(question: str, course: str | None, course_files: list[dict]) -> None:
    """Send a question to the agent; the selected course is passed as context but not shown in the chat."""
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking…"):
                prompt = question
                if course:
                    names = ", ".join(f["name"] for f in course_files)
                    prompt = f"[The student has the course '{course}' open. Its files: {names}. Focus on these.]\n\n{question}"
                history = st.session_state.history + [{"role": "user", "content": prompt}]
                result = get_agent().invoke({"messages": history})
            answer = result["messages"][-1].text
            tools = tool_calls(result["messages"][len(history):])
            st.session_state.history = result["messages"]
        except Exception as e:
            answer, tools = f"Something went wrong: {e}", []
        st.markdown(answer)
        if tools:
            st.caption("🔧 " + ", ".join(tools))
    st.session_state.messages.append({"role": "assistant", "content": answer, "tools": tools})


# Streamlit Cloud's disk is ephemeral, so the index is rebuilt from Drive after each restart.
if not st.session_state.get("index_checked"):
    if agent.index.store is None:
        try:
            with st.spinner("Indexing your Google Drive files (first load only)…"):
                sync_drive()
        except Exception as e:
            st.error(f"Could not sync Google Drive: {e}")
            st.stop()
    st.session_state.index_checked = True

try:
    courses: dict[str, list[dict]] = load_courses()
except Exception as e:
    st.warning(f"Could not list your courses: {e}")
    courses = {}

# "history" is the full LangChain message list (incl. tool calls) sent to the agent; "messages" is what we display.
st.session_state.setdefault("messages", [])
st.session_state.setdefault("history", [])
if st.session_state.get("selected") not in courses:
    st.session_state.selected = next(iter(courses), None)
selected = st.session_state.selected
course_files = courses.get(selected, [])

st.subheader("LMS Buddy")
left, right = st.columns([5, 8], gap="large")

with left:
    st.markdown("### My Learning Modules")
    if not courses:
        st.info("No course folders found in your Drive yet.")
    for name, files in courses.items():
        with st.container(key=f"course_{name}"):
            st.markdown('<div class="course">', unsafe_allow_html=True)
            label = f"📖  **{name}**\n\n{len(files)} resources" + ("  ·  ✅ Selected" if name == selected else "")
            if st.button(label, key=f"pick_{name}", type="primary" if name == selected else "secondary"):
                st.session_state.selected = name
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    if selected:
        st.markdown("### Selected module")
        st.markdown(f"**{selected}**")
        for f in course_files:
            icon = "📄" if f["mimeType"] in ("application/pdf", "application/vnd.google-apps.document") else "📁"
            st.markdown(f"{icon} [{f['name']}]({f['webViewLink']})" if f.get("webViewLink") else f"{icon} {f['name']}")
    c1, c2 = st.columns(2)
    if c1.button("🔄 Sync Drive", use_container_width=True):
        try:
            with st.spinner("Syncing…"):
                count = sync_drive()
            st.success(f"Re-indexed {count} documents.")
            st.rerun()
        except Exception as e:
            st.error(f"Sync failed: {e}")
    if c2.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.messages, st.session_state.history = [], []
        st.rerun()

with right:
    with st.container(border=True):
        st.markdown("### LMS Buddy")
        st.markdown('<span class="pill">Ready to help</span>', unsafe_allow_html=True)
        st.divider()

        pending = st.session_state.pop("pending", None)
        with st.container(height=520, border=False):
            if not st.session_state.messages and not pending:
                st.markdown("#### What would you like to explore?")
                st.caption("Suggested questions")
                st.markdown('<div class="suggest">', unsafe_allow_html=True)
                for i, template in enumerate(SUGGESTIONS):
                    text = template.format(course=selected or "this subject")
                    if st.button(text, key=f"sugg_{i}", use_container_width=True):
                        st.session_state.pending = text
                        st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            for m in st.session_state.messages:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])
                    if m.get("tools"):
                        st.caption("🔧 " + ", ".join(m["tools"]))
            if pending:
                ask(pending, selected, course_files)

        if question := st.chat_input("Ask your tutor a question…"):
            st.session_state.pending = question
            st.rerun()
