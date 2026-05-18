import streamlit as st

st.set_page_config(page_title="Maintainer's Copilot", layout="wide")

st.title("Maintainer's Copilot")
st.markdown("""
Welcome to the Maintainer's Copilot admin interface.

Use the sidebar to navigate:
- **Chat** — Talk to the copilot
- **Memory Inspector** — View saved context
- **Widget Config** — Manage embeddable widgets
- **Logout** — Sign out

This interface is for authenticated users and admins only.
""")
