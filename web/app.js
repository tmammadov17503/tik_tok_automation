const statusContainer = document.getElementById("tool-status");
const form = document.getElementById("run-form");
const logsElement = document.getElementById("logs");
const artifactsElement = document.getElementById("artifacts");
const analysisElement = document.getElementById("analysis-summary");
const jobMeta = document.getElementById("job-meta");
const runButton = document.getElementById("run-button");
const tiktokSummary = document.getElementById("tiktok-summary");
const tiktokForm = document.getElementById("tiktok-form");
const tiktokConnectButton = document.getElementById("tiktok-connect");
const tiktokQrConnectButton = document.getElementById("tiktok-qr-connect");
const tiktokDisconnectButton = document.getElementById("tiktok-disconnect");
const tiktokQrPanel = document.getElementById("tiktok-qr-panel");
const runtimeSummary = document.getElementById("runtime-summary");
const runtimeForm = document.getElementById("runtime-form");
const telegramSummary = document.getElementById("telegram-summary");
const telegramForm = document.getElementById("telegram-form");
const sourceForm = document.getElementById("source-form");
const sourceList = document.getElementById("source-list");
const automationSummary = document.getElementById("automation-summary");
const automationForm = document.getElementById("automation-form");
const automationQueue = document.getElementById("automation-queue");
const automationRunButton = document.getElementById("automation-run");

let activeJobId = null;
let pollHandle = null;
let automationPollHandle = null;
let tiktokQrPollHandle = null;
let currentTikTokQrDataUrl = "";

function createStatusPill(name, enabled) {
  const item = document.createElement("div");
  item.className = `tool-pill ${enabled ? "ok" : "missing"}`;
  item.innerHTML = `<span>${name}</span><strong>${enabled ? "ready" : "missing"}</strong>`;
  return item;
}

