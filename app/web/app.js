(() => {
  "use strict";
  const POLL_MS = 2000;
  const state = {
    token: new URLSearchParams(location.search).get("token") || "",
    device: "pc",
    ids: new Set(),
    newestId: "",
    oldestId: "",
    objectUrls: new Set(),
    modalObjectUrl: "",
    pairingObjectUrl: "",
    modalRequest: 0,
  };
  const el = Object.fromEntries(["status-dot", "connection-label", "identity-copy", "device-select", "message-list", "empty-state", "feedback", "composer", "message-input", "send-button", "image-input", "upload-queue", "storage-stats", "load-more", "pairing-card", "pairing-qr", "qr-frame", "image-modal", "modal-image", "modal-download", "modal-close", "app-version"].map((id) => [id.replaceAll("-", "_"), document.getElementById(id)]));
  const auth = (headers = {}) => {
    const result = new Headers(headers);
    result.set("Authorization", `Bearer ${state.token}`);
    return result;
  };
  const assetUrl = (id, download = false) => `/api/assets/${encodeURIComponent(id)}${download ? "?download=1" : ""}`;
  const formatBytes = (value) => { if (!value) return "0 B"; const u = ["B", "KB", "MB", "GB"], i = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 3); return `${(value / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`; };
  function feedback(text = "", error = false) { el.feedback.textContent = text; el.feedback.classList.toggle("is-error", error); }
  function connection(kind, text) { el.status_dot.className = `status-dot is-${kind}`; el.connection_label.textContent = text; }
  async function api(path, options = {}) {
    if (!state.token) throw new Error("Open the current QR pairing link.");
    const response = await fetch(path, { ...options, headers: auth(options.headers) });
    if (!response.ok) {
      let detail = "";
      try { detail = (await response.json()).detail || ""; } catch (_) {}
      if (response.status === 401) connection("auth", "Needs pairing");
      throw new Error(detail || `Request failed (${response.status}).`);
    }
    return response;
  }
  function makeObjectUrl(blob) {
    const url = URL.createObjectURL(blob);
    state.objectUrls.add(url);
    return url;
  }
  function releaseObjectUrl(url) {
    if (!url) return;
    URL.revokeObjectURL(url);
    state.objectUrls.delete(url);
  }
  function releaseAllObjectUrls() {
    [...state.objectUrls].forEach((url) => URL.revokeObjectURL(url));
    state.objectUrls.clear();
    state.modalObjectUrl = "";
    state.pairingObjectUrl = "";
  }
  async function getAssetBlob(asset, download = false) {
    return (await api(assetUrl(asset.id, download))).blob();
  }
  async function getAssetObjectUrl(asset) {
    return makeObjectUrl(await getAssetBlob(asset));
  }
  async function loadVersion() {
    try {
      const response = await fetch("/api/version", { cache: "no-store" });
      if (!response.ok) throw new Error("Version unavailable");
      const data = await response.json();
      const version = typeof data.version === "string" ? data.version.trim() : "";
      const build = typeof data.build === "string" ? data.build.trim() : "";
      if (!version || !build) throw new Error("Version metadata unavailable");
      el.app_version.textContent = `Rainier Link v${version} · ${build}`;
    } catch (_) { /* Keep the compact default when the public endpoint is unavailable. */ }
  }
  function action(label, handler) { const b = document.createElement("button"); b.type = "button"; b.className = "message-action"; b.textContent = label; b.onclick = handler; return b; }
  function previewable(asset) { return asset && !["heic", "heif"].includes((asset.extension || "").toLowerCase()); }
  function closePreview() {
    state.modalRequest += 1;
    releaseObjectUrl(state.modalObjectUrl);
    state.modalObjectUrl = "";
    el.modal_image.removeAttribute("src");
    el.modal_image.hidden = true;
    if (el.image_modal.open) el.image_modal.close();
  }
  async function openPreview(asset) {
    const request = ++state.modalRequest;
    releaseObjectUrl(state.modalObjectUrl);
    state.modalObjectUrl = "";
    el.modal_image.removeAttribute("src");
    el.modal_image.alt = asset.original_filename || "Image";
    el.modal_image.hidden = true;
    el.modal_download.onclick = (event) => { event.preventDefault(); downloadAsset(asset); };
    if (!el.image_modal.open) el.image_modal.showModal();
    try {
      const url = await getAssetObjectUrl(asset);
      if (request !== state.modalRequest || !el.image_modal.open) { releaseObjectUrl(url); return; }
      state.modalObjectUrl = url;
      el.modal_image.src = url;
      el.modal_image.hidden = false;
    } catch (error) {
      if (request === state.modalRequest) feedback(`Preview failed: ${error.message}`, true);
    }
  }
  function genericPreview() {
    const generic = document.createElement("div");
    generic.className = "asset-generic";
    generic.textContent = "Image preview unavailable";
    return generic;
  }
  function assetCard(asset) {
    const card = document.createElement("div");
    card.className = "asset-card";
    if (previewable(asset)) {
      const image = document.createElement("img");
      image.alt = asset.original_filename || "Shared image";
      image.loading = "lazy";
      let imageUrl = "";
      image.onclick = () => openPreview(asset);
      image.onerror = () => {
        releaseObjectUrl(imageUrl);
        imageUrl = "";
        if (image.isConnected) image.replaceWith(genericPreview());
      };
      card.append(image);
      getAssetObjectUrl(asset).then((url) => {
        if (!card.isConnected) { releaseObjectUrl(url); return; }
        imageUrl = url;
        image.src = url;
      }).catch(() => {
        if (image.isConnected) image.replaceWith(genericPreview());
      });
    } else card.append(genericPreview());
    const title = document.createElement("strong");
    title.textContent = asset.original_filename || "Image";
    const size = document.createElement("span");
    size.textContent = formatBytes(asset.size);
    const tools = document.createElement("div");
    tools.className = "asset-actions";
    if (previewable(asset)) tools.append(action("View", () => openPreview(asset)));
    tools.append(action("Download", () => downloadAsset(asset)));
    card.append(title, size, tools);
    return card;
  }
  async function downloadAsset(asset) {
    try {
      const url = makeObjectUrl(await getAssetBlob(asset, true));
      const link = document.createElement("a");
      link.href = url;
      link.download = asset.original_filename || "rainier-link-image";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => releaseObjectUrl(url), 1000);
    } catch (error) {
      feedback(`Download failed: ${error.message}`, true);
    }
  }
  function addMessage(message, prepend = false) { if (!message || state.ids.has(message.id)) return; state.ids.add(message.id); el.empty_state.hidden = true; const item = document.createElement("article"), own = message.sender === state.device; item.className = `message ${own ? "is-own" : "is-other"}`; const meta = document.createElement("div"); meta.className = "message-meta"; const date = new Date(message.created_at); meta.textContent = `${own ? "You" : message.sender === "pc" ? "PC" : "iPhone"} · ${Number.isNaN(date) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`; item.append(meta); if (message.type === "image" && message.asset) item.append(assetCard(message.asset)); else { const bubble = document.createElement("div"); bubble.className = "message-bubble"; bubble.textContent = message.content || ""; item.append(bubble); } item.append(action("Delete", () => deleteMessage(message))); if (prepend) el.message_list.prepend(item); else el.message_list.append(item); }
  function renderInitial(messages) {
    [...messages].reverse().forEach((message) => addMessage(message));
    if (messages.length) {
      state.newestId = messages[0].id;
      state.oldestId = messages.at(-1).id;
    }
    el.load_more.hidden = messages.length < 50;
  }
  function appendRecent(messages) {
    messages.forEach((message) => addMessage(message));
    if (messages.length) state.newestId = messages.at(-1).id;
  }
  function prependOlder(messages) {
    messages.forEach((message) => addMessage(message, true));
    if (messages.length) state.oldestId = messages.at(-1).id;
  }
  async function initial() { const body = await (await api("/api/messages?limit=50")).json(); renderInitial(body.messages); }
  async function poll() { try { if (state.newestId) { const body = await (await api(`/api/messages?after=${encodeURIComponent(state.newestId)}&limit=50`)).json(); appendRecent(body.messages); } connection("connected", "Connected"); } catch (error) { connection("offline", "Offline"); feedback(error.message, true); } finally { setTimeout(poll, POLL_MS); } }
  async function older() { if (!state.oldestId) return; const body = await (await api(`/api/messages?before=${encodeURIComponent(state.oldestId)}&limit=50`)).json(); prependOlder(body.messages); el.load_more.hidden = body.messages.length < 50; }
  async function deleteMessage(message) { const suffix = message.type === "image" ? " This can delete the local image when it has no other references." : ""; if (!confirm(`Delete this message?${suffix}`)) return; try { await api(`/api/messages/${encodeURIComponent(message.id)}`, { method: "DELETE" }); location.reload(); } catch (error) { feedback(error.message, true); } }
  function files() { return [...el.image_input.files]; }
  function drawQueue(list) { el.upload_queue.replaceChildren(); el.upload_queue.hidden = !list.length; list.forEach((file) => { const row = document.createElement("div"); row.className = "upload-row"; row.dataset.file = file.name; row.textContent = `${file.name} · ${formatBytes(file.size)} · Ready`; el.upload_queue.append(row); }); }
  function upload(file) { return new Promise((resolve, reject) => { const data = new FormData(); data.append("file", file, file.name); data.append("sender", state.device); const request = new XMLHttpRequest(), row = [...el.upload_queue.children].find((node) => node.dataset.file === file.name); request.open("POST", "/api/assets/images"); request.setRequestHeader("Authorization", `Bearer ${state.token}`); request.upload.onprogress = (event) => { if (row && event.lengthComputable) row.textContent = `${file.name} · ${Math.round(event.loaded / event.total * 100)}% uploading`; }; request.onload = () => { if (request.status >= 200 && request.status < 300) { if (row) row.textContent = `${file.name} · Complete`; resolve(); return; } let detail = ""; try { detail = JSON.parse(request.responseText || "{}").detail || ""; } catch (_) {} reject(new Error(detail || `Upload failed (${request.status}).`)); }; request.onerror = () => reject(new Error("Upload interrupted.")); request.ontimeout = () => reject(new Error("Upload timed out.")); request.send(data); }); }
  async function uploadFiles() { for (const file of files()) { try { await upload(file); } catch (error) { feedback(`${file.name}: ${error.message}`, true); } } el.image_input.value = ""; await stats(); await initial(); }
  async function stats() { try { const data = await (await api("/api/storage/stats")).json(); el.storage_stats.textContent = `${formatBytes(data.image_bytes)} · ${data.asset_count} images`; } catch (_) { el.storage_stats.textContent = "Unavailable"; } }
  async function sendText(event) { event.preventDefault(); const content = el.message_input.value; if (!content.trim()) return; el.send_button.disabled = true; try { const body = await (await api("/api/messages", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sender: state.device, type: "text", content }) })).json(); addMessage(body.message); state.newestId = body.message.id; el.message_input.value = ""; } catch (error) { feedback(error.message, true); } finally { el.send_button.disabled = false; } }
  async function pairingQr() { try { const url = makeObjectUrl(await (await api("/api/pairing/qr")).blob()); releaseObjectUrl(state.pairingObjectUrl); state.pairingObjectUrl = url; el.pairing_qr.src = url; } catch (_) { el.qr_frame.hidden = true; } }
  function init() { loadVersion(); state.device = ["localhost", "127.0.0.1"].includes(location.hostname) ? "pc" : "iphone"; el.device_select.value = state.device; const updateDevice = () => { state.device = el.device_select.value; el.identity_copy.textContent = `Messages from this browser are labeled ${state.device === "pc" ? "PC" : "iPhone"}.`; }; updateDevice(); el.device_select.onchange = updateDevice; el.composer.onsubmit = sendText; el.image_input.onchange = () => { drawQueue(files()); uploadFiles(); }; el.load_more.onclick = () => older().catch((error) => feedback(error.message, true)); el.modal_close.onclick = closePreview; el.image_modal.addEventListener("close", closePreview); el.image_modal.onclick = (event) => { if (event.target === el.image_modal) closePreview(); }; window.addEventListener("beforeunload", releaseAllObjectUrls); if (!state.token) { el.pairing_card.hidden = true; connection("auth", "Needs pairing"); feedback("Open the current QR pairing link.", true); return; } Promise.all([initial(), stats(), pairingQr()]).then(() => { connection("connected", "Connected"); poll(); }).catch((error) => { connection("offline", "Offline"); feedback(error.message, true); }); }
  init();
})();
