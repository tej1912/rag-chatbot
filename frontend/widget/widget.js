(function () {
  const config = window.RAG_CHAT_CONFIG || {};
  const API_BASE = config.apiBase || "";
  const CATEGORY = config.category || null;
  const DEBUG = !!config.debug; // dev only: server ignores it in production
  const TURNSTILE_SITE_KEY = config.turnstileSiteKey || null; // optional captcha

  // ---- server-issued session (signed token) ----
  const SESSION_KEY = "rag_chat_session";
  let sessionPromise = null;

  function loadStoredSession() {
    try {
      const s = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
      // refresh 5 minutes before the server-side expiry
      if (s && s.token && s.exp > Date.now() + 5 * 60 * 1000) return s;
    } catch (e) {}
    return null;
  }

  function clearSession() {
    localStorage.removeItem(SESSION_KEY);
    sessionPromise = null;
  }

  function loadTurnstileScript() {
    return new Promise((resolve, reject) => {
      if (window.turnstile) return resolve();
      const s = document.createElement("script");
      s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("turnstile script failed to load"));
      document.head.appendChild(s);
    });
  }

  async function getTurnstileToken() {
    await loadTurnstileScript();
    return new Promise((resolve, reject) => {
      const holder = document.createElement("div");
      holder.style.cssText = "position:fixed;bottom:90px;right:20px;z-index:10000";
      document.body.appendChild(holder);
      const timer = setTimeout(() => {
        holder.remove();
        reject(new Error("captcha timeout"));
      }, 30000);
      window.turnstile.render(holder, {
        sitekey: TURNSTILE_SITE_KEY,
        appearance: "interaction-only", // only visible if a challenge is needed
        callback: (token) => {
          clearTimeout(timer);
          holder.remove();
          resolve(token);
        },
        "error-callback": () => {
          clearTimeout(timer);
          holder.remove();
          reject(new Error("captcha error"));
        },
      });
    });
  }

  async function createSession() {
    const body = {};
    if (TURNSTILE_SITE_KEY) body.turnstile_token = await getTurnstileToken();
    const resp = await fetch(`${API_BASE}/v1/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp.status === 429) throw new Error("rate_limited");
    if (!resp.ok) throw new Error("session_failed_" + resp.status);
    const data = await resp.json();
    const session = { token: data.token, exp: Date.now() + data.expires_in * 1000 };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return session;
  }

  function ensureSession() {
    const stored = loadStoredSession();
    if (stored) return Promise.resolve(stored);
    if (!sessionPromise) {
      sessionPromise = createSession().finally(() => {
        sessionPromise = null;
      });
    }
    return sessionPromise;
  }

  // ---- DOM ----
  const root = document.getElementById("rag-chat-widget") || document.body;

  const launcher = document.createElement("button");
  launcher.id = "rag-chat-launcher";
  launcher.textContent = "💬";
  launcher.setAttribute("aria-label", "Open chat");
  root.appendChild(launcher);

  const panel = document.createElement("div");
  panel.id = "rag-chat-panel";
  panel.innerHTML = `
    <div id="rag-chat-header">Ask us anything</div>
    <div id="rag-chat-messages"></div>
    <div id="rag-chat-typing" class="rag-typing" style="display:none">
      <span></span><span></span><span></span>
    </div>
    <div id="rag-chat-input-row">
      <input id="rag-chat-input" type="text" maxlength="2000" placeholder="Type your question..." />
      <button id="rag-chat-send">Send</button>
    </div>
  `;
  root.appendChild(panel);

  const messagesEl = panel.querySelector("#rag-chat-messages");
  const inputEl = panel.querySelector("#rag-chat-input");
  const sendBtn = panel.querySelector("#rag-chat-send");
  const typingEl = panel.querySelector("#rag-chat-typing");

  launcher.addEventListener("click", () => {
    panel.classList.toggle("open");
    if (panel.classList.contains("open")) {
      ensureSession().catch(() => {}); // warm up; errors surface on first send
    }
  });

  // ---- tiny markdown renderer (HTML-escapes first) ----
  function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function inline(text) {
    return text
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+?)`/g, "<code>$1</code>");
  }

  function renderMarkdown(raw) {
    const blocks = escapeHtml(raw).split(/\n{2,}/);
    return blocks
      .map((block) => {
        const lines = block.split("\n").filter((l) => l.trim() !== "");
        const isBullets = lines.length > 0 && lines.every((l) => /^\s*[-*]\s+/.test(l));
        const isNumbered = lines.length > 0 && lines.every((l) => /^\s*\d+\.\s+/.test(l));
        if (isBullets) {
          return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`;
        }
        if (isNumbered) {
          return `<ol>${lines.map((l) => `<li>${inline(l.replace(/^\s*\d+\.\s+/, ""))}</li>`).join("")}</ol>`;
        }
        return `<p>${lines.map(inline).join("<br>")}</p>`;
      })
      .join("");
  }

  function addMessage(role, text) {
    const el = document.createElement("div");
    el.className = "rag-msg " + role;
    if (role === "assistant") el.innerHTML = renderMarkdown(text);
    else el.textContent = text;
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  function setTyping(on) {
    typingEl.style.display = on ? "flex" : "none";
    if (on) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  async function postChat(text, session) {
    return fetch(`${API_BASE}/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": session.token },
      body: JSON.stringify({ message: text, category: CATEGORY, debug: DEBUG }),
    });
  }

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = "";
    sendBtn.disabled = true;
    addMessage("user", text);
    setTyping(true);

    let assistantEl = null;
    let rawAnswer = "";

    try {
      let session = await ensureSession();
      let resp = await postChat(text, session);

      if (resp.status === 401) {
        // token expired or invalid: get a fresh session and retry once
        clearSession();
        session = await ensureSession();
        resp = await postChat(text, session);
      }
      if (resp.status === 429) {
        setTyping(false);
        addMessage("assistant", "You're sending messages a bit fast. Please wait a moment and try again.");
        return;
      }
      if (!resp.ok || !resp.body) throw new Error(`Request failed: ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const blocks = buffer.split("\n\n");
        buffer = blocks.pop();

        for (const block of blocks) {
          const lines = block.split("\n");
          const isDebugEvent = lines.some((l) => l.trim() === "event: debug");
          const dataLine = lines.find((l) => l.startsWith("data: "));
          if (!dataLine) continue;
          const payload = dataLine.slice(6);

          if (isDebugEvent) {
            if (DEBUG) {
              try {
                console.log("[rag-chat retrieval]", JSON.parse(payload));
              } catch (e) {
                console.log("[rag-chat retrieval] (unparsed)", payload);
              }
            }
            continue;
          }
          if (payload === "[DONE]") continue;

          if (!assistantEl) {
            setTyping(false);
            assistantEl = addMessage("assistant", "");
          }
          rawAnswer += payload.replace(/\\n/g, "\n");
          assistantEl.innerHTML = renderMarkdown(rawAnswer);
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }
      }

      if (!assistantEl) {
        setTyping(false);
        addMessage("assistant", "Sorry, I didn't get a response. Please try again.");
      }
    } catch (err) {
      setTyping(false);
      if (!assistantEl) {
        const msg =
          err && err.message === "rate_limited"
            ? "Too many chat sessions started from your network. Please wait a minute and try again."
            : "Sorry, something went wrong reaching the server. Please try again.";
        addMessage("assistant", msg);
      }
      console.error("rag-chat error:", err);
    } finally {
      setTyping(false);
      sendBtn.disabled = false;
    }
  }

  sendBtn.addEventListener("click", sendMessage);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage();
  });
})();
