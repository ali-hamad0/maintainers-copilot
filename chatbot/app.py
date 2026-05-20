"""Streamlit admin UI for Maintainer's Copilot.

Pages:
  Login       — email + password → JWT stored in session_state
  Chat        — SSE streaming from POST /chat
  Memories    — table of current user's long-term memories
  Widget Admin — CRUD for widget configs (origin allowlisting)

Environment:
  API_BASE_URL — defaults to http://localhost:8000
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="Maintainer's Copilot", layout="wide", page_icon="🤖")


# ── Session helpers ───────────────────────────────────────────────────────────


def _token() -> str | None:
    return st.session_state.get("jwt_token")


def _auth_headers() -> dict[str, str]:
    tok = _token()
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    return {}


def _api(method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = {**_auth_headers(), **kwargs.pop("headers", {})}
    with httpx.Client(timeout=30.0) as client:
        return client.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)


# ── Login page ────────────────────────────────────────────────────────────────


def _render_login() -> None:
    st.title("Maintainer's Copilot")
    st.subheader("Sign in")

    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        if not email or not password:
            st.error("Email and password are required.")
            return
        try:
            resp = httpx.post(
                f"{API_BASE}/auth/jwt/login",
                data={"username": email, "password": password},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                st.session_state["jwt_token"] = data["access_token"]
                st.session_state["user_email"] = email
                st.session_state["page"] = "chat"
                st.rerun()
            else:
                st.error(f"Login failed ({resp.status_code}). Check your credentials.")
        except httpx.RequestError as exc:
            st.error(f"Cannot reach API: {exc}")

    st.markdown("---")
    st.caption(f"API: `{API_BASE}`")


# ── Chat page ─────────────────────────────────────────────────────────────────


def _render_chat() -> None:
    st.title("Chat")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    if "conversation_id" not in st.session_state:
        st.session_state["conversation_id"] = None

    # Display conversation history.
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("Ask the copilot something…")
    if not user_input:
        return

    # Append user message.
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Stream the SSE response.
    payload: dict[str, Any] = {"message": user_input}
    if st.session_state["conversation_id"]:
        payload["conversation_id"] = st.session_state["conversation_id"]

    reply_parts: list[str] = []
    tool_events: list[str] = []

    with st.chat_message("assistant"):
        reply_box = st.empty()
        tool_box = st.empty()

        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream(
                    "POST",
                    f"{API_BASE}/chat",
                    json=payload,
                    headers=_auth_headers(),
                ) as resp:
                    # Conversation ID is in the response header.
                    conv_id = resp.headers.get("x-conversation-id")
                    if conv_id:
                        st.session_state["conversation_id"] = conv_id

                    for line in resp.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        etype = event.get("type")
                        if etype == "text_delta":
                            reply_parts.append(event.get("content", ""))
                            reply_box.markdown("".join(reply_parts))
                        elif etype == "tool_start":
                            tool_events.append(f"⚙️ `{event['tool']}` …")
                            tool_box.markdown("\n".join(tool_events))
                        elif etype == "tool_end":
                            status = "✅" if not event.get("is_error") else "❌"
                            tool_events[-1] = f"{status} `{event['tool']}` done"
                            tool_box.markdown("\n".join(tool_events))
                        elif etype == "error":
                            st.error(event.get("message", "Unknown error"))
                        elif etype == "done":
                            break

        except httpx.RequestError as exc:
            st.error(f"Stream error: {exc}")

    final_reply = "".join(reply_parts)
    if final_reply:
        st.session_state["messages"].append({"role": "assistant", "content": final_reply})

    if st.button("New conversation"):
        st.session_state["messages"] = []
        st.session_state["conversation_id"] = None
        st.rerun()


# ── Memory inspector ──────────────────────────────────────────────────────────


def _render_memories() -> None:
    st.title("Memory Inspector")
    st.caption("Long-term memories stored for your account, newest first.")

    if st.button("Refresh"):
        st.rerun()

    try:
        resp = _api("GET", "/memories")
        if resp.status_code == 200:
            memories = resp.json()
            if not memories:
                st.info("No memories stored yet. Use the chat and ask the copilot to remember something.")
            else:
                for m in memories:
                    with st.expander(
                        f"[{m['source_tool']}] {m['content'][:80]}…"
                        if len(m["content"]) > 80
                        else f"[{m['source_tool']}] {m['content']}"
                    ):
                        st.json(m)
        else:
            st.error(f"Failed to load memories: {resp.status_code}")
    except httpx.RequestError as exc:
        st.error(f"Cannot reach API: {exc}")


# ── Widget admin ──────────────────────────────────────────────────────────────


def _render_widgets() -> None:
    st.title("Widget Config Admin")
    st.caption(
        "Create widget configs and manage which origins are allowed to embed the chat widget."
    )

    # ── Create new widget ─────────────────────────────────────────────────────
    with st.expander("Create new widget config", expanded=False):
        with st.form("create_widget"):
            name = st.text_input("Name", placeholder="My demo widget")
            origins_raw = st.text_area(
                "Allowed origins (one per line)",
                placeholder="http://localhost:8080\nhttps://mysite.example.com",
            )
            if st.form_submit_button("Create"):
                allowed = [o.strip() for o in origins_raw.splitlines() if o.strip()]
                try:
                    resp = _api(
                        "POST",
                        "/widgets",
                        json={"name": name, "allowed_origins": allowed},
                    )
                    if resp.status_code == 201:
                        st.success(f"Widget created: `{resp.json()['id']}`")
                        st.rerun()
                    else:
                        st.error(f"Error {resp.status_code}: {resp.text}")
                except httpx.RequestError as exc:
                    st.error(str(exc))

    # ── List existing widgets ─────────────────────────────────────────────────
    st.subheader("Your widget configs")
    if st.button("Refresh list"):
        st.rerun()

    try:
        resp = _api("GET", "/widgets")
        if resp.status_code != 200:
            st.error(f"Failed to load widgets: {resp.status_code}")
            return
        widgets = resp.json()
    except httpx.RequestError as exc:
        st.error(str(exc))
        return

    if not widgets:
        st.info("No widget configs yet. Create one above.")
        return

    for w in widgets:
        with st.expander(f"{w['name']} — `{w['id']}`  {'🟢' if w['is_active'] else '🔴'}"):
            st.write(f"**Allowed origins:** {', '.join(w['allowed_origins']) or '*(none)*'}")
            st.write(f"**Created:** {w['created_at'][:19]}")
            st.markdown(
                f"**Embed snippet URL:** `{API_BASE}/embed/{w['id']}`  "
                "(add `Content-Security-Policy` header to your host page)"
            )

            with st.form(f"edit_{w['id']}"):
                new_name = st.text_input("Name", value=w["name"])
                new_origins = st.text_area(
                    "Allowed origins",
                    value="\n".join(w["allowed_origins"]),
                )
                new_active = st.checkbox("Active", value=w["is_active"])

                col1, col2 = st.columns(2)
                with col1:
                    save = st.form_submit_button("Save")
                with col2:
                    deactivate = st.form_submit_button("Deactivate" if w["is_active"] else "Activate")

            if save:
                allowed = [o.strip() for o in new_origins.splitlines() if o.strip()]
                try:
                    resp = _api(
                        "PATCH",
                        f"/widgets/{w['id']}",
                        json={
                            "name": new_name,
                            "allowed_origins": allowed,
                            "is_active": new_active,
                        },
                    )
                    if resp.status_code == 200:
                        st.success("Saved.")
                        st.rerun()
                    else:
                        st.error(f"Error {resp.status_code}: {resp.text}")
                except httpx.RequestError as exc:
                    st.error(str(exc))

            if deactivate:
                try:
                    resp = _api(
                        "PATCH",
                        f"/widgets/{w['id']}",
                        json={"is_active": not w["is_active"]},
                    )
                    if resp.status_code == 200:
                        st.rerun()
                    else:
                        st.error(f"Error {resp.status_code}: {resp.text}")
                except httpx.RequestError as exc:
                    st.error(str(exc))


# ── Navigation + main ─────────────────────────────────────────────────────────


def main() -> None:
    if not _token():
        _render_login()
        return

    # Sidebar navigation.
    st.sidebar.title("Maintainer's Copilot")
    st.sidebar.write(f"Signed in as `{st.session_state.get('user_email', '?')}`")
    st.sidebar.divider()

    page = st.sidebar.radio(
        "Navigate",
        ["Chat", "Memories", "Widget Admin"],
        key="nav_radio",
    )

    if st.sidebar.button("Sign out"):
        st.session_state.clear()
        st.rerun()

    if page == "Chat":
        _render_chat()
    elif page == "Memories":
        _render_memories()
    elif page == "Widget Admin":
        _render_widgets()


if __name__ == "__main__":
    main()
else:
    main()
