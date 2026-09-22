(() => {
  "use strict";

  const POLL_MS = 2000;
  const MAX_ASSET_URLS = 20;
  const state = {
    token: new URLSearchParams(location.search).get("token") || "",
    device: "pc", ids: new Set(), syncCursor: "", oldestId: "", objectUrls: new Set(),
    assetUrls: new Map(), assetUrlRequests: new Map(), assetObserver: null, assetLoaders: new WeakMap(),
    modalObjectUrl: "", pairingObjectUrl: "", modalRequest: 0, uploadQueue: [], uploadRunning: false,
  };
  const ids = ["status-dot", "connection-label", "identity-copy", "device-select", "message-list", "empty-state", "feedback", "composer", "message-input", "send-button", "image-input", "file-input", "upload-queue", "storage-stats", "load-more", "pairing-card", "pairing-qr", "qr-frame", "image-modal", "modal-image", "modal-download", "modal-close", "app-version"];
  const el = Object.fromEntries(ids.map((id) => [id.replaceAll("-", "_"), document.getElementById(id)]));
  const auth = (headers = {}) => { const result = new Headers(headers); if (state.token) result.set("Authorization", `Bearer ${state.token}`); return result; };
  const assetUrl = (id, download = false) => `/api/assets/${encodeURIComponent(id)}${download ? "?download=1" : ""}`;
  const formatBytes = (value) => { if (!value) return "0 B"; const units = ["B", "KB", "MB", "GB"], index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 3); return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; };
  const extension = (asset) => (asset?.extension || asset?.original_filename?.split(".").pop() || "").replace(/^\./, "").toLowerCase();
  const isImage = (message) => message?.type === "image";
  const isFile = (message) => message?.type === "file";
  function feedback(text = "", error = false) { el.feedback.textContent = text; el.feedback.classList.toggle("is-error", error); }
  function connection(kind, text) { el.status_dot.className = `status-dot is-${kind}`; el.connection_label.textContent = text; }
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, credentials: "same-origin", headers: auth(options.headers) });
    if (!response.ok) { let detail = ""; try { detail = (await response.json()).detail || ""; } catch (_) {} if (response.status === 401) connection("auth", "Needs pairing"); const error = new Error(detail || `Request failed (${response.status}).`); error.status = response.status; throw error; }
    return response;
  }
  function makeObjectUrl(blob) { const url = URL.createObjectURL(blob); state.objectUrls.add(url); return url; }
  function releaseObjectUrl(url) { if (!url) return; URL.revokeObjectURL(url); state.objectUrls.delete(url); }
  function releaseAssetUrls() { for (const { url } of state.assetUrls.values()) releaseObjectUrl(url); state.assetUrls.clear(); state.assetUrlRequests.clear(); }
  function releaseAllObjectUrls() { releaseAssetUrls(); [...state.objectUrls].forEach((url) => URL.revokeObjectURL(url)); state.objectUrls.clear(); state.modalObjectUrl = ""; state.pairingObjectUrl = ""; }
  async function getAssetBlob(asset) { return (await api(assetUrl(asset.id))).blob(); }
  async function getAssetObjectUrl(asset) {
    const cached = state.assetUrls.get(asset.id); if (cached) { cached.lastUsed = Date.now(); return cached.url; }
    const pending = state.assetUrlRequests.get(asset.id); if (pending) return pending;
    const request = (async () => {
      const url = makeObjectUrl(await getAssetBlob(asset)); state.assetUrls.set(asset.id, { url, lastUsed: Date.now() });
      while (state.assetUrls.size > MAX_ASSET_URLS) { const oldest = [...state.assetUrls.entries()].sort(([, a], [, b]) => a.lastUsed - b.lastUsed)[0]; if (!oldest) break; state.assetUrls.delete(oldest[0]); releaseObjectUrl(oldest[1].url); }
      return url;
    })();
    state.assetUrlRequests.set(asset.id, request);
    try { return await request; } finally { if (state.assetUrlRequests.get(asset.id) === request) state.assetUrlRequests.delete(asset.id); }
  }
  async function loadVersion() { try { const response = await fetch("/api/version", { cache: "no-store", credentials: "same-origin" }); if (!response.ok) throw new Error(); const data = await response.json(); if (data.version && data.build) el.app_version.textContent = `Rainier Link v${data.version} · ${data.build}`; } catch (_) {} }
  function action(label, handler) { const button = document.createElement("button"); button.type = "button"; button.className = "message-action"; button.textContent = label; button.onclick = handler; return button; }
  function previewable(asset) { return asset && !["heic", "heif"].includes(extension(asset)); }
  function closePreview() { state.modalRequest += 1; state.modalObjectUrl = ""; el.modal_image.removeAttribute("src"); el.modal_image.hidden = true; if (el.image_modal.open) el.image_modal.close(); }
  async function openPreview(asset) {
    const request = ++state.modalRequest; el.modal_image.removeAttribute("src"); el.modal_image.alt = asset.original_filename || "Image"; el.modal_image.hidden = true; el.modal_download.href = assetUrl(asset.id, true); el.modal_download.download = asset.original_filename || "rainier-link-image"; if (!el.image_modal.open) el.image_modal.showModal();
    try { const url = await getAssetObjectUrl(asset); if (request !== state.modalRequest || !el.image_modal.open) return; state.modalObjectUrl = url; el.modal_image.src = url; el.modal_image.hidden = false; } catch (error) { if (request === state.modalRequest) feedback(`Preview failed: ${error.message}`, true); }
  }
  function genericPreview(text = "Preview unavailable") { const generic = document.createElement("div"); generic.className = "asset-generic"; generic.textContent = text; return generic; }
  function setupAssetObserver() { if (!("IntersectionObserver" in window)) return; state.assetObserver = new IntersectionObserver((entries) => entries.forEach((entry) => { if (!entry.isIntersecting) return; const loader = state.assetLoaders.get(entry.target); state.assetObserver.unobserve(entry.target); state.assetLoaders.delete(entry.target); if (loader) loader(); }), { root: null, rootMargin: "500px 0px", threshold: 0 }); }
  function observeAssetPlaceholder(placeholder, loader) { if (!state.assetObserver) { loader(); return; } state.assetLoaders.set(placeholder, loader); state.assetObserver.observe(placeholder); }
  function fileIcon(asset) { const ext = extension(asset); return { pdf: "PDF", doc: "DOC", docx: "DOC", ppt: "PPT", pptx: "PPT", xls: "XLS", xlsx: "XLS", zip: "ZIP" }[ext] || "FILE"; }
  function downloadLink(asset) { const link = document.createElement("a"); link.className = "message-action asset-download"; link.href = assetUrl(asset.id, true); link.download = asset.original_filename || "rainier-link-file"; link.textContent = "Download"; return link; }
  function assetCard(asset, kind = "image") {
    const card = document.createElement("div"); card.className = `asset-card ${kind === "file" ? "file-card" : "image-card"}`;
    if (kind === "image") {
      if (previewable(asset)) { const placeholder = genericPreview("Loading image preview…"); placeholder.classList.add("asset-placeholder"); card.append(placeholder); observeAssetPlaceholder(placeholder, () => { if (!placeholder.isConnected || placeholder.dataset.loading) return; placeholder.dataset.loading = "1"; getAssetObjectUrl(asset).then((url) => { if (!card.isConnected) return; const image = document.createElement("img"); image.alt = asset.original_filename || "Shared image"; image.loading = "lazy"; image.src = url; image.onclick = () => openPreview(asset); image.onerror = () => { if (image.isConnected) image.replaceWith(genericPreview()); }; placeholder.replaceWith(image); }).catch(() => { if (placeholder.isConnected) placeholder.replaceWith(genericPreview()); }); }); }
      else card.append(genericPreview("Image preview unavailable"));
    } else { const icon = document.createElement("span"); icon.className = "file-icon"; icon.textContent = fileIcon(asset); card.append(icon); }
    const title = document.createElement("strong"); title.textContent = asset.original_filename || (kind === "file" ? "File" : "Image");
    const details = document.createElement("span"); details.textContent = `${asset.mime_type || "application/octet-stream"} · ${formatBytes(asset.size)}`;
    const tools = document.createElement("div"); tools.className = "asset-actions"; if (kind === "image" && previewable(asset)) tools.append(action("View", () => openPreview(asset))); tools.append(downloadLink(asset)); card.append(title, details, tools); return card;
  }
  function addMessage(message, prepend = false) {
    if (!message || state.ids.has(message.id)) return; state.ids.add(message.id); el.empty_state.hidden = true; const item = document.createElement("article"), own = message.sender === state.device; item.className = `message ${own ? "is-own" : "is-other"}`; const meta = document.createElement("div"); meta.className = "message-meta"; const date = new Date(message.created_at); meta.textContent = `${own ? "You" : message.sender === "pc" ? "PC" : "iPhone"} · ${Number.isNaN(date) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`; item.append(meta); if (message.asset && (isImage(message) || isFile(message))) item.append(assetCard(message.asset, message.type)); else { const bubble = document.createElement("div"); bubble.className = "message-bubble"; bubble.textContent = message.content || ""; item.append(bubble); } item.append(action("Delete", () => deleteMessage(message))); if (prepend) el.message_list.prepend(item); else el.message_list.append(item);
  }
  function renderInitial(messages) { [...messages].reverse().forEach((message) => addMessage(message)); if (messages.length) { state.syncCursor = messages[0].id; state.oldestId = messages.at(-1).id; } el.load_more.hidden = messages.length < 50; }
  function appendRecent(messages) { messages.forEach((message) => addMessage(message)); }
  function prependOlder(messages) { messages.forEach((message) => addMessage(message, true)); if (messages.length) state.oldestId = messages.at(-1).id; }
  async function initial() { const body = await (await api("/api/messages?limit=50")).json(); renderInitial(body.messages); }
  async function poll() { try { const path = state.syncCursor ? `/api/messages?after=${encodeURIComponent(state.syncCursor)}&limit=50` : "/api/messages?limit=50"; const body = await (await api(path)).json(); if (state.syncCursor) appendRecent(body.messages); else [...body.messages].reverse().forEach((message) => addMessage(message)); if (body.messages.length) { if (!state.syncCursor) { state.oldestId = body.messages.at(-1).id; el.load_more.hidden = body.messages.length < 50; } state.syncCursor = state.syncCursor ? body.messages.at(-1).id : body.messages[0].id; } connection("connected", "Connected"); } catch (error) { connection(error.status === 401 ? "auth" : "offline", error.status === 401 ? "Needs pairing" : "Offline"); feedback(error.message, true); } finally { setTimeout(poll, POLL_MS); } }
  async function older() { if (!state.oldestId) return; const body = await (await api(`/api/messages?before=${encodeURIComponent(state.oldestId)}&limit=50`)).json(); prependOlder(body.messages); el.load_more.hidden = body.messages.length < 50; }
  async function deleteMessage(message) { const suffix = message.type === "image" || message.type === "file" ? " This can delete the local asset when it has no other references." : ""; if (!confirm(`Delete this message?${suffix}`)) return; try { await api(`/api/messages/${encodeURIComponent(message.id)}`, { method: "DELETE" }); location.reload(); } catch (error) { feedback(error.message, true); } }
  function queueFiles(files, kind) { [...files].forEach((file) => state.uploadQueue.push({ id: crypto.randomUUID(), file, kind, status: "QUEUED", progress: 0, error: "" })); renderQueue(); processQueue(); }
  function renderQueue() { el.upload_queue.replaceChildren(); el.upload_queue.hidden = !state.uploadQueue.length; state.uploadQueue.forEach((entry) => { const row = document.createElement("div"); row.className = `upload-row is-${entry.status.toLowerCase()}`; const label = document.createElement("span"); label.textContent = `${entry.file.name} · ${formatBytes(entry.file.size)} · ${entry.status}${entry.status === "UPLOADING" ? ` ${entry.progress}%` : ""}${entry.error ? ` · ${entry.error}` : ""}`; row.append(label); if (entry.status === "FAILED") row.append(action("Retry", () => { entry.status = "QUEUED"; entry.error = ""; renderQueue(); processQueue(); })); el.upload_queue.append(row); }); }
  function upload(entry) { return new Promise((resolve, reject) => { const data = new FormData(); data.append("file", entry.file, entry.file.name); data.append("sender", state.device); const request = new XMLHttpRequest(); request.open("POST", entry.kind === "image" ? "/api/assets/images" : "/api/assets/files"); request.withCredentials = true; if (state.token) request.setRequestHeader("Authorization", `Bearer ${state.token}`); request.upload.onprogress = (event) => { if (event.lengthComputable) { entry.progress = Math.round(event.loaded / event.total * 100); renderQueue(); } }; request.onload = () => { if (request.status >= 200 && request.status < 300) { try { resolve(JSON.parse(request.responseText || "{}")); } catch (_) { resolve({}); } return; } let detail = ""; try { detail = JSON.parse(request.responseText || "{}").detail || ""; } catch (_) {} reject(new Error(detail || `Upload failed (${request.status}).`)); }; request.onerror = () => reject(new Error("Upload interrupted.")); request.ontimeout = () => reject(new Error("Upload timed out.")); request.send(data); }); }
  async function processQueue() { if (state.uploadRunning) return; state.uploadRunning = true; try { for (const entry of state.uploadQueue) { if (entry.status !== "QUEUED") continue; entry.status = "UPLOADING"; entry.progress = 0; renderQueue(); try { const body = await upload(entry); entry.status = "PROCESSING"; renderQueue(); entry.status = "COMPLETED"; entry.progress = 100; if (body.message) addMessage({ ...body.message, asset: body.asset }); } catch (error) { entry.status = "FAILED"; entry.error = error.message; feedback(`${entry.file.name}: ${error.message}`, true); } renderQueue(); } } finally { state.uploadRunning = false; await stats(); } }
  async function stats() { try { const data = await (await api("/api/storage/stats")).json(); el.storage_stats.textContent = `${formatBytes(data.total_bytes ?? data.image_bytes)} · ${data.asset_count} assets`; } catch (_) { el.storage_stats.textContent = "Unavailable"; } }
  async function sendText(event) { event.preventDefault(); const content = el.message_input.value; if (!content.trim()) return; el.send_button.disabled = true; try { const body = await (await api("/api/messages", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sender: state.device, type: "text", content }) })).json(); addMessage(body.message); el.message_input.value = ""; } catch (error) { feedback(error.message, true); } finally { el.send_button.disabled = false; } }
  async function pairingQr() { try { const url = makeObjectUrl(await (await api("/api/pairing/qr")).blob()); releaseObjectUrl(state.pairingObjectUrl); state.pairingObjectUrl = url; el.pairing_qr.src = url; } catch (_) { el.qr_frame.hidden = true; } }
  function init() { loadVersion(); setupAssetObserver(); state.device = ["localhost", "127.0.0.1"].includes(location.hostname) ? "pc" : "iphone"; el.device_select.value = state.device; const updateDevice = () => { state.device = el.device_select.value; el.identity_copy.textContent = `Messages from this browser are labeled ${state.device === "pc" ? "PC" : "iPhone"}.`; }; updateDevice(); el.device_select.onchange = updateDevice; el.composer.onsubmit = sendText; el.image_input.onchange = () => { queueFiles(el.image_input.files, "image"); el.image_input.value = ""; }; el.file_input.onchange = () => { queueFiles(el.file_input.files, "file"); el.file_input.value = ""; }; el.load_more.onclick = () => older().catch((error) => feedback(error.message, true)); el.modal_close.onclick = closePreview; el.image_modal.addEventListener("close", closePreview); el.image_modal.onclick = (event) => { if (event.target === el.image_modal) closePreview(); }; window.addEventListener("beforeunload", () => { if (state.assetObserver) state.assetObserver.disconnect(); releaseAllObjectUrls(); }); Promise.all([initial(), stats(), pairingQr()]).then(() => { connection("connected", "Connected"); poll(); }).catch((error) => { connection(error.status === 401 ? "auth" : "offline", error.status === 401 ? "Needs pairing" : "Offline"); feedback(error.message, true); }); }
  init();
})();
