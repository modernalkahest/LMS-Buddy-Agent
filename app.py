"""Streamlit frontend for LMS Buddy. Configuration comes from Streamlit secrets (see .streamlit/secrets.toml.example)."""

import hmac
import json
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # local runs; on Streamlit Cloud there is no .env and secrets are used instead

st.set_page_config(page_title="LMS Buddy", page_icon="📚", layout="centered")


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


@st.cache_resource(show_spinner="Starting LMS Buddy…")
def get_agent():
    return agent.build_agent()


def sync_drive() -> int:
    return agent.index.sync()


def tool_calls(messages) -> list[str]:
    return [call["name"] for m in messages if getattr(m, "tool_calls", None) for call in m.tool_calls]


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

with st.sidebar:
    st.header("📚 LMS Buddy")
    if agent.index.store is not None:
        files = {d.metadata["source"] for d in agent.index.store.docstore._dict.values()}
        st.caption(f"{len(files)} files indexed")
        with st.expander("Indexed files"):
            st.write("\n".join(f"- {f}" for f in sorted(files)))
    else:
        st.caption("No files indexed yet")
    if st.button("🔄 Sync Drive", use_container_width=True):
        try:
            with st.spinner("Syncing…"):
                count = sync_drive()
            st.success(f"Re-indexed {count} documents.")
            st.rerun()
        except Exception as e:
            st.error(f"Sync failed: {e}")
    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.pop("messages", None)
        st.session_state.pop("history", None)
        st.rerun()

st.title("LMS Buddy")
st.caption("Ask questions about your Google Drive course material.")

# "history" is the full LangChain message list (incl. tool calls) sent to the agent; "messages" is what we display.
st.session_state.setdefault("messages", [])
st.session_state.setdefault("history", [])

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("tools"):
            st.caption("🔧 " + ", ".join(m["tools"]))

if question := st.chat_input("Ask about your files…"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking…"):
                history = st.session_state.history + [{"role": "user", "content": question}]
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
