import React, { useState } from "react";
import { createRoot } from "react-dom/client";

const API_BASE = window.__COPILOT_API_BASE__ ?? "http://localhost:8000";

function Widget() {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [log, setLog] = useState([]);

  const send = async () => {
    if (!message.trim()) return;
    const userMsg = message.trim();
    setLog((prev) => [...prev, { role: "user", text: userMsg }]);
    setMessage("");
    try {
      const resp = await fetch(`${API_BASE}/healthz`);
      const data = await resp.json();
      setLog((prev) => [...prev, { role: "assistant", text: JSON.stringify(data) }]);
    } catch {
      setLog((prev) => [...prev, { role: "assistant", text: "Error connecting to API." }]);
    }
  };

  return (
    <div style={{ position: "fixed", bottom: 24, right: 24, zIndex: 9999, fontFamily: "sans-serif" }}>
      {open && (
        <div
          style={{
            width: 340,
            height: 480,
            background: "#fff",
            border: "1px solid #ddd",
            borderRadius: 12,
            boxShadow: "0 4px 24px rgba(0,0,0,0.15)",
            display: "flex",
            flexDirection: "column",
            marginBottom: 8,
          }}
        >
          <div style={{ padding: "12px 16px", borderBottom: "1px solid #eee", fontWeight: 600 }}>
            Maintainer's Copilot
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
            {log.map((m, i) => (
              <div
                key={i}
                style={{
                  marginBottom: 8,
                  textAlign: m.role === "user" ? "right" : "left",
                }}
              >
                <span
                  style={{
                    display: "inline-block",
                    padding: "6px 10px",
                    borderRadius: 8,
                    background: m.role === "user" ? "#0070f3" : "#f0f0f0",
                    color: m.role === "user" ? "#fff" : "#000",
                    fontSize: 13,
                    maxWidth: "80%",
                  }}
                >
                  {m.text}
                </span>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", borderTop: "1px solid #eee", padding: 8, gap: 6 }}>
            <input
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Ask something..."
              style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid #ccc", fontSize: 13 }}
            />
            <button
              onClick={send}
              style={{
                padding: "6px 14px",
                borderRadius: 6,
                background: "#0070f3",
                color: "#fff",
                border: "none",
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              Send
            </button>
          </div>
        </div>
      )}
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: 52,
          height: 52,
          borderRadius: "50%",
          background: "#0070f3",
          color: "#fff",
          border: "none",
          cursor: "pointer",
          fontSize: 22,
          boxShadow: "0 2px 12px rgba(0,0,0,0.2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
        aria-label="Toggle chat"
      >
        {open ? "✕" : "💬"}
      </button>
    </div>
  );
}

const root = createRoot(document.getElementById("root"));
root.render(<Widget />);
