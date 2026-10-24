(function () {
  const config = window.RAG_CHAT_CONFIG || {};
  const API_BASE = (config.apiBase || "").replace(/\/+$/, ""); // strip trailing slash(es)
  const CATEGORY = config.category || null;
  const TITLE = config.title || "Ask us anything";
  const SUBTITLE = config.subtitle || "Online";
  const WELCOME = config.welcome || null; // optional first bot message
  const SUGGESTIONS = Array.isArray(config.suggestions) ? config.suggestions : []; // quick-reply chips
  const BADGE_TEXT = config.badge || null; // optional trust strip under the header
  const BADGE_LINK_TEXT = config.badgeLinkText || null;
  const BADGE_LINK_URL = config.badgeLinkUrl || null;
  const PLACEHOLDER = config.placeholder || "Type your question...";
  const LOGO_URL = config.logo || null;
  const ACCENT = config.accent || null;
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

  const ICON = {
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.6 8.6 0 0 1-3.7-.8L3 21l1.9-5.3A8.4 8.4 0 0 1 3 11.5 8.5 8.5 0 0 1 12 3a8.5 8.5 0 0 1 9 8.5z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    reset: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>',
  };

  const launcher = document.createElement("button");
  launcher.id = "rag-chat-launcher";
  launcher.setAttribute("aria-label", "Open chat");
  launcher.innerHTML =
    ICON.chat.replace("<svg", '<svg class="rc-ic-chat"') + ICON.close.replace("<svg", '<svg class="rc-ic-close"');
  root.appendChild(launcher);

  const panel = document.createElement("div");
  panel.id = "rag-chat-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", TITLE);
  panel.innerHTML = `
    <div id="rag-chat-header">
      <div class="rc-logo"><span class="rc-online"></span></div>
      <div class="rc-head-text">
        <div class="rc-title"></div>
        <div class="rc-sub"><span class="rc-dot"></span><span class="rc-sub-text"></span></div>
      </div>
      <button class="rc-icon-btn" id="rag-chat-reset" aria-label="Start a new conversation" title="New conversation">${ICON.reset}</button>
      <button class="rc-icon-btn" id="rag-chat-close" aria-label="Close chat" title="Close">${ICON.close}</button>
    </div>
    <div id="rag-chat-badge">${ICON.shield}<span class="rc-badge-text"></span></div>
    <div id="rag-chat-messages" aria-live="polite"></div>
    <div id="rag-chat-typing" class="rag-typing"><span></span><span></span><span></span></div>
    <div id="rag-chat-input-row">
      <input id="rag-chat-input" type="text" maxlength="2000" autocomplete="off" />
      <button id="rag-chat-send" aria-label="Send message">${ICON.send}</button>
    </div>
  `;
  root.appendChild(panel);

  // text via textContent (never innerHTML) so config values can't inject markup
  panel.querySelector(".rc-title").textContent = TITLE;
  panel.querySelector(".rc-sub-text").textContent = SUBTITLE;
  panel.querySelector("#rag-chat-input").placeholder = PLACEHOLDER;
  const logoEl = panel.querySelector(".rc-logo");
  if (LOGO_URL) {
    const img = document.createElement("img");
    img.src = LOGO_URL;
    img.alt = "";
    logoEl.insertBefore(img, logoEl.firstChild);
  } else {
    logoEl.insertBefore(document.createTextNode((TITLE.trim()[0] || "S").toUpperCase()), logoEl.firstChild);
  }
  if (BADGE_TEXT) {
    const badge = panel.querySelector("#rag-chat-badge");
    badge.querySelector(".rc-badge-text").textContent = BADGE_TEXT;
    if (BADGE_LINK_TEXT && BADGE_LINK_URL && /^(https?:\/\/|\/)/.test(BADGE_LINK_URL)) {
      const a = document.createElement("a");
      a.textContent = BADGE_LINK_TEXT;
      a.href = BADGE_LINK_URL;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      badge.appendChild(a);
    }
    badge.classList.add("show");
  }
  if (ACCENT && /^#[0-9a-fA-F]{3,8}$/.test(ACCENT)) {
    launcher.style.setProperty("--rc-accent", ACCENT);
    panel.style.setProperty("--rc-accent", ACCENT);
  }

  const messagesEl = panel.querySelector("#rag-chat-messages");
  const inputEl = panel.querySelector("#rag-chat-input");
  const sendBtn = panel.querySelector("#rag-chat-send");
  const typingEl = panel.querySelector("#rag-chat-typing");

  function setOpen(open) {
    panel.classList.toggle("open", open);
    launcher.classList.toggle("open", open);
    launcher.setAttribute("aria-label", open ? "Close chat" : "Open chat");
    if (open) {
      if (!messagesEl.children.length) showWelcome();
      ensureSession().catch(() => {}); // warm up; errors surface on first send
      setTimeout(() => inputEl.focus(), 200);
    }
  }

  function showWelcome() {
    if (WELCOME) addMessage("assistant", WELCOME, "Just now");
    if (WELCOME && SUGGESTIONS.length) {
      const wrap = document.createElement("div");
      wrap.className = "rc-chips";
      SUGGESTIONS.forEach((q) => {
        const b = document.createElement("button");
        b.className = "rc-chip";
        b.type = "button";
        b.textContent = q;
        b.addEventListener("click", () => {
          inputEl.value = q;
          sendMessage();
        });
        wrap.appendChild(b);
      });
      messagesEl.appendChild(wrap);
    }
  }

  launcher.addEventListener("click", () => setOpen(!panel.classList.contains("open")));
  panel.querySelector("#rag-chat-close").addEventListener("click", () => setOpen(false));
  panel.querySelector("#rag-chat-reset").addEventListener("click", () => {
    // new conversation = new server session (server-side history is per session)
    clearSession();
    messagesEl.innerHTML = "";
    showWelcome();
    ensureSession().catch(() => {});
    inputEl.focus();
  });

  function syncSendState() {
    sendBtn.classList.toggle("ready", inputEl.value.trim().length > 0);
  }
  inputEl.addEventListener("input", syncSendState);

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

  function timeLabel() {
    try {
      return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    } catch (e) {
      return "";
    }
  }

  // returns the bubble element (streaming code updates its innerHTML)
  function addMessage(role, text, label) {
    const row = document.createElement("div");
    row.className = "rc-row " + role;
    const el = document.createElement("div");
    el.className = "rag-msg " + role;
    if (role === "assistant") el.innerHTML = renderMarkdown(text);
    else el.textContent = text;
    const t = document.createElement("div");
    t.className = "rc-time";
    t.textContent = label || timeLabel();
    row.appendChild(el);
    row.appendChild(t);
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  function setTyping(on) {
    typingEl.style.display = on ? "flex" : "none";
    if (on) setTimeout(() => (messagesEl.scrollTop = messagesEl.scrollHeight), 0);
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
    syncSendState();
    sendBtn.disabled = true;
    messagesEl.querySelectorAll(".rc-chips").forEach((c) => c.remove()); // chips only before the first question
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
