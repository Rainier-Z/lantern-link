(() => {
  "use strict";

  const POLL_MS = 2000;
  const MAX_ASSET_URLS = 20;
  const state = {
    token: new URLSearchParams(location.search).get("token") || "",
    device: "pc", ids: new Set(), syncCursor: "", oldestId: "", objectUrls: new Set(),
    assetUrls: new Map(), assetUrlRequests: new Map(), assetObserver: null, assetLoaders: new WeakMap(),
    modalObjectUrl: "", pairingObjectUrl: "", modalRequest: 0, composerEntries: [], uploadRunning: false, clientEntrySeq: 0, textSending: false, scrollFrame: 0,
    historyMode: "date", historyRequest: 0, historySearchTimer: 0, historyReturnFocus: null, historyItems: [], historyCursor: "", historyHasMore: false, historyLoading: false, toastTimer: 0,
  };
  const ids = ["app-shell", "status-dot", "connection-label", "identity-copy", "device-select", "message-list", "empty-state", "feedback", "composer", "message-input", "send-button", "file-input", "file-pick-button", "composer-batch", "load-more", "pairing-card", "pairing-qr", "qr-frame", "image-modal", "modal-image", "modal-download", "modal-close", "app-version", "history-toggle", "history-overlay", "history-drawer", "history-close", "history-tab-date", "history-tab-file", "history-type", "history-format", "history-search", "history-status", "history-list", "history-more", "batch-toast"];
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
    if (!response.ok) { if (response.status === 401) connection("auth", "Needs pairing"); const error = new Error(`Request failed (${response.status}).`); error.status = response.status; throw error; }
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
  async function loadVersion() { try { const response = await fetch("/api/version", { cache: "no-store", credentials: "same-origin" }); if (!response.ok) throw new Error(); const data = await response.json(); if (data.version && data.build) el.app_version.textContent = `private_send v${data.version} · ${data.build}`; } catch (_) {} }
  function action(label, handler) { const button = document.createElement("button"); button.type = "button"; button.className = "message-action"; button.textContent = label; button.onclick = handler; return button; }
  function previewable(asset) { return asset && !["heic", "heif"].includes(extension(asset)); }
  function closePreview() { state.modalRequest += 1; state.modalObjectUrl = ""; el.modal_image.removeAttribute("src"); el.modal_image.hidden = true; if (el.image_modal.open) el.image_modal.close(); }
  async function openPreview(asset) {
    const request = ++state.modalRequest; el.modal_image.removeAttribute("src"); el.modal_image.alt = asset.original_filename || "Image"; el.modal_image.hidden = true; el.modal_download.href = assetUrl(asset.id, true); el.modal_download.download = asset.original_filename || "private_send"; if (!el.image_modal.open) el.image_modal.showModal();
    try { const url = await getAssetObjectUrl(asset); if (request !== state.modalRequest || !el.image_modal.open) return; state.modalObjectUrl = url; el.modal_image.src = url; el.modal_image.hidden = false; } catch (_) { if (request === state.modalRequest) feedback("Preview could not be loaded.", true); }
  }
  function genericPreview(text = "Preview unavailable") { const generic = document.createElement("div"); generic.className = "asset-generic"; generic.textContent = text; return generic; }
  function setupAssetObserver() { if (!("IntersectionObserver" in window)) return; state.assetObserver = new IntersectionObserver((entries) => entries.forEach((entry) => { if (!entry.isIntersecting) return; const loader = state.assetLoaders.get(entry.target); state.assetObserver.unobserve(entry.target); state.assetLoaders.delete(entry.target); if (loader) loader(); }), { root: null, rootMargin: "500px 0px", threshold: 0 }); }
  function observeAssetPlaceholder(placeholder, loader) { if (!state.assetObserver) { loader(); return; } state.assetLoaders.set(placeholder, loader); state.assetObserver.observe(placeholder); }
  function fileIcon(asset) { const ext = extension(asset); return { pdf: "PDF", doc: "DOC", docx: "DOC", ppt: "PPT", pptx: "PPT", xls: "XLS", xlsx: "XLS", zip: "ZIP" }[ext] || "FILE"; }
  function downloadLink(asset) { const link = document.createElement("a"); link.className = "message-action asset-download"; link.href = assetUrl(asset.id, true); link.download = asset.original_filename || "private_send"; link.textContent = "Download"; return link; }
  function openPdfLink(asset) { const link = document.createElement("a"); link.className = "message-action asset-open"; link.href = `${assetUrl(asset.id)}?preview=1`; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Open"; return link; }
  function isPdfFile(asset) { return asset?.kind === "file" && asset.extension?.toLowerCase() === ".pdf" && asset.mime_type?.toLowerCase() === "application/pdf"; }
  function assetCard(asset, kind = "image") {
    const card = document.createElement("div"); card.className = `asset-card ${kind === "file" ? "file-card" : "image-card"}`;
    if (kind === "image") {
      if (previewable(asset)) {
        const placeholder = genericPreview("Loading image preview…"); placeholder.classList.add("asset-placeholder"); card.append(placeholder);
        observeAssetPlaceholder(placeholder, () => {
          if (!placeholder.isConnected || placeholder.dataset.loading) return;
          placeholder.dataset.loading = "1";
          getAssetObjectUrl(asset).then((url) => {
            if (!card.isConnected) return;
            const image = document.createElement("img"); image.alt = asset.original_filename || "Shared image"; image.loading = "lazy";
            image.onload = () => { if (card.closest(".message") === el.message_list.lastElementChild) scrollToBottom(); };
            image.onclick = () => openPreview(asset); image.onerror = () => { if (image.isConnected) image.replaceWith(genericPreview()); };
            placeholder.replaceWith(image); image.src = url;
          }).catch(() => { if (placeholder.isConnected) placeholder.replaceWith(genericPreview()); });
        });
      } else card.append(genericPreview("Image preview unavailable"));
    } else { const icon = document.createElement("span"); icon.className = "file-icon"; icon.textContent = fileIcon(asset); card.append(icon); }
    const title = document.createElement("strong"); title.textContent = asset.original_filename || (kind === "file" ? "File" : "Image");
    const details = document.createElement("span"); details.textContent = `${asset.mime_type || "application/octet-stream"} · ${formatBytes(asset.size)}`;
    const tools = document.createElement("div"); tools.className = "asset-actions"; if (kind === "image" && previewable(asset)) tools.append(action("View", () => openPreview(asset))); if (kind === "file" && isPdfFile(asset)) tools.append(openPdfLink(asset)); tools.append(downloadLink(asset)); card.append(title, details, tools); return card;
  }
  function addMessage(message, prepend = false) {
    if (!message || state.ids.has(message.id)) return null; state.ids.add(message.id); el.empty_state.hidden = true; const item = document.createElement("article"), own = message.sender === state.device; item.className = `message ${own ? "is-own" : "is-other"}`; const meta = document.createElement("div"); meta.className = "message-meta"; const date = new Date(message.created_at); meta.textContent = `${own ? "You" : message.sender === "pc" ? "PC" : "iPhone"} · ${Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`; item.append(meta); if (message.asset && (isImage(message) || isFile(message))) item.append(assetCard(message.asset, message.type)); else { const bubble = document.createElement("div"); bubble.className = "message-bubble"; bubble.textContent = message.content || ""; item.append(bubble); } item.append(action("Delete", () => deleteMessage(message))); if (prepend) el.message_list.prepend(item); else el.message_list.append(item); return item;
  }
  function scrollToBottom() { if (state.scrollFrame) cancelAnimationFrame(state.scrollFrame); state.scrollFrame = requestAnimationFrame(() => { state.scrollFrame = 0; const lastMessage = el.message_list.lastElementChild; if (lastMessage) lastMessage.scrollIntoView({ block: "end", behavior: "auto" }); }); }
  function renderInitial(messages) { [...messages].reverse().forEach((message) => addMessage(message)); if (messages.length) { state.syncCursor = messages[0].id; state.oldestId = messages.at(-1).id; } el.load_more.hidden = messages.length < 50; scrollToBottom(); }
  function appendRecent(messages) { return messages.reduce((count, message) => count + (addMessage(message) ? 1 : 0), 0); }
  function prependOlder(messages) { const added = messages.reduce((count, message) => count + (addMessage(message, true) ? 1 : 0), 0); if (messages.length) state.oldestId = messages.at(-1).id; return added; }
  async function initial() { const body = await (await api("/api/messages?limit=50")).json(); renderInitial(body.messages); }
  async function poll() { try { const path = state.syncCursor ? `/api/messages?after=${encodeURIComponent(state.syncCursor)}&limit=50` : "/api/messages?limit=50"; const body = await (await api(path)).json(); const added = state.syncCursor ? appendRecent(body.messages) : [...body.messages].reverse().reduce((count, message) => count + (addMessage(message) ? 1 : 0), 0); if (added) scrollToBottom(); if (body.messages.length) { if (!state.syncCursor) { state.oldestId = body.messages.at(-1).id; el.load_more.hidden = body.messages.length < 50; } state.syncCursor = state.syncCursor ? body.messages.at(-1).id : body.messages[0].id; } connection("connected", "Connected"); } catch (error) { connection(error.status === 401 ? "auth" : "offline", error.status === 401 ? "Needs pairing" : "Offline"); feedback(error.message, true); } finally { setTimeout(poll, POLL_MS); } }
  async function older() { if (!state.oldestId) return; const body = await (await api(`/api/messages?before=${encodeURIComponent(state.oldestId)}&limit=50`)).json(); if (state.scrollFrame) cancelAnimationFrame(state.scrollFrame); state.scrollFrame = 0; const previousHeight = el.message_list.scrollHeight, previousTop = el.message_list.scrollTop; const added = prependOlder(body.messages); if (added) el.message_list.scrollTop = previousTop + el.message_list.scrollHeight - previousHeight; el.load_more.hidden = body.messages.length < 50; }
  async function deleteMessage(message) { const suffix = message.type === "image" || message.type === "file" ? " Files saved in Windows Downloads are kept." : ""; if (!confirm(`Delete this message from history?${suffix}`)) return; try { await api(`/api/messages/${encodeURIComponent(message.id)}`, { method: "DELETE" }); location.reload(); } catch (error) { feedback(error.message, true); } }
  function historyTime(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
  function historyExtension(item) { return (item.filename.split(".").pop() || "").toLowerCase(); }
  function safeHistoryUrl(value) { return typeof value === "string" && value.startsWith("/api/assets/") && !value.startsWith("//"); }
  function historyGroup(label, items) {
    const section = document.createElement("section"); section.className = "history-group";
    const title = document.createElement("h3"); title.className = "history-group-title"; title.textContent = label; section.append(title);
    for (const item of items) {
      const row = document.createElement("article"); row.className = "history-item";
      const icon = document.createElement("span"); icon.className = `history-file-icon is-${item.kind}`;
      icon.textContent = item.kind === "image" ? "IMG" : (item.file_format || "FILE").slice(0, 5).toUpperCase(); icon.setAttribute("aria-hidden", "true");
      const details = document.createElement("div"); details.className = "history-item-details";
      const name = document.createElement("strong"); name.className = "history-item-name"; name.textContent = item.display_name || item.filename || "File";
      const meta = document.createElement("span"); meta.className = "history-item-meta"; const direction = item.sender === "iphone" ? "iPhone → PC" : "PC → iPhone"; meta.textContent = `${item.kind === "image" ? "Image" : (item.file_format || "Other").toUpperCase()} · ${formatBytes(item.size)} · ${direction} · ${historyTime(item.created_at)}`;
      const availability = document.createElement("span"); availability.className = `history-availability is-${item.availability.toLowerCase()}`;
      availability.textContent = ({ AVAILABLE: "Available", MISSING: "Missing from archive", PENDING: "Pending" }[item.availability] || "Unavailable");
      details.append(name, meta, availability); row.append(icon, details);
      const actions = document.createElement("div"); actions.className = "history-item-actions";
      if (item.availability === "AVAILABLE" && safeHistoryUrl(item.asset_url) && safeHistoryUrl(item.download_url)) {
        const open = document.createElement("a"); open.className = "history-action"; open.textContent = "Open"; open.href = item.kind === "file" && historyExtension(item) === "pdf" ? `${item.asset_url}?preview=1` : item.asset_url; open.target = "_blank"; open.rel = "noopener";
        const download = document.createElement("a"); download.className = "history-action"; download.textContent = "Download"; download.href = item.download_url; download.download = item.filename;
        actions.append(open, download);
      }
      if (actions.childElementCount) row.append(actions);
      section.append(row);
    }
    return section;
  }
  function renderHistory(items) {
    el.history_list.replaceChildren();
    el.history_list.setAttribute("aria-labelledby", state.historyMode === "date" ? "history-tab-date" : "history-tab-file");
    if (!items.length) { el.history_status.textContent = "No history items found."; return; }
    el.history_status.textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;
    if (state.historyMode === "date") {
      const groups = new Map();
      for (const item of items) { const label = item.archive_date || "Unknown date"; if (!groups.has(label)) groups.set(label, []); groups.get(label).push(item); }
      for (const [label, groupedItems] of groups) el.history_list.append(historyGroup(label, groupedItems));
      return;
    }
    const images = items.filter((item) => item.kind === "image"); if (images.length) el.history_list.append(historyGroup("Images", images));
    const formats = [...new Set(items.filter((item) => item.kind === "file").map((item) => item.file_format || "other"))].sort();
    for (const format of formats) { const groupedItems = items.filter((item) => item.kind === "file" && (item.file_format || "other") === format); el.history_list.append(historyGroup(format.toUpperCase(), groupedItems)); }
  }
  function resetHistory() { state.historyItems = []; state.historyCursor = ""; state.historyHasMore = false; }
  function syncHistoryFormat() { const files = el.history_type.value === "file"; el.history_format.hidden = !files; if (!files) el.history_format.value = "all"; }
  async function loadHistory(reset = true) {
    if (state.historyLoading) return; if (reset) resetHistory(); const request = ++state.historyRequest; const type = el.history_type.value; const format = el.history_format.value; const query = el.history_search.value.trim();
    const search = query ? `&q=${encodeURIComponent(query)}` : ""; const cursor = !reset && state.historyCursor ? `&before=${encodeURIComponent(state.historyCursor)}` : "";
    state.historyLoading = true; el.history_more.hidden = true; el.history_status.textContent = state.historyItems.length ? "Loading more…" : "Loading history…"; if (reset) el.history_list.replaceChildren();
    try {
      const response = await api(`/api/history?type=${encodeURIComponent(type)}&format=${encodeURIComponent(format)}&limit=100${search}${cursor}`); const body = await response.json();
      if (request !== state.historyRequest || el.history_drawer.hidden) return;
      state.historyItems.push(...(Array.isArray(body.items) ? body.items : [])); state.historyCursor = body.next_cursor || ""; state.historyHasMore = Boolean(body.has_more); renderHistory(state.historyItems); el.history_more.hidden = !state.historyHasMore;
    } catch (_) {
      if (request !== state.historyRequest || el.history_drawer.hidden) return;
      el.history_status.textContent = "History couldn't be loaded. Try again.";
      const retry = action("Retry", loadHistory); retry.classList.add("history-action"); el.history_list.replaceChildren(retry);
    } finally { state.historyLoading = false; }
  }
  function openHistory() {
    if (!el.history_drawer.hidden) { closeHistory(); return; }
    state.historyReturnFocus = document.activeElement; el.app_shell.inert = true; el.app_shell.setAttribute("aria-hidden", "true");
    el.history_overlay.hidden = false; el.history_drawer.hidden = false;
    requestAnimationFrame(() => { el.history_overlay.classList.add("is-visible"); el.history_drawer.classList.add("is-open"); });
    document.body.classList.add("history-open"); el.history_toggle.setAttribute("aria-expanded", "true");
    el.history_search.focus(); loadHistory();
  }
  function closeHistory() {
    if (el.history_drawer.hidden) return;
    state.historyRequest += 1; el.history_drawer.classList.remove("is-open"); el.history_overlay.classList.remove("is-visible");
    el.history_drawer.hidden = true; el.history_overlay.hidden = true; el.app_shell.inert = false; el.app_shell.removeAttribute("aria-hidden");
    document.body.classList.remove("history-open"); el.history_toggle.setAttribute("aria-expanded", "false");
    const focusTarget = state.historyReturnFocus && state.historyReturnFocus.isConnected ? state.historyReturnFocus : el.history_toggle;
    focusTarget.focus();
  }
  function trapHistoryFocus(event) {
    if (el.history_drawer.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); closeHistory(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...el.history_drawer.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href]')];
    if (!focusable.length) { event.preventDefault(); el.history_drawer.focus(); return; }
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function showBatchToast(message, isError, duration) {
    clearTimeout(state.toastTimer); el.batch_toast.textContent = message;
    el.batch_toast.classList.toggle("is-error", isError); el.batch_toast.hidden = false;
    state.toastTimer = setTimeout(() => { el.batch_toast.hidden = true; }, duration);
  }
  function nextClientEntryId() { state.clientEntrySeq += 1; return `${Date.now()}-${state.clientEntrySeq}`; }
  function updateComposerControls() { const uploading = state.uploadRunning; el.file_pick_button.disabled = uploading; el.file_input.disabled = uploading; el.message_input.disabled = uploading || state.textSending; el.send_button.disabled = uploading || state.textSending || (!el.message_input.value.trim() && !state.composerEntries.some((entry) => entry.status === "READY")); }
  function renderComposerBatch() { el.composer_batch.replaceChildren(); el.composer_batch.hidden = !state.composerEntries.length; for (const entry of state.composerEntries) { const row = document.createElement("div"); row.className = `upload-row is-${entry.status.toLowerCase()}`; const label = document.createElement("span"); label.textContent = `${entry.name} · ${formatBytes(entry.size)} · ${entry.status}${entry.status === "UPLOADING" ? ` ${entry.progress}%` : ""}${entry.error ? ` · ${entry.error}` : ""}`; row.append(label); if (entry.status === "FAILED") { const retry = action("Retry", () => { entry.status = "READY"; entry.error = ""; renderComposerBatch(); processCurrentBatch(); }); retry.disabled = state.uploadRunning; row.append(retry); } el.composer_batch.append(row); } updateComposerControls(); }
  function currentBatchIsTerminal() { return state.composerEntries.length > 0 && state.composerEntries.every((entry) => ["COMPLETED", "FAILED"].includes(entry.status)); }
  function stageFiles(files) { if (state.uploadRunning) { feedback("Wait for the current upload to finish before selecting files.", true); return 0; } const selected = [...files]; if (!selected.length) return 0; if (currentBatchIsTerminal()) state.composerEntries = []; const staged = selected.map((file) => ({ id: nextClientEntryId(), file, name: file.name || "private_send", size: file.size, status: "READY", progress: 0, error: "", archiveDate: "" })); state.composerEntries.push(...staged); renderComposerBatch(); feedback(`${staged.length} file${staged.length === 1 ? "" : "s"} ready to send.`); return staged.length; }
  function handleFileSelection(input) { try { stageFiles(input.files); } catch (_) { feedback("Could not add the selected files.", true); } finally { input.value = ""; } }
  function uploadEntry(entry) { return new Promise((resolve, reject) => { const data = new FormData(); data.append("file", entry.file, entry.name); data.append("sender", state.device); const request = new XMLHttpRequest(); request.open("POST", "/api/assets"); request.withCredentials = true; if (state.token) request.setRequestHeader("Authorization", `Bearer ${state.token}`); request.upload.onprogress = (event) => { if (event.lengthComputable) { entry.progress = Math.round(event.loaded / event.total * 100); renderComposerBatch(); } }; request.onload = () => { if (request.status >= 200 && request.status < 300) { try { resolve(JSON.parse(request.responseText || "{}")); } catch (_) { resolve({}); } return; } reject(new Error(`Upload failed (${request.status}).`)); }; request.onerror = () => reject(new Error("Upload interrupted.")); request.ontimeout = () => reject(new Error("Upload timed out.")); request.send(data); }); }
  async function processEntry(entry) { entry.status = "UPLOADING"; entry.progress = 0; renderComposerBatch(); let body; try { body = await uploadEntry(entry); } catch (_) { entry.status = "FAILED"; entry.error = "Upload failed. Retry this file."; renderComposerBatch(); return; } entry.status = "COMPLETED"; entry.progress = 100; entry.file = null; entry.archiveDate = body.archive?.date || body.asset?.relative_path?.split("/", 1)[0] || ""; renderComposerBatch(); if (body.message) { addMessage({ ...body.message, asset: body.asset }); scrollToBottom(); } }
  async function processCurrentBatch() { if (state.uploadRunning) return; const batch = state.composerEntries.filter((entry) => entry.status === "READY"); if (!batch.length) return; state.uploadRunning = true; renderComposerBatch(); try { for (const entry of batch) await processEntry(entry); } finally { state.uploadRunning = false; renderComposerBatch(); } const saved = batch.filter((entry) => entry.status === "COMPLETED"); const failedCount = batch.length - saved.length; const dates = [...new Set(saved.map((entry) => entry.archiveDate).filter(Boolean))]; const location = dates.length === 1 ? `Windows Downloads\\file_private_send\\${dates[0]}` : "Windows Downloads\\file_private_send"; const message = failedCount ? `${saved.length} saved · ${failedCount} failed\nSaved files are in ${location}\nRetry the failed item below.` : `${saved.length} item${saved.length === 1 ? "" : "s"} saved to\n${dates.length > 1 ? "2 date folders under\n" : ""}${location}`; showBatchToast(message, Boolean(failedCount), failedCount ? 8000 : 4000); }
  async function sendTextMessage(content) { try { const body = await (await api("/api/messages", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sender: state.device, type: "text", content }) })).json(); addMessage(body.message); el.message_input.value = ""; scrollToBottom(); feedback(); return true; } catch (error) { feedback(error.message, true); return false; } }
  async function sendComposer(event) { event.preventDefault(); if (state.uploadRunning || state.textSending) return; const content = el.message_input.value; const hasText = Boolean(content.trim()); if (!hasText && !state.composerEntries.some((entry) => entry.status === "READY")) return; if (hasText) { state.textSending = true; updateComposerControls(); const sent = await sendTextMessage(content); state.textSending = false; updateComposerControls(); if (!sent) return; } if (state.composerEntries.some((entry) => entry.status === "READY")) await processCurrentBatch(); }
  async function pairingQr() { try { const url = makeObjectUrl(await (await api("/api/pairing/qr")).blob()); releaseObjectUrl(state.pairingObjectUrl); state.pairingObjectUrl = url; el.pairing_qr.src = url; } catch (_) { el.qr_frame.hidden = true; } }
  function init() {
    loadVersion(); setupAssetObserver(); state.device = ["localhost", "127.0.0.1"].includes(location.hostname) ? "pc" : "iphone"; el.device_select.value = state.device;
    const updateDevice = () => { state.device = el.device_select.value; el.identity_copy.textContent = `Messages from this browser are labeled ${state.device === "pc" ? "PC" : "iPhone"}.`; };
    updateDevice(); el.device_select.onchange = updateDevice; el.composer.onsubmit = sendComposer; el.message_input.oninput = updateComposerControls; el.message_input.onkeydown = (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); el.composer.requestSubmit(); } };
    el.file_pick_button.onclick = () => el.file_input.click(); el.file_input.onchange = () => handleFileSelection(el.file_input); updateComposerControls();
    el.load_more.onclick = () => older().catch((error) => feedback(error.message, true));
    el.modal_close.onclick = closePreview; el.image_modal.addEventListener("close", closePreview); el.image_modal.onclick = (event) => { if (event.target === el.image_modal) closePreview(); };
    el.history_toggle.onclick = openHistory; el.history_close.onclick = closeHistory; el.history_overlay.onclick = closeHistory;
    el.history_type.onchange = () => { syncHistoryFormat(); loadHistory(); }; el.history_format.onchange = () => loadHistory(); el.history_more.onclick = () => loadHistory(false); el.history_search.oninput = () => { clearTimeout(state.historySearchTimer); state.historySearchTimer = setTimeout(loadHistory, 250); };
    for (const tab of [el.history_tab_date, el.history_tab_file]) tab.onclick = () => {
      state.historyMode = tab.dataset.historyMode;
      for (const candidate of [el.history_tab_date, el.history_tab_file]) {
        const active = candidate === tab; candidate.classList.toggle("is-active", active); candidate.setAttribute("aria-selected", String(active));
      }
      loadHistory();
    };
    window.addEventListener("keydown", trapHistoryFocus);
    window.addEventListener("beforeunload", () => { clearTimeout(state.toastTimer); clearTimeout(state.historySearchTimer); if (state.assetObserver) state.assetObserver.disconnect(); releaseAllObjectUrls(); });
    Promise.all([initial(), pairingQr()]).then(() => { connection("connected", "Connected"); poll(); }).catch((error) => { connection(error.status === 401 ? "auth" : "offline", error.status === 401 ? "Needs pairing" : "Offline"); feedback(error.message, true); });
  }
  init();
})();
