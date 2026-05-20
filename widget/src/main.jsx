import React, { useState, useRef, useEffect } from "react";
import { createRoot } from "react-dom/client";

const API_BASE = (window.__COPILOT_API_BASE__ ?? "http://localhost:8000").replace(/\/$/, "");

// ── Styles ────────────────────────────────────────────────────────────────────

const S = {
  bubble: {
    position: "fixed",
    bottom: 24,
    right: 24,
    zIndex: 9999,
    fontFamily: "system-ui, sans-serif",
    fontSize: 14,
  },
  panel: {
    width: 360,
    height: 520,
    background: "#fff",
    border: "1px solid #e0e0e0",
    borderRadius: 12,
    boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
    display: "flex",
    flexDirection: "column",
    marginBottom: 10,
    overflow: "hidden",
  },
  header: {
    padding: "12px 16px",
    background: "#0070f3",
    color: "#fff",
    fontWeight: 600,
    fontSize: 15,
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  body: {
    flex: 1,
    overflowY: "auto",
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  inputRow: {
    display: "flex",
    borderTop: "1px solid #eee",
    padding: 8,
    gap: 6,
  },
  input: {
    flex: 1,
    padding: "7px 10px",
    borderRadius: 6,
    border: "1px solid #ccc",
    fontSize: 13,
    outline: "none",
  },
  sendBtn: {
    padding: "7px 14px",
    borderRadius: 6,
    background: "#0070f3",
    color: "#fff",
    border: "none",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 500,
  },
  toggleBtn: {
    width: 52,
    height: 52,
    borderRadius: "50%",
    background: "#0070f3",
    color: "#fff",
    border: "none",
    cursor: "pointer",
    fontSize: 22,
    boxShadow: "0 2px 12px rgba(0,0,0,0.22)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
};

function msgBubble(role, text) {
  const isUser = role === "user";
  return {
    display: "inline-block",
    padding: "7px 11px",
    borderRadius: 10,
    background: isUser ? "#0070f3" : "#f2f2f2",
    color: isUser ? "#fff" : "#111",
    maxWidth: "85%",
    wordBreak: "break-word",
    alignSelf: isUser ? "flex-end" : "flex-start",
  };
}

// ── Login form ────────────────────────────────────────────────────────────────

function LoginForm({ onLogin }) {
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!email || !pass) return;
    setLoading(true);
    setErr("");
    try {
      const form = new URLSearchParams({ username: email, password: pass });
      const res = await fetch(`${API_BASE}/auth/jwt/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: form.toString(),
      });
      if (!res.ok) {
        setErr("Invalid credentials. Try again.");
        setLoading(false);
        return;
      }
      const data = await res.json();
      onLogin(data.access_token, email);
    } catch {
      setErr("Cannot reach the API.");
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 10 }}>
      <p style={{ margin: 0, color: "#555" }}>Sign in to start chatting</p>
      {err && <p style={{ margin: 0, color: "#d32f2f", fontSize: 12 }}>{err}</p>}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={{ ...S.input, width: "100%", boxSizing: "border-box" }}
          required
        />
        <input
          type="password"
          placeholder="Password"
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          style={{ ...S.input, width: "100%", boxSizing: "border-box" }}
          required
        />
        <button
          type="submit"
          disabled={loading}
          style={{ ...S.sendBtn, padding: "9px 0", width: "100%" }}
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

// ── Chat view ─────────────────────────────────────────────────────────────────

function ChatView({ token, onLogout }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [convId, setConvId] = useState(null);
  const bodyRef = useRef(null);

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setStreaming(true);

    // Placeholder for the assistant reply that we'll fill incrementally.
    setMessages((prev) => [...prev, { role: "assistant", text: "" }]);

    const payload = { message: text };
    if (convId) payload.conversation_id = convId;

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        setMessages((prev) => {
          const copy = [...prev];
          copy[copy.length - 1] = {
            role: "assistant",
            text: `Error ${res.status}. Please try again.`,
          };
          return copy;
        });
        setStreaming(false);
        return;
      }

      // Grab conversation_id from header.
      const cid = res.headers.get("x-conversation-id");
      if (cid) setConvId(cid);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let event;
          try {
            event = JSON.parse(line.slice(6));
          } catch {
            continue;
          }

          if (event.type === "text_delta") {
            setMessages((prev) => {
              const copy = [...prev];
              copy[copy.length - 1] = { role: "assistant", text: event.content };
              return copy;
            });
          } else if (event.type === "error") {
            setMessages((prev) => {
              const copy = [...prev];
              copy[copy.length - 1] = {
                role: "assistant",
                text: `⚠️ ${event.message}`,
              };
              return copy;
            });
          } else if (event.type === "done") {
            break;
          }
        }
      }
    } catch (err) {
      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = {
          role: "assistant",
          text: "Connection error. Please try again.",
        };
        return copy;
      });
    }

    setStreaming(false);
  };

  return (
    <>
      <div ref={bodyRef} style={S.body}>
        {messages.length === 0 && (
          <p style={{ color: "#999", fontSize: 13, textAlign: "center", marginTop: 40 }}>
            Ask me anything about this project's issues…
          </p>
        )}
        {messages.map((m, i) => (
          <span key={i} style={msgBubble(m.role, m.text)}>
            {m.text || (m.role === "assistant" && streaming ? "▌" : "")}
          </span>
        ))}
      </div>
      <div style={S.inputRow}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
          placeholder="Ask something…"
          disabled={streaming}
          style={S.input}
        />
        <button onClick={send} disabled={streaming} style={S.sendBtn}>
          {streaming ? "…" : "Send"}
        </button>
      </div>
    </>
  );
}

// ── Root widget ───────────────────────────────────────────────────────────────

function Widget() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState(null);
  const [email, setEmail] = useState("");

  const handleLogin = (tok, em) => {
    setToken(tok);
    setEmail(em);
  };

  const handleLogout = () => {
    setToken(null);
    setEmail("");
  };

  return (
    <div style={S.bubble}>
      {open && (
        <div style={S.panel}>
          <div style={S.header}>
            <span>Maintainer's Copilot</span>
            {token && (
              <button
                onClick={handleLogout}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "#fff",
                  cursor: "pointer",
                  fontSize: 11,
                  opacity: 0.8,
                }}
              >
                sign out
              </button>
            )}
          </div>
          {token ? (
            <ChatView token={token} onLogout={handleLogout} />
          ) : (
            <LoginForm onLogin={handleLogin} />
          )}
        </div>
      )}
      <button
        onClick={() => setOpen((o) => !o)}
        style={S.toggleBtn}
        aria-label="Toggle chat"
      >
        {open ? "✕" : "💬"}
      </button>
    </div>
  );
}

const root = createRoot(document.getElementById("root"));
root.render(<Widget />);