async function loadStatus() {
  const response = await fetch("/api/status");
  const data = await response.json();
  statusContainer.innerHTML = "";
  Object.entries(data.tools).forEach(([name, enabled]) => {
    statusContainer.appendChild(createStatusPill(name, enabled));
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderTikTokStatus(data) {
  const profile = data.profile || {};
  const lines = [];

  if (data.connected) {
    const label = profile.display_name ? escapeHtml(profile.display_name) : "Connected account";
    lines.push(`<p><strong>${label}</strong> is connected.</p>`);
  } else if (data.configured) {
    lines.push("<p>TikTok settings are saved, but the account is not connected yet.</p>");
  } else {
    lines.push("<p>TikTok settings are not saved yet.</p>");
  }

  lines.push(`<p class="mini-line">Client key: ${escapeHtml(data.client_key_preview || "not saved")}</p>`);
  lines.push(`<p class="mini-line">Redirect URI: ${escapeHtml(data.redirect_uri || "not set")}</p>`);
  lines.push(`<p class="mini-line">Scopes: ${escapeHtml(data.scopes || "not set")}</p>`);
  if (Array.isArray(data.default_hashtags) && data.default_hashtags.length) {
    lines.push(`<p class="mini-line">Default hashtags: ${escapeHtml(data.default_hashtags.join(" "))}</p>`);
  }

  if (data.access_expires_at) {
    lines.push(`<p class="mini-line">Access token expires: ${escapeHtml(data.access_expires_at)}</p>`);
  }
  if (data.refresh_expires_at) {
    lines.push(`<p class="mini-line">Refresh token expires: ${escapeHtml(data.refresh_expires_at)}</p>`);
  }

  tiktokSummary.innerHTML = lines.join("");
  tiktokDisconnectButton.disabled = !data.connected && !data.can_refresh;

  if (data.redirect_uri) {
    tiktokForm.elements.redirect_uri.value = data.redirect_uri;
  }
  if (data.scopes) {
    tiktokForm.elements.scopes.value = data.scopes;
  }
}

function renderRuntimeStatus(data) {
  const lines = [];
  if (data.openai_configured) {
    lines.push("<p><strong>OpenAI transcription is ready.</strong></p>");
  } else {
    lines.push("<p><strong>OpenAI transcription is not configured.</strong></p>");
  }
  lines.push(`<p class="mini-line">API key: ${escapeHtml(data.openai_api_key_preview || "not saved")}</p>`);
  lines.push(`<p class="mini-line">Model: ${escapeHtml(data.openai_transcribe_model || "whisper-1")}</p>`);
  if (data.openai_key_source) {
    lines.push(`<p class="mini-line">Source: ${escapeHtml(data.openai_key_source)}</p>`);
  }
  runtimeSummary.innerHTML = lines.join("");
  runtimeForm.elements.openai_transcribe_model.value = data.openai_transcribe_model || "whisper-1";
}

function renderTelegramStatus(data) {
  const lines = [];
  if (data.enabled && data.connected) {
    lines.push("<p><strong>Telegram bot is enabled and connected.</strong></p>");
  } else if (data.enabled && data.configured) {
    lines.push("<p><strong>Telegram bot is enabled. Send /start to bind the chat.</strong></p>");
  } else if (data.configured) {
    lines.push("<p><strong>Telegram bot token is saved, but the bot is paused.</strong></p>");
  } else {
    lines.push("<p><strong>Telegram bot is not configured.</strong></p>");
  }
  lines.push(`<p class="mini-line">Bot token: ${escapeHtml(data.bot_token_preview || "not saved")}</p>`);
  lines.push(`<p class="mini-line">Chat ID: ${escapeHtml(data.chat_id || "not bound")}</p>`);
  if (data.last_error) {
    lines.push(`<p class="mini-line">Last issue: ${escapeHtml(data.last_error)}</p>`);
  }
  if (data.last_update_at) {
    lines.push(`<p class="mini-line">Last update: ${escapeHtml(data.last_update_at)}</p>`);
  }
  telegramSummary.innerHTML = lines.join("");
  telegramForm.elements.enabled.checked = Boolean(data.enabled);
  if (data.chat_id) {
    telegramForm.elements.chat_id.value = data.chat_id;
  }
}

function truncateMiddle(value, maxLength = 88) {
  const text = String(value || "");
  if (text.length <= maxLength) {
    return text;
  }
  const head = Math.ceil((maxLength - 3) / 2);
  const tail = Math.floor((maxLength - 3) / 2);
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

function sourceStatusLabel(status) {
  if (status === "done") {
    return "Complete";
  }
  if (status === "active") {
    return "In progress";
  }
  if (status === "parked") {
    return "Parked";
  }
  return "Queued";
}

function sourceCountLine(entry) {
  return `${entry.posted_clips} posted, ${entry.remaining_clips} left`;
}

function automationItemStatusLabel(status) {
  const labels = {
    pending: "Pending upload",
    uploading: "Uploading",
    processing: "Processing",
    sent_to_inbox: "Sent to TikTok inbox",
    posted: "Posted",
    failed: "Failed",
  };
  return labels[status] || status || "Unknown";
}

function applySourceToRun(entry) {
  form.elements.source_mode.value = "remote_url";
  form.elements.source_value.value = entry.source_url;
  form.elements.clip_duration_sec.value = entry.content_mode === "monetization" ? "72" : "30";
  form.elements.clips_count.value = entry.content_mode === "monetization" ? "1" : "2";
  form.elements.language.value = entry.audience_language || "ru";
  if (!form.elements.topic.value.trim() && entry.title) {
    form.elements.topic.value = entry.title;
  }
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderSources(sources) {
  sourceList.innerHTML = "";

  if (!Array.isArray(sources) || !sources.length) {
    sourceList.innerHTML = "<p>No queued sources yet.</p>";
    return;
  }

  sources.forEach((entry) => {
    const card = document.createElement("article");
    card.className = "source-card";

    const top = document.createElement("div");
    top.className = "source-top";

    const heading = document.createElement("div");
    heading.className = "source-heading";

    const title = document.createElement("h3");
    title.textContent = entry.title || "Saved source";

    const meta = document.createElement("p");
    meta.className = "source-meta";
    meta.textContent = (entry.mode_label || "Growth") + " mode - " + (entry.account_profile_label || "Film Box Official RU") + " - " + (entry.audience_language || "ru").toUpperCase() + " - planned " + entry.planned_clips + " clip" + (entry.planned_clips === 1 ? "" : "s");

    heading.appendChild(title);
    heading.appendChild(meta);

    const badge = document.createElement("span");
    badge.className = `source-badge ${entry.status || "queued"}`;
    badge.textContent = sourceStatusLabel(entry.status);

    top.appendChild(heading);
    top.appendChild(badge);

    const counts = document.createElement("p");
    counts.className = "source-counts";
    counts.textContent = sourceCountLine(entry);

    const url = document.createElement("p");
    url.className = "source-url";
    url.innerHTML = `<a href="${escapeHtml(entry.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(truncateMiddle(entry.source_url))}</a>`;

    const updated = document.createElement("p");
    updated.className = "source-updated";
    updated.textContent = `Updated ${entry.updated_at || entry.added_at || "just now"}`;

    const actions = document.createElement("div");
    actions.className = "source-actions";

    const useButton = document.createElement("button");
    useButton.type = "button";
    useButton.className = "ghost-button";
    useButton.textContent = "Use in workflow";
    useButton.addEventListener("click", () => applySourceToRun(entry));

    const markButton = document.createElement("button");
    markButton.type = "button";
    markButton.textContent = "+1 posted";
    markButton.disabled = entry.remaining_clips <= 0;
    markButton.addEventListener("click", () => {
      incrementSourcePosted(entry.id).catch((error) => {
        sourceList.innerHTML = `<p>Source update failed: ${escapeHtml(error.message)}</p>`;
      });
    });

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "ghost-button";
    removeButton.textContent = "Remove";
    removeButton.addEventListener("click", () => {
      removeSource(entry.id).catch((error) => {
        sourceList.innerHTML = `<p>Source removal failed: ${escapeHtml(error.message)}</p>`;
      });
    });

    actions.appendChild(useButton);
    actions.appendChild(markButton);
    actions.appendChild(removeButton);

    card.appendChild(top);
    card.appendChild(counts);
    card.appendChild(url);
    card.appendChild(updated);
    card.appendChild(actions);
    sourceList.appendChild(card);
  });
}

async function loadTikTokStatus() {
  const response = await fetch("/api/tiktok/status");
  const data = await response.json();
  renderTikTokStatus(data);
}

async function loadRuntimeStatus() {
  const response = await fetch("/api/config/status");
  const data = await response.json();
  renderRuntimeStatus(data);
}

async function loadTelegramStatus() {
  const response = await fetch("/api/telegram/status");
  const data = await response.json();
  renderTelegramStatus(data);
}

async function loadSources() {
  const response = await fetch("/api/sources");
  const data = await response.json();
  renderSources(data.sources || []);
}

function renderAutomationQueue(items) {
  automationQueue.innerHTML = "";
  if (!Array.isArray(items) || !items.length) {
    automationQueue.innerHTML = "<p>No queued TikTok clips yet.</p>";
    return;
  }

  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "source-card";

    const top = document.createElement("div");
    top.className = "source-top";

    const heading = document.createElement("div");
    heading.className = "source-heading";

    const title = document.createElement("h3");
    title.textContent = item.source_title || item.clip_label || "Queued clip";

    const meta = document.createElement("p");
    meta.className = "source-meta";
    meta.textContent = item.clip_label || "Generated clip";

    heading.appendChild(title);
    heading.appendChild(meta);

    const badge = document.createElement("span");
    badge.className = `source-badge ${item.status || "queued"}`;
    badge.textContent = automationItemStatusLabel(item.status);

    top.appendChild(heading);
    top.appendChild(badge);

    const queueLine = document.createElement("p");
    queueLine.className = "source-counts";
    queueLine.textContent = (item.hashtags || []).join(" ");

    const sourceUrl = document.createElement("p");
    sourceUrl.className = "source-url";
    sourceUrl.innerHTML = `<a href="${escapeHtml(item.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(truncateMiddle(item.source_url || ""))}</a>`;

    const updated = document.createElement("p");
    updated.className = "source-updated";
    if (item.error) {
      updated.textContent = `Issue: ${item.error}`;
    } else if (item.tiktok_status) {
      updated.textContent = `TikTok status: ${item.tiktok_status}`;
    } else {
      updated.textContent = `Updated ${item.updated_at || item.created_at || "just now"}`;
    }

    card.appendChild(top);
    card.appendChild(queueLine);
    card.appendChild(sourceUrl);
    card.appendChild(updated);
    automationQueue.appendChild(card);
  });
}

function renderAutomationStatus(data) {
  const lines = [];
  lines.push(`<p><strong>${data.enabled ? "Automation is enabled." : "Automation is paused."}</strong></p>`);
  lines.push(`<p class="mini-line">Runs every ${escapeHtml(String(data.interval_hours || 12))} hour(s).</p>`);
  if (data.performance_summary) {
    lines.push(`<p class="mini-line">${escapeHtml(String(data.performance_summary))}</p>`);
  }
  lines.push(`<p class="mini-line">Running now: ${data.running ? "yes" : "no"}.</p>`);
  if (data.draft_only) {
    lines.push("<p class=\"mini-line\">Current TikTok mode: upload to draft/inbox. Fully public hands-off posting needs video.publish approval later.</p>");
  }
  if (data.next_run_at) {
    lines.push(`<p class="mini-line">Next run: ${escapeHtml(data.next_run_at)}</p>`);
  }
  if (data.last_run_at) {
    lines.push(`<p class="mini-line">Last run: ${escapeHtml(data.last_run_at)}</p>`);
  }
  if (data.last_error) {
    lines.push(`<p class="mini-line">Last issue: ${escapeHtml(data.last_error)}</p>`);
  }
  if (data.queue_counts) {
    lines.push(
      `<p class="mini-line">Queue: ${escapeHtml(String(data.queue_counts.pending || 0))} pending, ${escapeHtml(String(data.queue_counts.making || 0))} making/queued, ${escapeHtml(String(data.queue_counts.inbox || 0))} in inbox, ${escapeHtml(String(data.queue_counts.posted || 0))} posted, ${escapeHtml(String(data.queue_counts.failed || 0))} failed.</p>`
    );
  }
  if (data.tiktok_pending_cap) {
    const remotePending = Number(data.tiktok_remote_pending || 0);
    const cap = Number(data.tiktok_pending_cap || 0);
    lines.push(
      `<p class="mini-line">TikTok inbox usage: ${escapeHtml(String(remotePending))}/${escapeHtml(String(cap))} pending API share(s).</p>`
    );
  }
  if (Array.isArray(data.logs) && data.logs.length) {
    lines.push(`<p class="mini-line">Latest: ${escapeHtml(data.logs[data.logs.length - 1])}</p>`);
  }

  automationSummary.innerHTML = lines.join("");
  automationForm.elements.interval_hours.value = String(data.interval_hours || 12);
  automationForm.elements.enabled.checked = Boolean(data.enabled);
  renderAutomationQueue(data.queue_items || []);
}

async function loadAutomationStatus() {
  const response = await fetch("/api/automation/status");
  const data = await response.json();
  renderAutomationStatus(data);
}

async function saveTikTokSettings(event) {
  event.preventDefault();
  const saveButton = document.getElementById("tiktok-save");
  saveButton.disabled = true;
  saveButton.textContent = "Saving...";

  const payload = {
    client_key: tiktokForm.elements.client_key.value,
    client_secret: tiktokForm.elements.client_secret.value,
    redirect_uri: tiktokForm.elements.redirect_uri.value,
    scopes: tiktokForm.elements.scopes.value,
  };

  const response = await fetch("/api/tiktok/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();

  saveButton.disabled = false;
  saveButton.textContent = "Save TikTok settings";

  if (!response.ok) {
    tiktokSummary.innerHTML = `<p>Save failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  tiktokForm.elements.client_key.value = "";
  tiktokForm.elements.client_secret.value = "";
  renderTikTokStatus(data);
}

async function saveRuntimeSettings(event) {
  event.preventDefault();
  const saveButton = document.getElementById("runtime-save");
  saveButton.disabled = true;
  saveButton.textContent = "Saving...";

  const payload = {
    openai_api_key: runtimeForm.elements.openai_api_key.value,
    openai_transcribe_model: runtimeForm.elements.openai_transcribe_model.value,
  };

  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();

  saveButton.disabled = false;
  saveButton.textContent = "Save caption settings";

  if (!response.ok) {
    runtimeSummary.innerHTML = `<p>Caption settings save failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  runtimeForm.elements.openai_api_key.value = "";
  renderRuntimeStatus(data);
  loadStatus().catch(() => {});
}

async function saveTelegramSettings(event) {
  event.preventDefault();
  const saveButton = document.getElementById("telegram-save");
  saveButton.disabled = true;
  saveButton.textContent = "Saving...";

  const payload = {
    bot_token: telegramForm.elements.bot_token.value,
    chat_id: telegramForm.elements.chat_id.value,
    enabled: telegramForm.elements.enabled.checked,
  };

  const response = await fetch("/api/telegram/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();

  saveButton.disabled = false;
  saveButton.textContent = "Save Telegram settings";

  if (!response.ok) {
    telegramSummary.innerHTML = `<p>Telegram save failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  telegramForm.elements.bot_token.value = "";
  renderTelegramStatus(data);
}

async function saveSource(event) {
  event.preventDefault();
  const saveButton = document.getElementById("source-save");
  saveButton.disabled = true;
  saveButton.textContent = "Saving...";

  const payload = {
    source_url: sourceForm.elements.source_url.value,
    planned_clips: Number(sourceForm.elements.planned_clips.value || 8),
    content_mode: sourceForm.elements.content_mode.value || "growth",
    account_profile: sourceForm.elements.account_profile.value || "main_ru",
    audience_language: sourceForm.elements.audience_language.value || "ru",
    title: sourceForm.elements.title.value,
  };

  const response = await fetch("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();

  saveButton.disabled = false;
  saveButton.textContent = "Add source";

  if (!response.ok) {
    sourceList.innerHTML = `<p>Source save failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  sourceForm.reset();
  sourceForm.elements.planned_clips.value = "8";
  sourceForm.elements.content_mode.value = "growth";
  sourceForm.elements.account_profile.value = "main_ru";
  sourceForm.elements.audience_language.value = "ru";
  renderSources(data.sources || []);
}

async function saveAutomationSettings(event) {
  event.preventDefault();
  const saveButton = document.getElementById("automation-save");
  saveButton.disabled = true;
  saveButton.textContent = "Saving...";

  const payload = {
    interval_hours: Number(automationForm.elements.interval_hours.value || 12),
    enabled: automationForm.elements.enabled.checked,
  };
  const response = await fetch("/api/automation/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();

  saveButton.disabled = false;
  saveButton.textContent = "Save automation";

  if (!response.ok) {
    automationSummary.innerHTML = `<p>Automation save failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  renderAutomationStatus(data);
}

async function runAutomationNow() {
  automationRunButton.disabled = true;
  automationRunButton.textContent = "Running...";

  const response = await fetch("/api/automation/run-now", { method: "POST" });
  const data = await response.json();

  automationRunButton.disabled = false;
  automationRunButton.textContent = "Run now";

  if (!response.ok) {
    automationSummary.innerHTML = `<p>Automation run failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  renderAutomationStatus(data);
  loadAutomationStatus().catch((error) => {
    automationSummary.innerHTML = `<p>Automation refresh failed: ${escapeHtml(error.message)}</p>`;
  });
}

async function incrementSourcePosted(sourceId, count = 1) {
  const response = await fetch(`/api/sources/${sourceId}/increment`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ count }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Source update failed.");
  }
  renderSources(data.sources || []);
}

async function removeSource(sourceId) {
  const response = await fetch(`/api/sources/${sourceId}/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Source removal failed.");
  }
  renderSources(data.sources || []);
}

async function connectTikTok() {
  tiktokConnectButton.disabled = true;
  tiktokConnectButton.textContent = "Preparing...";
  const response = await fetch("/api/tiktok/connect", { method: "POST" });
  const data = await response.json();

  tiktokConnectButton.disabled = false;
  tiktokConnectButton.textContent = "Connect TikTok";

  if (!response.ok) {
    tiktokSummary.innerHTML = `<p>Connect failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  window.location.href = data.authorize_url;
}

function renderTikTokQrPanel(data) {
  tiktokQrPanel.hidden = false;
  if (data.qr_data_url) {
    currentTikTokQrDataUrl = data.qr_data_url;
  }
  if (data.status === "expired" || data.status === "connected") {
    currentTikTokQrDataUrl = "";
  }
  const message = escapeHtml(data.message || "Scan the QR code with TikTok.");
  const status = escapeHtml(data.status || "waiting");
  const qrImage = currentTikTokQrDataUrl
    ? `<img class="qr-image" src="${escapeHtml(currentTikTokQrDataUrl)}" alt="TikTok QR authorization code">`
    : "";
  tiktokQrPanel.innerHTML = `
    <div class="qr-content">
      ${qrImage}
      <div>
        <h3>TikTok QR connect</h3>
        <p>${message}</p>
        <p class="mini-line">Status: ${status}</p>
      </div>
    </div>
  `;
}

function stopTikTokQrPolling() {
  if (tiktokQrPollHandle) {
    window.clearInterval(tiktokQrPollHandle);
    tiktokQrPollHandle = null;
  }
}

async function pollTikTokQr() {
  const response = await fetch("/api/tiktok/qr/status");
  const data = await response.json();
  if (!response.ok) {
    stopTikTokQrPolling();
    tiktokQrPanel.hidden = false;
    tiktokQrPanel.innerHTML = `<p>QR connect failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  renderTikTokQrPanel(data);
  if (["connected", "expired", "utilised", "not_started"].includes(data.status)) {
    stopTikTokQrPolling();
  }
  if (data.status === "connected") {
    await loadTikTokStatus();
  }
}

async function connectTikTokQr() {
  stopTikTokQrPolling();
  tiktokQrConnectButton.disabled = true;
  tiktokQrConnectButton.textContent = "Preparing QR...";

  const response = await fetch("/api/tiktok/qr/start", { method: "POST" });
  const data = await response.json();

  tiktokQrConnectButton.disabled = false;
  tiktokQrConnectButton.textContent = "Connect by QR";

  if (!response.ok) {
    tiktokQrPanel.hidden = false;
    tiktokQrPanel.innerHTML = `<p>QR connect failed: ${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  renderTikTokQrPanel(data);
  tiktokQrPollHandle = window.setInterval(() => {
    pollTikTokQr().catch((error) => {
      stopTikTokQrPolling();
      tiktokQrPanel.innerHTML = `<p>QR status failed: ${escapeHtml(error.message)}</p>`;
    });
  }, 9000);
}

async function disconnectTikTok() {
  stopTikTokQrPolling();
  tiktokDisconnectButton.disabled = true;
  const response = await fetch("/api/tiktok/disconnect", { method: "POST" });
  const data = await response.json();
  tiktokDisconnectButton.disabled = false;
  tiktokQrPanel.hidden = true;
  renderTikTokStatus(data);
}

function formToJson(formElement) {
  const data = new FormData(formElement);
  return {
    project_name: data.get("project_name"),
    topic: data.get("topic"),
    source_mode: data.get("source_mode"),
    source_value: data.get("source_value"),
    segments: data.get("segments"),
    clip_duration_sec: Number(data.get("clip_duration_sec") || 30),
    clips_count: Number(data.get("clips_count") || 2),
    frame_rate: data.get("frame_rate") || "source",
    language: data.get("language"),
    whisper_model: data.get("whisper_model"),
    add_captions: data.get("add_captions") === "on",
    publish_mode: data.get("publish_mode"),
    rights_confirmed: data.get("rights_confirmed") === "on",
  };
}

async function startRun(event) {
  event.preventDefault();
  runButton.disabled = true;
  runButton.textContent = "Starting...";
  logsElement.textContent = "Submitting workflow...";
  analysisElement.innerHTML = "<p>Waiting for analysis...</p>";
  artifactsElement.innerHTML = "<li>Waiting for outputs...</li>";

  const payload = formToJson(form);
  const response = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  activeJobId = data.job_id;
  jobMeta.textContent = `Running job ${activeJobId}`;
  runButton.textContent = "Workflow running";
  pollJob();
}

async function refreshJob() {
  if (!activeJobId) {
    return;
  }

  const response = await fetch(`/api/jobs/${activeJobId}`);
  const data = await response.json();

  jobMeta.textContent = `Job ${data.job_id} | ${data.status} | updated ${data.updated_at}`;
  logsElement.textContent = data.logs.length ? data.logs.join("\n") : "No logs yet.";

  artifactsElement.innerHTML = "";
  if (data.artifacts.length) {
    data.artifacts.forEach((artifact) => {
      const item = document.createElement("li");
      item.innerHTML = `<a href="${artifact.url}" target="_blank" rel="noreferrer">${artifact.label}</a>`;
      artifactsElement.appendChild(item);
    });
  } else {
    artifactsElement.innerHTML = "<li>No outputs yet.</li>";
  }

  await renderAnalysis(data.artifacts);

  if (data.status === "completed" || data.status === "failed") {
    runButton.disabled = false;
    runButton.textContent = "Start workflow";
    if (pollHandle) {
      window.clearInterval(pollHandle);
      pollHandle = null;
    }
  }
}

function createMomentCard(moment, index) {
  const card = document.createElement("article");
  card.className = "analysis-card";

  const title = document.createElement("h4");
  title.textContent = `Highlight ${index + 1}`;

  const time = document.createElement("p");
  time.className = "analysis-time";
  time.textContent = `${moment.start} to ${moment.end}`;

  const score = document.createElement("p");
  score.className = "analysis-score";
  score.textContent = moment.score ? `Score ${moment.score}` : "Manual segment";

  const reason = document.createElement("p");
  reason.textContent = moment.reason || "No reason recorded.";

  card.appendChild(title);
  card.appendChild(time);
  card.appendChild(score);
  card.appendChild(reason);

  if (moment.excerpt) {
    const excerpt = document.createElement("blockquote");
    excerpt.textContent = moment.excerpt;
    card.appendChild(excerpt);
  }

  return card;
}

async function renderAnalysis(artifacts) {
  const analysisArtifact = artifacts.find((artifact) => artifact.path === "analysis.json");
  if (!analysisArtifact) {
    analysisElement.innerHTML = "<p>No analysis yet.</p>";
    return;
  }

  try {
    const response = await fetch(analysisArtifact.url);
    const data = await response.json();
    analysisElement.innerHTML = "";

    const method = document.createElement("p");
    method.className = "analysis-method";
    method.textContent = `Method: ${data.method || "unknown"}`;
    analysisElement.appendChild(method);

    if (data.subtitle_source) {
      const subtitle = document.createElement("p");
      subtitle.className = "analysis-subtitle";
      subtitle.textContent = `Subtitle source: ${data.subtitle_source}`;
      analysisElement.appendChild(subtitle);
    }

    const selected = Array.isArray(data.selected_segments) ? data.selected_segments : [];
    if (!selected.length) {
      const empty = document.createElement("p");
      empty.textContent = "No ranked highlights yet.";
      analysisElement.appendChild(empty);
      return;
    }

    selected.forEach((moment, index) => {
      analysisElement.appendChild(createMomentCard(moment, index));
    });
  } catch (error) {
    analysisElement.innerHTML = `<p>Analysis load failed: ${error.message}</p>`;
  }
}

function pollJob() {
  if (pollHandle) {
    window.clearInterval(pollHandle);
  }

  refreshJob();
  pollHandle = window.setInterval(refreshJob, 2000);
}

form.addEventListener("submit", startRun);
tiktokForm.addEventListener("submit", saveTikTokSettings);
tiktokConnectButton.addEventListener("click", connectTikTok);
tiktokQrConnectButton.addEventListener("click", connectTikTokQr);
tiktokDisconnectButton.addEventListener("click", disconnectTikTok);
runtimeForm.addEventListener("submit", saveRuntimeSettings);
telegramForm.addEventListener("submit", saveTelegramSettings);
sourceForm.addEventListener("submit", saveSource);
sourceForm.elements.content_mode.addEventListener("change", () => {
  sourceForm.elements.planned_clips.value =
    sourceForm.elements.content_mode.value === "monetization" ? "4" : "8";
});
sourceForm.elements.account_profile.addEventListener("change", () => {
  sourceForm.elements.audience_language.value =
    sourceForm.elements.account_profile.value === "future_en" ? "en" : "ru";
});
automationForm.addEventListener("submit", saveAutomationSettings);
automationRunButton.addEventListener("click", runAutomationNow);
loadStatus().catch((error) => {
  statusContainer.textContent = `Status check failed: ${error.message}`;
});
loadTikTokStatus().catch((error) => {
  tiktokSummary.innerHTML = `<p>TikTok status check failed: ${escapeHtml(error.message)}</p>`;
});
loadRuntimeStatus().catch((error) => {
  runtimeSummary.innerHTML = `<p>Caption status check failed: ${escapeHtml(error.message)}</p>`;
});
loadTelegramStatus().catch((error) => {
  telegramSummary.innerHTML = `<p>Telegram status check failed: ${escapeHtml(error.message)}</p>`;
});
loadSources().catch((error) => {
  sourceList.innerHTML = `<p>Source queue load failed: ${escapeHtml(error.message)}</p>`;
});
loadAutomationStatus().catch((error) => {
  automationSummary.innerHTML = `<p>Automation status check failed: ${escapeHtml(error.message)}</p>`;
});
automationPollHandle = window.setInterval(() => {
  loadAutomationStatus().catch(() => {});
  loadTelegramStatus().catch(() => {});
}, 15000);
