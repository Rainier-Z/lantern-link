(function () {
  "use strict";

  const STORAGE = {
    token: "rainier_link_token",
    device: "rainier_link_device",
    lastId: "rainier_link_last_message_id"
  };
  const POLL_MS = 1000;
  const state = {
    token: "",
    device: "pc",
    lastId: "",
    messageIds: new Set(),
    pollTimer: null,
    pollInFlight: false,
    sending: false,
    pairingUrl: "",
    qrObjectUrl: ""
  };

  const elements = {
    statusDot: document.getElementById("status-dot"),
    connectionLabel: document.getElementById("connection-label"),
    identityCopy: document.getElementById("identity-copy"),
    deviceSelect: document.getElementById("device-select"),
    messageList: document.getElementById("message-list"),
    emptyState: document.getElementById("empty-state"),
    feedback: document.getElementById("feedback"),
    composer: document.getElementById("composer"),
    input: document.getElementById("message-input"),
    sendButton: document.getElementById("send-button"),
    pairingCard: document.getElementById("pairing-card"),
    pairingAddress: document.getElementById("pairing-address"),
    pairingNote: document.getElementById("pairing-note"),
    pairingQr: document.getElementById("pairing-qr"),
    qrFrame: document.getElementById("qr-frame")
  };

  function readIdentity() {
    const query = new URLSearchParams(window.location.search);
    const requested = query.get("device");
    const saved = window.localStorage.getItem(STORAGE.device);
    const host = window.location.hostname.toLowerCase();
    const localHost = host === "localhost" || host === "127.0.0.1" || host === "[::1]";
    const inferred = localHost ? "pc" : "iphone";
    state.device = requested === "pc" || requested === "iphone"
      ? requested
      : saved === "pc" || saved === "iphone" ? saved : inferred;
    window.localStorage.setItem(STORAGE.device, state.device);
    elements.deviceSelect.value = state.device;
    updateIdentityCopy();
  }

  function readToken() {
    const queryToken = new URLSearchParams(window.location.search).get("token");
    state.token = queryToken || window.localStorage.getItem(STORAGE.token) || "";
    if (queryToken) window.localStorage.setItem(STORAGE.token, queryToken);
  }

  function updateIdentityCopy() {
    const name = state.device === "pc" ? "PC" : "iPhone";
    elements.identityCopy.textContent = `Messages from this browser are labeled ${name}.`;
  }

  function setConnection(kind, label) {
    elements.statusDot.className = `status-dot is-${kind}`;
    elements.connectionLabel.textContent = label;
  }

  function setFeedback(message, isError) {
    elements.feedback.textContent = message || "";
    elements.feedback.classList.toggle("is-error", Boolean(isError));
  }

  async function readResponseError(response) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch (error) {
      detail = "";
    }
    if (response.status === 401) return "Pairing expired. Open Rainier Link with a valid pairing link.";
    if (response.status === 413) return "That message is larger than the 64 KiB limit.";
    if (response.status === 400) return detail || "Write a message before sending.";
    return detail || `The service returned an error (${response.status}).`;
  }

  async function apiFetch(path, options) {
    if (!state.token) {
      setConnection("auth", "Needs pairing");
      throw new Error("A pairing token is required. Open the link from the Rainier Link server.");
    }
    const headers = new Headers(options && options.headers ? options.headers : {});
    headers.set("Authorization", `Bearer ${state.token}`);
    if (options && options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch (error) {
      setConnection("offline", "Offline");
      throw new Error("Rainier Link is unreachable. Check that the server is running and both devices share Wi-Fi.");
    }
    if (!response.ok) {
      if (response.status === 401) setConnection("auth", "Needs pairing");
      throw new Error(await readResponseError(response));
    }
    return response;
  }

  function formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
  }

  function addMessage(message) {
    if (!message || typeof message.id !== "string" || state.messageIds.has(message.id)) return;
    if (message.type !== "text" || typeof message.content !== "string") return;
    state.messageIds.add(message.id);
    elements.emptyState.hidden = true;

    const item = document.createElement("article");
    const own = message.sender === state.device;
    item.className = `message ${own ? "is-own" : "is-other"}`;

    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = `${own ? "You" : message.sender === "pc" ? "PC" : "iPhone"} · ${formatTime(message.created_at)}`;

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    bubble.textContent = message.content;

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-button";
    copy.textContent = "Copy";
    copy.setAttribute("aria-label", "Copy message");
    copy.addEventListener("click", () => copyMessage(message.content, copy));

    item.append(meta, bubble, copy);
    elements.messageList.appendChild(item);
    elements.messageList.scrollTop = elements.messageList.scrollHeight;
  }

  function rememberLastMessage(message) {
    if (!message || typeof message.id !== "string") return;
    state.lastId = message.id;
    window.localStorage.setItem(STORAGE.lastId, state.lastId);
  }

  function renderMessages(messages) {
    if (!Array.isArray(messages)) return;
    messages.forEach((message) => {
      addMessage(message);
      rememberLastMessage(message);
    });
  }

  async function syncMessages() {
    if (state.pollInFlight) return;
    state.pollInFlight = true;
    try {
      const query = state.lastId ? `?after=${encodeURIComponent(state.lastId)}` : "";
      const response = await apiFetch(`/api/messages${query}`, { method: "GET" });
      const body = await response.json();
      renderMessages(body.messages);
      setConnection("connected", "Connected");
      if (!elements.feedback.classList.contains("is-error")) setFeedback("");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Could not refresh messages.", true);
    } finally {
      state.pollInFlight = false;
      window.clearTimeout(state.pollTimer);
      state.pollTimer = window.setTimeout(syncMessages, POLL_MS);
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    if (state.sending) return;
    const content = elements.input.value;
    if (!content.trim()) {
      setFeedback("Write a message before sending.", true);
      elements.input.focus();
      return;
    }
    state.sending = true;
    elements.sendButton.disabled = true;
    setFeedback("Sending…");
    try {
      const response = await apiFetch("/api/messages", {
        method: "POST",
        body: JSON.stringify({ sender: state.device, type: "text", content })
      });
      const body = await response.json();
      addMessage(body.message);
      rememberLastMessage(body.message);
      elements.input.value = "";
      setConnection("connected", "Connected");
      setFeedback("Sent");
      elements.input.focus();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Could not send the message.", true);
    } finally {
      state.sending = false;
      elements.sendButton.disabled = false;
    }
  }

  async function copyMessage(content, button) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(content);
      } else {
        const helper = document.createElement("textarea");
        helper.value = content;
        helper.setAttribute("readonly", "");
        helper.style.position = "fixed";
        helper.style.opacity = "0";
        document.body.appendChild(helper);
        helper.select();
        document.execCommand("copy");
        helper.remove();
      }
      const previous = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = previous; }, 1300);
    } catch (error) {
      setFeedback("Copy is unavailable in this browser. Select the message text manually.", true);
    }
  }

  function resolvePairingUrl(data) {
    if (!data || typeof data !== "object") return "";
    return data.url || data.pairing_url || data.address || "";
  }

  function fallbackPairingUrl() {
    const current = new URL(window.location.href);
    current.searchParams.set("token", state.token);
    current.searchParams.set("device", "iphone");
    return current.toString();
  }

  function setPairingUrl(url) {
    state.pairingUrl = url || fallbackPairingUrl();
    elements.pairingAddress.href = state.pairingUrl;
    elements.pairingAddress.textContent = state.pairingUrl;
  }

  function releaseQrObjectUrl() {
    if (!state.qrObjectUrl) return;
    URL.revokeObjectURL(state.qrObjectUrl);
    state.qrObjectUrl = "";
  }

  function handlePairingQrError() {
    releaseQrObjectUrl();
    elements.qrFrame.hidden = true;
    elements.pairingNote.textContent = "Share the iPhone address above to join this channel.";
  }

  async function loadPairing() {
    if (!state.token) {
      elements.pairingCard.classList.add("is-unavailable");
      return;
    }
    let data = null;
    try {
      const response = await apiFetch("/api/pairing", { method: "GET" });
      data = await response.json();
    } catch (error) {
      // Chat remains usable if an older server does not expose pairing metadata.
    }
    const pairingUrl = resolvePairingUrl(data);
    setPairingUrl(pairingUrl);
    elements.pairingQr.addEventListener("error", handlePairingQrError, { once: true });
    try {
      const qrResponse = await apiFetch("/api/pairing/qr", { method: "GET" });
      releaseQrObjectUrl();
      state.qrObjectUrl = URL.createObjectURL(await qrResponse.blob());
      elements.pairingQr.src = state.qrObjectUrl;
    } catch (error) {
      handlePairingQrError();
    }
  }

  function init() {
    readIdentity();
    readToken();
    state.lastId = window.localStorage.getItem(STORAGE.lastId) || "";
    elements.deviceSelect.addEventListener("change", () => {
      state.device = elements.deviceSelect.value;
      window.localStorage.setItem(STORAGE.device, state.device);
      updateIdentityCopy();
      setFeedback(`This browser is now labeled ${state.device === "pc" ? "PC" : "iPhone"}.`);
    });
    elements.composer.addEventListener("submit", sendMessage);
    elements.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        elements.composer.requestSubmit();
      }
    });
    elements.pairingAddress.addEventListener("click", (event) => {
      if (!state.pairingUrl) event.preventDefault();
    });
    window.addEventListener("pagehide", releaseQrObjectUrl);
    if (state.token) {
      syncMessages();
      loadPairing();
    } else {
      setConnection("auth", "Needs pairing");
      setFeedback("Open the pairing link from the Rainier Link server to connect.", true);
      setPairingUrl("");
    }
  }

  init();
}());
