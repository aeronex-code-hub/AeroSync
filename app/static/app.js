const app = document.getElementById("app");
let me = null;
let settingsCache = null;
let activePage = "Dashboard";
let licenseState = null;
let streamPageStart = 0;
let dwellTimer = null;
let previewRefreshTimer = null;
let liveSettingsRefreshTimer = null;
let mapRefreshTimer = null;
let mapInstance = null;
let mapMarkers = {};
let mapTileLayer = null;
let selectedMapDeviceSn = null;
let mqttRefreshTimer = null;
let ridRefreshTimer = null;
let ridRawRefreshTimer = null;
let ridRawAutoScroll = true;
let ridSettingsData = null;
let mqttRawLiveTimer = null;
let mqttRawLivePaused = false;
let mqttRawLiveInitialized = false;
let mqttRawLiveSeen = new Set();
let mqttRawLiveLines = [];

let mediaRefreshTimer = null;
let nvrRefreshTimer = null;
let standaloneMode = false;
let reportsCache = null;
let selectedEventId = null;
let selectedMqttId = null;
let mqttMessagesCache = [];
let selectedDfrProvider = null;
let selectedDfrTab = "monitor";
let selectedDfrEventId = null;
let dfrRefreshTimer = null;
let dashboardNvrServers = [];
let openApiTab = "overview";
let openApiPageCache = {connectionId:"", overview:null, projects:null, devicesByProject:{}, loadedAt:""};
let openApiPageLoading = null;

const moduleNames = ["Dashboard", "Events", "OpenAPI", "Live Streams", "NVR Sync", "DFR", "MQTT", "RID", "Live Map", "Media / S3", "Settings", "Email", "Reports", "Logs", "Backup", "Users", "License", "Help"];
const advancedModules = new Set(["NVR Sync", "DFR", "OpenAPI", "Media / S3", "Reports", "Backup"]);
const moduleDisplayNames = {
  "Dashboard": "DASH BOARD",
  "Events": "EVENT API",
  "Live Streams": "LIVE STREAM",
  "NVR Sync": "NVR SYNC",
  "DFR": "DFR",
  "OpenAPI": "OPEN API",
  "MQTT": "MQTT",
  "RID": "RID",
  "Live Map": "LIVE MAP",
  "Media / S3": "MEDIA STORAGE",
  "Settings": "SETTINGS",
  "Email": "EMAIL",
  "Reports": "REPORTS",
  "Logs": "LOGS",
  "Backup": "BACKUP",
  "Users": "USERS",
  "License": "LICENSE",
  "Help": "HELP"
};
const modulePermissionMap = {
  "Dashboard": "dashboard",
  "Events": "events",
  "MQTT": "mqtt",
  "RID": "rid",
  "Media / S3": "media_s3",
  "Live Streams": "live_streams",
  "Live Map": "live_map",
  "NVR Sync": "nvr_sync",
  "DFR": "dfr_view",
  "OpenAPI": "openapi",
  "Logs": "logs",
  "Backup": "backup",
  "Reports": "reports",
  "Email": "email",
  "Settings": "settings",
  "Users": "users",
  "License": "license",
  "Help": null
};

const permissionLabels = [
  ["dashboard", "Dashboard"],
  ["events", "Events"],
  ["mqtt", "MQTT"],
  ["rid", "RID"],
  ["media_s3", "Media / S3"],
  ["live_streams", "Live Streams"],
  ["live_map", "Live Map"],
  ["nvr_sync", "NVR Sync"],
  ["dfr_view", "DFR View"],
  ["dfr_settings", "DFR Settings"],
  ["openapi", "OpenAPI"],
  ["logs", "Logs"],
  ["backup", "Backup"],
  ["reports", "Reports"],
  ["email", "Email"],
  ["settings", "Settings"],
  ["users", "Users"],
  ["license", "License"]
];

function hasPermission(permission) {
  if (!permission) return true;
  return Boolean(me?.permissions?.includes(permission));
}

function currentEdition() {
  return String(licenseState?.edition || "Advanced").toLowerCase() === "advanced" ? "Advanced" : "Basic";
}

function moduleAllowedByEdition(name) {
  return currentEdition() === "Advanced" || !advancedModules.has(name);
}

function allowedModuleNames() {
  return moduleNames.filter(name => hasPermission(modulePermissionMap[name]) && moduleAllowedByEdition(name));
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[c]));
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(String(value ?? ""));
  return String(value ?? "").replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function reportFrontendDiagnostic(payload) {
  try {
    const body = JSON.stringify({
      page: window.location.pathname + window.location.search,
      userAgent: navigator.userAgent,
      ...payload
    });
    if (navigator.sendBeacon) {
      const blob = new Blob([body], {type: "application/json"});
      navigator.sendBeacon("/api/diagnostics/frontend", blob);
      return;
    }
    fetch("/api/diagnostics/frontend", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body,
      keepalive: true
    }).catch(() => {});
  } catch (_) {}
}

window.addEventListener("error", event => {
  reportFrontendDiagnostic({
    kind: "js_error",
    message: event.message,
    source: event.filename,
    line: event.lineno,
    col: event.colno
  });
});

window.addEventListener("unhandledrejection", event => {
  const reason = event.reason || {};
  reportFrontendDiagnostic({
    kind: "unhandled_rejection",
    message: reason.message || String(reason),
    source: reason.stack ? String(reason.stack).slice(0, 500) : ""
  });
});

async function api(path, options = {}) {
  const started = performance.now();
  let res;
  try {
    res = await fetch(path, {
      headers: {"Content-Type": "application/json"},
      ...options
    });
  } catch (err) {
    reportFrontendDiagnostic({kind: "api_fetch_failed", path, message: err.message});
    throw err;
  }
  const elapsed = performance.now() - started;
  const data = await res.json().catch(() => ({}));
  if (elapsed >= 1500 || !res.ok || res.status === 401) {
    reportFrontendDiagnostic({
      kind: res.status === 401 ? "api_unauthorized" : (!res.ok ? "api_error" : "api_slow"),
      path,
      status: res.status,
      ms: Math.round(elapsed),
      message: data.error || ""
    });
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function boot() {
  const data = await api("/api/me");
  licenseState = data.license || null;
  if (!data.authenticated) {
    renderLogin(data.footer, licenseState);
    return;
  }
  me = data.user;
  me.license = licenseState;
  applyUrlState();
  if (licenseState?.status === "expired") activePage = "License";
  renderShell();
  await loadSettings();
  renderPage(activePage);
}

function applyUrlState() {
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("module");
  if (requested && moduleNames.includes(requested)) activePage = requested;
  if (activePage === "DFR") {
    selectedDfrProvider = normalizeDfrProviderName(params.get("dfrProvider"));
    selectedDfrTab = params.get("dfrTab") === "settings" ? "settings" : "monitor";
  }
  standaloneMode = params.get("standalone") === "1";
}

function moduleUrl(name, standalone = false) {
  const url = new URL(window.location.href);
  url.searchParams.set("module", name);
  url.searchParams.delete("dfrProvider");
  url.searchParams.delete("dfrTab");
  if (standalone) url.searchParams.set("standalone", "1");
  else url.searchParams.delete("standalone");
  return url.toString();
}

function dfrProviderUrl(provider, standalone = false, tab = "monitor") {
  const url = new URL(window.location.href);
  url.searchParams.set("module", "DFR");
  url.searchParams.set("dfrProvider", normalizeDfrProviderName(provider));
  url.searchParams.set("dfrTab", tab === "settings" ? "settings" : "monitor");
  if (standalone) url.searchParams.set("standalone", "1");
  else url.searchParams.delete("standalone");
  return url.toString();
}

function openModuleSeparate(name) {
  window.open(moduleUrl(name, true), "_blank");
}

function openDfrProviderSeparate(provider, event) {
  if (event) event.stopPropagation();
  const name = normalizeDfrProviderName(provider);
  if (!name) return showToast(`${provider} DFR integration is not connected yet`, "error");
  window.open(dfrProviderUrl(name, true), "_blank");
}

function nvrWebUrl(nvr) {
  const host = String(nvr?.host || "").trim();
  if (!host) return "";
  const port = Number(nvr?.web_port || 80);
  const protocol = port === 443 ? "https" : "http";
  const suffix = port && ![80, 443].includes(port) ? `:${port}` : "";
  return `${protocol}://${host}${suffix}`;
}

function dashboardNvrs(statusServers = []) {
  const source = (statusServers && statusServers.length)
    ? statusServers
    : (settingsCache?.settings?.modules?.nvr_sync?.nvrs || []);
  return source
    .filter(nvr => nvr && nvr.enabled !== false && String(nvr.host || "").trim())
    .map((nvr, index) => ({
      name: nvr.name || `NVR ${index + 1}`,
      host: nvr.host || "",
      web_port: Number(nvr.web_port || 80),
      enabled: nvr.enabled !== false
    }));
}

function openNvrWeb(index, event) {
  if (event) event.stopPropagation();
  const nvr = (dashboardNvrServers || [])[Number(index)];
  const url = nvrWebUrl(nvr);
  if (!url) return showToast("NVR web address is not configured", "error");
  window.open(url, "_blank");
}

function cleanSdkText(value) {
  return String(value || "").replace(/Hikvision\s+/gi, "");
}

function goModule(name) {
  activePage = name;
  if (name === 'DFR') {
    selectedDfrProvider = null;
    selectedDfrTab = "monitor";
    selectedDfrEventId = null;
  }
  if (!standaloneMode) {
    window.history.replaceState({}, "", moduleUrl(name, false));
  }
  renderPage(activePage);
}

function isActiveContent(content, name) {
  return activePage === name && content === document.getElementById("content");
}

function isEditingStreamSettings() {
  const settingsPanel = document.getElementById("streamSettings");
  return Boolean(settingsPanel?.classList.contains("show") || settingsPanel?.contains(document.activeElement));
}

function syncLiveStreamSettingsDom(liveCfg) {
  const settingsPanel = document.getElementById("streamSettings");
  if (!settingsPanel || !liveCfg) return;
  const active = document.activeElement;
  const savePath = document.getElementById("streamSavePath");
  if (savePath && active !== savePath) savePath.value = liveCfg.save_path || "";
  (liveCfg.channels || []).forEach(ch => {
    const channel = ch.channel;
    const enabled = settingsPanel.querySelector(`[data-stream-enabled="${channel}"]`);
    const name = settingsPanel.querySelector(`[data-stream-name="${channel}"]`);
    const url = settingsPanel.querySelector(`[data-stream-url="${channel}"]`);
    if (enabled && active !== enabled) enabled.checked = Boolean(ch.enabled);
    if (name && active !== name) name.value = ch.name || "";
    if (url && active !== url) url.value = ch.rtsp_url || "";
    const row = url?.closest(".channel-row");
    const meta = row?.querySelector(".channel-meta");
    if (meta) meta.textContent = `SN: ${ch.device_sn || "--"} | Updated: ${formatDubaiTime(ch.updated_at)}`;
  });
}

function recordActivity(action, module) {
  api("/api/activity", {
    method: "POST",
    body: JSON.stringify({action, module, standalone: standaloneMode})
  }).catch(() => {});
}

function licenseIsBlocking(license) {
  return ["missing", "invalid", "invalid_machine", "hardware_id_unavailable"].includes(license?.status);
}

function renderLogin(footer, license) {
  const blocked = licenseIsBlocking(license);
  const expired = license?.status === "expired";
  app.innerHTML = `
    <div class="login-shell">
      <div class="login-visual"></div>
      <div class="login-panel">
        <form class="login-card" id="loginForm">
          <img class="login-logo" src="/static/assets/aerosync-logo-wordmark.png" alt="AERO SYNC">
          ${blocked ? `
            <div class="license-block">
              <strong>${esc(license?.message || "License not found")}</strong>
              <p class="muted">Send this machine code to AERO NEX to receive the license file.</p>
              <div class="machine-code">${esc(license?.machine_code || "--")}</div>
              <div class="button-row">
                <button class="ghost small-btn" type="button" onclick="copyText('${escAttr(license?.machine_code || "")}')">Copy Code</button>
                <label class="secondary small-btn file-btn">Import License<input type="file" accept=".lic,.json,application/json" onchange="importLicenseFile(this)" hidden></label>
              </div>
              <label class="field">
                <span>Paste License Code</span>
                <textarea id="licensePasteCode" rows="6" placeholder="ASLIC-START ... ASLIC-END"></textarea>
              </label>
              <button class="secondary small-btn" type="button" onclick="activateLicenseCode()">Activate License</button>
            </div>
          ` : ""}
          ${expired ? `<div class="license-block warn"><strong>License expired</strong><p class="muted">Admin login is allowed so the license can be renewed.</p></div>` : ""}
          <label class="field">
            <span>Username</span>
            <input id="username" autocomplete="username" value="admin" ${blocked ? "disabled" : ""}>
          </label>
          <label class="field">
            <span>Password</span>
            <input id="password" type="password" autocomplete="current-password" value="admin123" ${blocked ? "disabled" : ""}>
          </label>
          <button class="primary" type="submit" style="width:100%" ${blocked ? "disabled" : ""}>Login</button>
          <div class="error" id="loginError"></div>
          <button class="ghost" type="button" style="width:100%;margin-top:8px" onclick="forgotPassword()">Forgot password?</button>
          <p class="muted" id="lockWarning" style="font-size:12px;display:none">Wrong password locks the account after 5 failed attempts. Admin reset is required after lock.</p>
          <p class="muted" style="font-size:12px">${esc(footer)}</p>
        </form>
      </div>
    </div>
  `;
  document.getElementById("loginForm").addEventListener("submit", async ev => {
    ev.preventDefault();
    const error = document.getElementById("loginError");
    error.textContent = "";
    try {
      await api("/api/login", {
        method: "POST",
        body: JSON.stringify({
          username: document.getElementById("username").value,
          password: document.getElementById("password").value
        })
      });
      await boot();
    } catch (err) {
      error.textContent = err.message;
      document.getElementById("lockWarning").style.display = "block";
    }
  });
}

async function importLicenseFile(input) {
  const file = input.files?.[0];
  if (!file) return;
  const text = await file.text();
  await importLicenseText(text, input);
}

async function importLicenseText(text, input = null) {
  try {
    const result = await api("/api/license/import", {
      method: "POST",
      body: JSON.stringify({content: text})
    });
    showToast(result.message || "License imported");
    licenseState = result.license;
    await boot();
  } catch (err) {
    alert(`License import failed: ${err.message}`);
  } finally {
    if (input) input.value = "";
  }
}

async function activateLicenseCode() {
  const box = document.getElementById("licensePasteCode");
  const code = box?.value || "";
  if (!code.trim()) return showToast("Paste the license code first", "error");
  await importLicenseText(code);
}

function renderShell() {
  app.innerHTML = `
    <div class="app-shell ${standaloneMode ? "standalone" : ""}">
      <aside class="sidebar">
        <div class="logo"><img class="sidebar-logo" src="/static/assets/aerosync-logo-wordmark.png" alt="AERO SYNC"></div>
        <nav class="nav">
          ${allowedModuleNames().map(name => `<button data-page="${esc(name)}">${esc(moduleDisplayNames[name] || name)}</button>`).join("")}
        </nav>
        <div class="footer">2025 Aero Nex FZCO<br>Contact us : <a href="mailto:Support@aeronex.ae">Support@aeronex.ae</a></div>
      </aside>
      <main class="main">
        <header class="topbar">
          <div></div>
          <div class="window-actions">
            <span class="user-chip">${esc(currentEdition())}</span>
            <span class="user-chip">${esc(me.username)}</span>
            ${standaloneMode ? `<button class="icon-btn" title="Main Dashboard" onclick="window.location.href='${escAttr(moduleUrl(activePage, false))}'">&#8962;</button>` : `<button class="icon-btn" title="Open module separately" onclick="openModuleSeparate(activePage)">&#8599;</button>`}
            <button class="circle-btn" title="Info" onclick="showInfo()">i</button>
            <button class="circle-btn" title="Compact mode" onclick="toggleCompact()">-</button>
            <button class="circle-btn" title="Logout" onclick="logout()">X</button>
          </div>
        </header>
        <section class="content" id="content"></section>
      </main>
    </div>
  `;
  document.querySelectorAll(".nav button").forEach(btn => {
    btn.addEventListener("click", () => {
      goModule(btn.dataset.page);
    });
  });
}

function updateNav() {
  document.querySelectorAll(".nav button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.page === activePage);
  });
}

async function loadSettings() {
  const data = await api("/api/settings");
  settingsCache = data;
}

async function renderPage(name) {
  activePage = name;
  if (dwellTimer) {
    clearInterval(dwellTimer);
    dwellTimer = null;
  }
  if (previewRefreshTimer) {
    clearInterval(previewRefreshTimer);
    previewRefreshTimer = null;
  }
  if (liveSettingsRefreshTimer) {
    clearInterval(liveSettingsRefreshTimer);
    liveSettingsRefreshTimer = null;
  }
  if (mapRefreshTimer) {
    clearInterval(mapRefreshTimer);
    mapRefreshTimer = null;
  }
  if (mqttRefreshTimer) {
    clearInterval(mqttRefreshTimer);
    mqttRefreshTimer = null;
  }
  if (ridRefreshTimer) {
    clearInterval(ridRefreshTimer);
    ridRefreshTimer = null;
  }
  if (mediaRefreshTimer) {
    clearInterval(mediaRefreshTimer);
    mediaRefreshTimer = null;
  }
  if (nvrRefreshTimer) {
    clearInterval(nvrRefreshTimer);
    nvrRefreshTimer = null;
  }
  if (dfrRefreshTimer) {
    clearInterval(dfrRefreshTimer);
    dfrRefreshTimer = null;
  }
  mapInstance = null;
  mapMarkers = {};
  mapTileLayer = null;
  updateNav();
  recordActivity("module_open", name);
  const content = document.getElementById("content");
  content.innerHTML = `
    <div class="page-title"><h1>${esc(name)}</h1></div>
    <div class="card loading-card"><strong>Loading ${esc(name)}...</strong></div>
  `;
  if (!settingsCache) await loadSettings();
  if (licenseState?.status === "expired" && name !== "License") {
    activePage = "License";
    return renderLicense(content);
  }
  if (!moduleAllowedByEdition(name)) {
    content.innerHTML = `<div class="module-header"><div><h1>Advanced License Required</h1></div></div><div class="card"><h3>${esc(name)}</h3><p class="muted">This module is available only with the AERO SYNC Advanced edition.</p><button class="secondary" onclick="goModule('License')">View License</button></div>`;
    return;
  }
  if (!hasPermission(modulePermissionMap[name])) {
    content.innerHTML = `<div class="module-header"><div><h1>Access Restricted</h1></div></div><div class="card"><h3>${esc(name)}</h3><p class="muted">Your role does not have permission to open this module.</p></div>`;
    return;
  }
  if (name === "Dashboard") return renderDashboard(content);
  if (name === "Events") return renderEvents(content);
  if (name === "MQTT") return renderMqtt(content);
  if (name === "RID") return renderRid(content);
  if (name === "Media / S3") return renderMedia(content);
  if (name === "Live Map") return renderLiveMap(content);
  if (name === "NVR Sync") return renderNvrSync(content);
  if (name === "DFR") return renderDfr(content);
  if (name === "OpenAPI") return renderOpenApi(content);
  if (name === "Logs") return renderLogs(content);
  if (name === "Backup") return renderBackup(content);
  if (name === "Users") return renderUsers(content);
  if (name === "Reports") return renderReports(content);
  if (name === "Email") return renderEmail(content);
  if (name === "Settings") return renderSettings(content);
  if (name === "License") return renderLicense(content);
  if (name === "Live Streams") return renderLiveStreams(content);
  if (name === "Help") return renderHelp(content);
  return renderPlaceholder(content, name);
}

async function renderDashboard(content) {
  const status = await api("/api/status");
  if (!isActiveContent(content, "Dashboard")) return;
  const eventCard = status.cards.find(c => c.name === "Events") || {};
  const mqttCard = status.cards.find(c => c.name === "MQTT") || {};
  const mediaCard = status.cards.find(c => c.name === "Media / S3") || {};
  const streamCard = status.cards.find(c => c.name === "Live Streams") || {};
  const resources = status.resources || {};
  const mem = resources.memory || {};
  const cpu = resources.cpu || {};
  const net = resources.network || {};
  const dashboardCards = status.cards.filter(m => !["Logs", "Settings", "OpenAPI"].includes(m.name) && moduleAllowedByEdition(m.name));
  const openApiCard = status.cards.find(c => c.name === "OpenAPI") || {};
  const openApiConnections = settingsCache?.settings?.modules?.openapi?.connections || [];
  const openApiConnectionCount = openApiConnections.filter(c => c && c.enabled !== false).length;
  const openApiConnected = String(openApiCard.value || "").toLowerCase() === "ready" || openApiConnectionCount > 0;
  const advancedDashboard = currentEdition() === "Advanced";
  const mediaStatus = (status.recent_media || []).slice(0, 5).map(f => ({
    level: "info",
    title: f.name || "Media File",
    message: `${formatBytes(f.size || 0)}${f.modified ? ` | ${f.modified}` : ""}`,
    source: f.path || ""
  }));
  dashboardNvrServers = dashboardNvrs(status.nvr_servers || []);
  const dfrCard = status.cards.find(c => c.name === "DFR") || {};
  const fh2Mode = String(settingsCache?.settings?.flight_hub?.mode || "cloud").toLowerCase() === "onprem" ? "onprem" : "cloud";
  const canChangeFh2Mode = hasPermission("settings");
  content.innerHTML = `
    <div class="dash-hero">
      <div>
        <h1>Operation Center</h1>
      </div>
      <div class="hero-status">
        <span class="pill"><span class="dot"></span> Secure HTTPS</span>
        <span class="pill">UTC+4 Display</span>
        <label class="pill" style="display:flex;align-items:center;gap:7px;padding:4px 8px" title="Select the active FlightHub platform">
          <span>FH2:</span>
          <select id="dashboardFh2Mode"
                  aria-label="FlightHub Mode"
                  ${canChangeFh2Mode ? `onchange="saveDashboardFh2Mode()"` : "disabled"}
                  style="width:118px;height:26px;min-height:26px;padding:2px 24px 2px 8px;border-radius:7px;font-size:12px;margin:0">
            <option value="cloud" ${fh2Mode === "cloud" ? "selected" : ""}>Cloud</option>
            <option value="onprem" ${fh2Mode === "onprem" ? "selected" : ""}>On-Prem</option>
          </select>
        </label>
      </div>
    </div>
    <div class="kpi-grid">
      ${dashboardCards.map(m => `
        <div class="module-button" onclick="goModule('${escAttr(m.name)}')">
          <div class="module-card-head">
            <strong>${esc(m.name)}</strong>
            <span class="module-head-actions">
              <button class="icon-btn" title="Open ${escAttr(m.name)} separately" onclick="event.stopPropagation(); openModuleSeparate('${escAttr(m.name)}')">&#8599;</button>
              <span class="led ${moduleLedClass(m)}"></span>
            </span>
          </div>
          <span class="kpi-value">${esc(m.value)}</span>
          <span class="muted">${esc(m.status)}</span>
          <div class="module-card-foot">
            <span class="pill">Port ${esc(m.port)}</span>
          </div>
        </div>
      `).join("")}
      ${advancedDashboard && hasPermission("openapi") ? `
        <div class="module-button" onclick="goModule('OpenAPI')">
          <div class="module-card-head">
            <strong>OpenAPI</strong>
            <span class="module-head-actions">
              <button class="icon-btn" title="Open OpenAPI separately" onclick="event.stopPropagation(); openModuleSeparate('OpenAPI')">&#8599;</button>
              <span class="led ${openApiConnected ? "ok" : "warn"}"></span>
            </span>
          </div>
          <span class="kpi-value" style="font-size:20px">API Integration</span>
          <span class="muted">Status: ${openApiConnected ? "Connected" : "Not configured"}</span>
          <div class="module-card-foot">
            <span class="pill">Connections: ${esc(openApiConnectionCount)}</span>
            <span class="pill">Open &#9654;</span>
          </div>
        </div>
      ` : ""}
    </div>
    <div class="dashboard-panels">
      <div class="card dashboard-panel-card">
        <div class="panel-title"><h3>EventAPI Message Status</h3><span class="pill">${esc(eventCard.value ?? 0)} received</span></div>
        ${statusMessageList(status.event_status_messages, "No EventAPI messages received yet.")}
      </div>
      ${advancedDashboard ? `<div class="card dashboard-panel-card">
        <div class="panel-title"><h3>DFR Message Status</h3><span class="pill">${esc(dfrCard.value ?? 0)} today</span></div>
        ${statusMessageList(status.dfr_status_messages, "No DFR events received yet.")}
      </div>` : ""}
      <div class="card dashboard-panel-card">
        <div class="panel-title"><h3>MQTT Message Status</h3><span class="pill">${esc(mqttCard.value ?? 0)} captured</span></div>
        ${statusMessageList(status.mqtt_status_messages, "No readable MQTT status yet.")}
      </div>
      ${advancedDashboard ? `<div class="card dashboard-panel-card">
        <div class="panel-title"><h3>Recent Media</h3><span class="pill">${esc(mediaCard.value ?? 0)} files</span></div>
        ${statusMessageList(mediaStatus, "No media files received yet.")}
      </div>` : ""}
      <div class="card dashboard-resource-card">
        <div class="panel-title"><h3>Server Resources</h3><span class="pill">Live</span></div>
        <div class="resource-grid">
          <div><span>CPU</span><strong>${cpu.percent == null ? "Warming" : `${esc(cpu.percent)}%`}</strong><small>${esc(cpu.cores || "--")} cores</small></div>
          <div><span>RAM</span><strong>${mem.percent == null ? "N/A" : `${esc(mem.percent)}%`}</strong><small>${formatBytes(mem.used || 0)} / ${formatBytes(mem.total || 0)}</small></div>
        </div>
        <div class="mini-list resource-list">
          <div><strong>Network:</strong> Down ${formatBandwidth(net.download_bps)} | Up ${formatBandwidth(net.upload_bps)} | Clients ${esc(net.clients ?? 0)}</div>
          ${(resources.disks || []).slice(0, 4).map(d => `<div><strong>${esc(d.label)}</strong> ${esc(d.percent ?? "--")}% used | Free ${formatBytes(d.free || 0)}</div>`).join("") || `<div>No drive data available</div>`}
          ${(resources.gpu || []).slice(0, 2).map(g => `<div><strong>GPU</strong> ${esc(g.name || "Not available")}</div>`).join("")}
        </div>
      </div>
      ${advancedDashboard && dashboardNvrServers.length ? `
        <div class="card dashboard-panel-card dashboard-nvr-card">
          <div class="panel-title"><h3>NVR</h3><span class="pill">${esc(dashboardNvrServers.length)} configured</span></div>
          <div class="nvr-dashboard-list">
            ${dashboardNvrServers.map((nvr, index) => `
              <button class="nvr-dashboard-item" onclick="openNvrWeb(${index}, event)">
                <span>
                  <strong>${esc(nvr.name || `NVR ${index + 1}`)}</strong>
                  <small>${esc(nvr.host || "--")}</small>
                </span>
                <span class="module-head-actions">
                  <span class="icon-btn" title="Open ${escAttr(nvr.name || `NVR ${index + 1}`)} web interface">&#8599;</span>
                </span>
              </button>
            `).join("")}
          </div>
        </div>
      ` : ""}
    </div>
  `;
}

async function saveDashboardFh2Mode() {
  const select = document.getElementById("dashboardFh2Mode");
  const mode = select?.value === "onprem" ? "onprem" : "cloud";
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({settings: {flight_hub: {mode}}})
    });
    settingsCache = {
      ...(settingsCache || {}),
      ...data,
      settings: data.settings || settingsCache?.settings || {}
    };
    showToast(`FlightHub mode changed to ${mode === "onprem" ? "FH2 On-Prem" : "FH2 Cloud"}`, "success");
    if (activePage === "Dashboard") await renderDashboard(document.getElementById("content"));
  } catch (error) {
    showToast(error.message || "Unable to save FlightHub mode", "error");
  }
}

function moduleLedClass(card) {
  const status = String(card?.status || "").toLowerCase();
  const value = Number(card?.value || 0);
  if (status.includes("path not set") || status.includes("empty") || status.includes("offline")) return "led-yellow";
  if (status.includes("error") || status.includes("fail")) return "led-red";
  if (status.includes("ready") || status.includes("configured") || status.includes("enabled") || value > 0) return "led-green";
  return "led-yellow";
}

function smallList(items, formatter) {
  if (!items || !items.length) return `<p class="muted">No data available. Configure paths in Settings.</p>`;
  return `<div class="mini-list">${items.slice(0, 6).map(item => `<div>${esc(formatter(item))}</div>`).join("")}</div>`;
}

function statusMessageList(items, emptyText) {
  if (!items || !items.length) return `<p class="muted">${esc(emptyText || "No status available.")}</p>`;
  return `<div class="mqtt-status-list dashboard-status-list">${items.slice(0, 5).map(item => `
    <div class="mqtt-status-item ${escAttr(item.level || "info")}">
      <strong>${esc(item.title || "Status")}</strong>
      <span>${esc(item.message || "")}</span>
      <small>${item.time ? esc(formatDubaiTime(item.time)) : ""}${item.source ? `${item.time ? " | " : ""}${esc(item.source)}` : ""}</small>
    </div>
  `).join("")}</div>`;
}

function normalizeDfrProviderName(provider) {
  const value = String(provider || "").toLowerCase();
  if (value.includes("hik")) return "Hikvision";
  if (value.includes("scylla")) return "Scylla";
  return "";
}

function dfrProviderConfigKey(provider) {
  return normalizeDfrProviderName(provider).toLowerCase();
}

function dfrProviderEndpoint(provider) {
  return dfrProviderConfigKey(provider) === "hikvision" ? "/dfr/hikvision" : "/dfr/scylla";
}

function openDfrProvider(provider) {
  const name = normalizeDfrProviderName(provider);
  if (!name) {
    showToast(`${provider} DFR integration is not connected yet`, "error");
    return;
  }
  selectedDfrProvider = name;
  selectedDfrTab = "monitor";
  selectedDfrEventId = null;
  if (!standaloneMode) window.history.replaceState({}, "", dfrProviderUrl(name, false));
  renderDfr(document.getElementById("content"));
}

function backToDfrProviders() {
  selectedDfrProvider = null;
  selectedDfrTab = "monitor";
  selectedDfrEventId = null;
  if (!standaloneMode) window.history.replaceState({}, "", moduleUrl("DFR", false));
  renderDfr(document.getElementById("content"));
}

function openDfrMonitor() {
  selectedDfrTab = "monitor";
  if (!standaloneMode && selectedDfrProvider) window.history.replaceState({}, "", dfrProviderUrl(selectedDfrProvider, false, "monitor"));
  renderDfr(document.getElementById("content"));
}

function openDfrSettings() {
  if (!hasPermission("dfr_settings")) return showToast("DFR Settings permission required", "error");
  selectedDfrTab = "settings";
  if (!standaloneMode && selectedDfrProvider) window.history.replaceState({}, "", dfrProviderUrl(selectedDfrProvider, false, "settings"));
  renderDfr(document.getElementById("content"));
}

function selectDfrEvent(id) {
  selectedDfrEventId = String(id || "");
  renderDfr(document.getElementById("content"));
}

function normalizedHikDocks(providerCfg) {
  const docks = Array.isArray(providerCfg?.docks) ? providerCfg.docks : [];
  return docks.length ? docks.slice(0, 5) : [{name: "", project_uuid: ""}];
}

function normalizedHikCameras(providerCfg, docks) {
  const cameras = Array.isArray(providerCfg?.cameras) ? providerCfg.cameras : [];
  const firstDock = (docks.find(d => d.name)?.name || "");
  return cameras.length ? cameras.slice(0, 128) : [{alarm_source_name: "", dock_name: firstDock}];
}

function dfrDockOptions(docks, selected) {
  const names = docks.map(d => String(d.name || "").trim()).filter(Boolean);
  return [`<option value="">Select Dock / Project</option>`].concat(names.map(name => `<option value="${escAttr(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`)).join("");
}

function renderDfrHikDockRows(docks) {
  return docks.map((dock, index) => `
    <div class="dfr-map-row dfr-hik-dock-row">
      <label class="field"><span>Dock Name</span><input class="dfr-hik-dock-name" value="${escAttr(dock.name || "")}" placeholder="Dock 1" oninput="refreshDfrCameraDockOptions()"></label>
      <label class="field"><span>DJI Project UUID</span><input class="dfr-hik-dock-project" value="${escAttr(dock.project_uuid || dock.uuid || "")}" placeholder="FH2 project UUID"></label>
      <button class="ghost small-btn dfr-remove-btn" onclick="removeDfrDockRow(this)" ${docks.length <= 1 ? "disabled" : ""}>Remove</button>
    </div>`).join("");
}

function renderDfrHikCameraRows(cameras, docks) {
  return cameras.map((cam, index) => `
    <div class="dfr-map-row dfr-hik-camera-row">
      <label class="field"><span>Alarm Source Name</span><input class="dfr-hik-camera-source" value="${escAttr(cam.alarm_source_name || cam.source || "")}" placeholder="Hikvision alarm source name"></label>
      <label class="field"><span>Dock / Project</span><select class="dfr-hik-camera-dock">${dfrDockOptions(docks, cam.dock_name || cam.dock || "")}</select></label>
      <button class="ghost small-btn dfr-remove-btn" onclick="removeDfrCameraRow(this)" ${cameras.length <= 1 ? "disabled" : ""}>Remove</button>
    </div>`).join("");
}

function readDfrHikDocksFromDom(includeEmpty = false) {
  const rows = Array.from(document.querySelectorAll(".dfr-hik-dock-row")).slice(0, 5).map(row => ({
    name: row.querySelector(".dfr-hik-dock-name")?.value.trim() || "",
    project_uuid: row.querySelector(".dfr-hik-dock-project")?.value.trim() || ""
  }));
  return includeEmpty ? rows : rows.filter(row => row.name || row.project_uuid);
}

function readDfrHikCamerasFromDom(includeEmpty = false) {
  const rows = Array.from(document.querySelectorAll(".dfr-hik-camera-row")).slice(0, 128).map(row => ({
    alarm_source_name: row.querySelector(".dfr-hik-camera-source")?.value.trim() || "",
    dock_name: row.querySelector(".dfr-hik-camera-dock")?.value.trim() || ""
  }));
  return includeEmpty ? rows : rows.filter(row => row.alarm_source_name || row.dock_name);
}

function refreshDfrCameraDockOptions() {
  const docks = readDfrHikDocksFromDom();
  document.querySelectorAll(".dfr-hik-camera-dock").forEach(select => {
    const current = select.value;
    select.innerHTML = dfrDockOptions(docks, current);
  });
}

function addDfrDockRow() {
  const container = document.getElementById("dfrHikDockRows");
  if (!container) return;
  const docks = readDfrHikDocksFromDom(true);
  if (docks.length >= 5) return showToast("Maximum 5 Dock mappings allowed", "error");
  docks.push({name: "", project_uuid: ""});
  container.innerHTML = renderDfrHikDockRows(docks);
  refreshDfrCameraDockOptions();
}

function removeDfrDockRow(button) {
  const rows = Array.from(document.querySelectorAll(".dfr-hik-dock-row"));
  if (rows.length <= 1) return;
  button.closest(".dfr-hik-dock-row")?.remove();
  document.getElementById("dfrHikDockRows").innerHTML = renderDfrHikDockRows(readDfrHikDocksFromDom(true).length ? readDfrHikDocksFromDom(true) : [{name: "", project_uuid: ""}]);
  refreshDfrCameraDockOptions();
}

function addDfrCameraRow() {
  const container = document.getElementById("dfrHikCameraRows");
  if (!container) return;
  const cameras = readDfrHikCamerasFromDom(true);
  if (cameras.length >= 128) return showToast("Maximum 128 camera mappings allowed", "error");
  const docks = readDfrHikDocksFromDom();
  cameras.push({alarm_source_name: "", dock_name: docks[0]?.name || ""});
  container.innerHTML = renderDfrHikCameraRows(cameras, docks);
}

function removeDfrCameraRow(button) {
  const rows = Array.from(document.querySelectorAll(".dfr-hik-camera-row"));
  if (rows.length <= 1) return;
  button.closest(".dfr-hik-camera-row")?.remove();
  const docks = readDfrHikDocksFromDom();
  const cameras = readDfrHikCamerasFromDom(true);
  document.getElementById("dfrHikCameraRows").innerHTML = renderDfrHikCameraRows(cameras.length ? cameras : [{alarm_source_name: "", dock_name: docks[0]?.name || ""}], docks);
}

async function renderDfr(content) {
  const data = await api("/api/dfr");
  const dfrCfg = settingsCache.settings.modules.dfr || {};
  if (!isActiveContent(content, "DFR")) return;
  scheduleDfrRefresh();
  const events = data.events || [];
  const providers = data.providers || [];
  const providerMap = Object.fromEntries(providers.map(p => [String(p.name || "").toLowerCase(), p]));
  const providerName = normalizeDfrProviderName(selectedDfrProvider);

  if (!providerName) {
    const launchCards = [
      {name: "Hikvision", description: "Receive Hikvision DFR alarms and forward workflow requests to FH2.", future: false},
      {name: "Scylla", description: "Receive Scylla DFR alarms and forward workflow requests to FH2.", future: false},
      {name: "Milestone", description: "Future DFR connector. Not connected yet.", future: true},
      {name: "Genetec", description: "Future DFR connector. Not connected yet.", future: true}
    ];
    content.innerHTML = `
      <div class="module-header">
        <div><h1>DFR</h1></div>
        <span class="pill">${esc(data.today_count || 0)} events today</span>
      </div>
      <div class="grid dfr-launch-grid">
        ${launchCards.map(card => {
          const provider = providerMap[card.name.toLowerCase()] || {};
          const enabled = Boolean(provider.enabled);
          const statusText = card.future ? "Not connected" : (provider.status || (enabled ? "Enabled" : "Disabled"));
          const badgeClass = card.future ? "warn" : (enabled ? "online" : "offline");
          return `
            <div class="card dfr-provider-card ${card.future ? "is-disabled" : "clickable-card"}" ${card.future ? "" : `onclick="openDfrProvider('${escAttr(card.name)}')"`}>
              <div class="card-title-row">
                <h3>${esc(card.name)}</h3>
                <div class="button-row compact-row">
                  <span class="status-badge ${badgeClass}">${esc(statusText)}</span>
                  ${card.future ? "" : `<button class="icon-btn" title="Open ${escAttr(card.name)} separately" onclick="openDfrProviderSeparate('${escAttr(card.name)}', event)">&#8599;</button>`}
                </div>
              </div>
              <p class="muted">${esc(card.description)}</p>
              ${card.future ? `<div class="button-row"><button class="secondary small-btn" disabled>Coming Soon</button></div>` : ""}
            </div>`;
        }).join("")}
      </div>
      <div class="card dfr-events-card">
        <div class="card-title-row"><h3>Last DFR Events</h3><span class="pill">Last 5</span></div>
        <table class="table">
          <thead><tr><th>Time</th><th>Provider</th><th>Event</th><th>Status</th><th>Source</th></tr></thead>
          <tbody>
            ${events.slice(0, 5).map(e => `<tr>
              <td>${esc(formatDubaiTime(e.received_at))}</td>
              <td>${esc(e.provider)}</td>
              <td>${esc(e.event)}</td>
              <td><span class="status-badge ${e.status === "Event Sent to FH2" ? "online" : e.status === "Failed to Sent" ? "offline" : "warn"}">${esc(e.status)}</span></td>
              <td>${esc(e.project_uuid || e.source_ip || "--")}</td>
            </tr>`).join("") || `<tr><td colspan="5" class="muted">No DFR events received yet.</td></tr>`}
          </tbody>
        </table>
      </div>
    `;
    return;
  }

  const key = dfrProviderConfigKey(providerName);
  const provider = providerMap[key] || {name: providerName, endpoint: dfrProviderEndpoint(providerName), enabled: false, status: "disabled"};
  const providerCfg = dfrCfg[key] || {};
  const providerEvents = events.filter(e => String(e.provider || "").toLowerCase() === key);
  const latestProviderEvent = providerEvents[0] || null;
  const selectedProviderEvent = providerEvents.find(e => String(e.id) === String(selectedDfrEventId)) || latestProviderEvent;
  const selectedRaw = selectedProviderEvent?.raw || {};
  const alarmSource = selectedRaw.alarm_source_name || selectedRaw.alarmSourceName || selectedRaw.camera_name || selectedRaw.device_name || selectedRaw.source || "--";
  const latitude = selectedRaw.latitude || selectedRaw.lat || selectedRaw.location?.latitude || "--";
  const longitude = selectedRaw.longitude || selectedRaw.lng || selectedRaw.lon || selectedRaw.location?.longitude || "--";
  const description = selectedRaw.description || selectedRaw.message || selectedRaw.desc || selectedProviderEvent?.message || "--";
  const sentCount = providerEvents.filter(e => e.status === "Event Sent to FH2").length;
  const failedCount = providerEvents.filter(e => e.status === "Failed to Sent").length;
  const cancelledCount = providerEvents.filter(e => e.status === "Cancelled").length;
  const pendingCount = providerEvents.filter(e => !["Event Sent to FH2", "Failed to Sent", "Cancelled"].includes(e.status)).length;
  const enabledFieldId = key === "hikvision" ? "dfrHikEnabled" : "dfrScyllaEnabled";
  const tokenFieldId = "dfrScyllaToken";
  const tokenLabel = "Scylla Bearer Token";
  const endpoint = provider.endpoint || dfrProviderEndpoint(providerName);
  const statusClass = provider.enabled ? "online" : "offline";

  const commonSettingsFields = `
    <div class="settings-grid two-col">
      <label class="field"><span>FH2 Endpoint</span><input id="dfrFh2Endpoint" value="${escAttr(dfrCfg.common?.fh2_endpoint || "")}" placeholder="FH2 triggered workflow endpoint"><small class="muted">International: https://es-flight-api-us.djigate.com/openapi/v0.1/workflow | Europe: https://es-flight-api-eu.djigate.com/openapi/v0.1/workflow | FH2 On-Prem: https://{host}:30812/openapi/v0.1/workflow</small></label>
      <label class="field"><span>FH2 Workflow UUID</span><input id="dfrWorkflowUuid" value="${escAttr(dfrCfg.common?.workflow_uuid || "")}" placeholder="Workflow UUID from FH2 Automation"></label>
      <label class="field"><span>Organization Key</span><input id="dfrOrgKey" type="password" value="${dfrCfg.common?.organization_key ? "********" : ""}" placeholder="Saved as hidden"></label>
      <label class="field"><span>Retry Max</span><input id="dfrRetryMax" type="number" min="1" max="10" value="${escAttr(data.retry_max || 3)}"></label>
      <label class="field"><span>FH2 Alert Level</span><input id="dfrAlertLevel" type="number" min="1" max="5" value="${escAttr(dfrCfg.common?.alert_level || 3)}"><small class="muted">DJI range 1-5. Default 3.</small></label>
      <label class="field"><span>${esc(providerName)} Enabled</span><select id="${enabledFieldId}"><option value="true" ${providerCfg.enabled ? "selected" : ""}>Enabled</option><option value="false" ${!providerCfg.enabled ? "selected" : ""}>Disabled</option></select></label>
      ${key === "scylla" ? `<label class="field full-span"><span>${esc(tokenLabel)}</span><input id="${tokenFieldId}" type="password" value="${providerCfg.bearer_token ? "********" : ""}" placeholder="Saved as hidden"></label>` : ""}
    </div>`;

  let settingsPanel = "";
  if (hasPermission("dfr_settings")) {
    if (key === "hikvision") {
      const docks = normalizedHikDocks(providerCfg);
      const cameras = normalizedHikCameras(providerCfg, docks);
      settingsPanel = `
          <div class="card dfr-settings-card">
          <div class="card-title-row">
            <h3>Hikvision DFR Settings</h3>
            <div class="button-row compact-row">
              <button class="ghost small-btn" onclick="openDfrMonitor()">Close</button>
              <button class="primary small-btn" onclick="saveDfrSettings()">Save DFR Settings</button>
            </div>
          </div>
          ${commonSettingsFields}
          <div class="dfr-map-section">
            <div class="card-title-row"><h3>Dock / DJI Project Mapping</h3><button class="secondary small-btn" onclick="addDfrDockRow()">+ Add Dock</button></div>
            <p class="muted">Match each Dock name to its DJI Project UUID. Maximum 5 Docks.</p>
            <div id="dfrHikDockRows" class="dfr-map-rows">${renderDfrHikDockRows(docks)}</div>
          </div>
          <div class="dfr-map-section">
            <div class="card-title-row"><h3>Alarm Source Mapping</h3><button class="secondary small-btn" onclick="addDfrCameraRow()">+ Add Camera</button></div>
            <p class="muted">Match Hikvision alarm_source_name to the Dock / DJI Project. Maximum 128 cameras.</p>
            <div id="dfrHikCameraRows" class="dfr-map-rows">${renderDfrHikCameraRows(cameras, docks)}</div>
          </div>
        </div>`;
    } else {
      settingsPanel = `
          <div class="card dfr-settings-card">
          <div class="card-title-row">
            <h3>${esc(providerName)} DFR Settings</h3>
            <div class="button-row compact-row">
              <button class="ghost small-btn" onclick="openDfrMonitor()">Close</button>
              <button class="primary small-btn" onclick="saveDfrSettings()">Save DFR Settings</button>
            </div>
          </div>
          ${commonSettingsFields}
          <div class="settings-grid two-col dfr-scylla-project-row">
            <label class="field full-span"><span>Default DJI Project UUID</span><input id="dfrScyllaProject" value="${escAttr(providerCfg.default_project_id || "")}" placeholder="DJI Project UUID"></label>
          </div>
        </div>`;
    }
  }

  const monitorPanel = `
    <div class="summary-grid dfr-monitor-summary">
      <div class="metric-card"><span>Event Received</span><strong>${esc(providerEvents.length)}</strong></div>
      <div class="metric-card"><span>Event Sent to FH2</span><strong>${esc(sentCount)}</strong></div>
      <div class="metric-card"><span>Failed to Sent</span><strong>${esc(failedCount)}</strong></div>
      <div class="metric-card"><span>Cancelled</span><strong>${esc(cancelledCount)}</strong></div>
      <div class="metric-card"><span>Pending / Queue</span><strong>${esc(pendingCount)}</strong></div>
    </div>
    <div class="grid two-col-grid">
      <div class="card">
        <div class="card-title-row"><h3>Last ${esc(providerName)} Events</h3><span class="pill">Last 10</span></div>
        <table class="table compact-table">
          <thead><tr><th>Time</th><th>Event</th><th>Alarm Source</th><th>Project UUID</th><th>${esc(providerName)} -> AERO SYNC</th><th>AERO SYNC -> DJI</th></tr></thead>
          <tbody>
            ${providerEvents.slice(0, 10).map(e => {
              const raw = e.raw || {};
              const src = raw.alarm_source_name || raw.alarmSourceName || raw.camera_name || raw.device_name || raw.source || "--";
              const isTestEvent = Boolean(raw.test) || String(e.event || "").toUpperCase().includes("TEST");
              const receivedOk = e.event !== "invalid_json" && e.status !== "Cancelled";
              const receiveLabel = isTestEvent ? "Test" : (receivedOk ? "Received" : "Rejected");
              const receiveClass = isTestEvent ? "warn" : (receivedOk ? "online" : "offline");
              const djiStatus = e.status === "Event Sent to FH2" ? "Sent" : e.status === "Failed to Sent" ? "Failed" : e.status === "Cancelled" ? "Cancelled" : "Pending";
              const djiClass = e.status === "Event Sent to FH2" ? "online" : e.status === "Failed to Sent" ? "offline" : e.status === "Cancelled" ? "warn" : "pending";
              return `<tr class="selectable-row ${String(e.id) === String(selectedProviderEvent?.id) ? "selected-row" : ""}" onclick="selectDfrEvent('${escAttr(e.id)}')">
                <td>${esc(formatDubaiTime(e.received_at))}</td>
                <td>${esc(e.event)}</td>
                <td>${esc(src)}</td>
                <td>${esc(e.project_uuid || "--")}</td>
                <td><span class="status-badge ${receiveClass}">${esc(receiveLabel)}</span></td>
                <td><span class="status-badge ${djiClass}">${esc(djiStatus)}</span></td>
              </tr>`;
            }).join("") || `<tr><td colspan="6" class="muted">No ${esc(providerName)} DFR events received yet.</td></tr>`}
          </tbody>
        </table>
      </div>
      <div class="card">
        <div class="card-title-row"><h3>Selected Event Details</h3><span class="pill">${selectedProviderEvent ? esc(selectedProviderEvent.status || "Event Received") : "No event"}</span></div>
        ${selectedProviderEvent ? `
          <div class="detail-grid">
            <div><span class="muted">Alarm Source</span><strong>${esc(alarmSource)}</strong></div>
            <div><span class="muted">GPS</span><strong>${esc(latitude)}, ${esc(longitude)}</strong></div>
            <div><span class="muted">FH2 Project UUID</span><strong>${esc(selectedProviderEvent.project_uuid || "--")}</strong></div>
            <div><span class="muted">${esc(providerName)} -> AERO SYNC</span><strong>${esc((selectedRaw.test || String(selectedProviderEvent.event || "").toUpperCase().includes("TEST")) ? "Test" : "Received")}</strong></div>
            <div><span class="muted">AERO SYNC -> DJI</span><strong>${esc(selectedProviderEvent.status === "Event Sent to FH2" ? "Sent" : selectedProviderEvent.status === "Failed to Sent" ? "Failed" : selectedProviderEvent.status || "Pending")}</strong></div>
            <div class="full-span"><span class="muted">Description / Reply</span><strong>${esc(description)}</strong></div>
          </div>
          <pre class="json-box dfr-selected-json">${esc(JSON.stringify(selectedRaw, null, 2))}</pre>
        ` : `<p class="muted">Select a DFR event to see full details.</p>`}
      </div>
    </div>`;

  const testExampleJson = JSON.stringify(dfrExamplePayload(providerName), null, 2);
  content.innerHTML = `
    <div class="module-header">
      <div><h1>DFR - ${esc(providerName)}</h1></div>
      <div class="button-row compact-row">
        <button class="ghost small-btn" onclick="backToDfrProviders()">Back</button>
        ${hasPermission("dfr_settings") ? (selectedDfrTab === "settings" ? `<button class="secondary small-btn" onclick="openDfrMonitor()">Monitor</button>` : `<button class="secondary small-btn" onclick="openDfrSettings()">Settings</button>`) : ""}
        <span class="pill">${esc(providerEvents.length)} ${esc(providerName)} events</span>
      </div>
    </div>
    <div class="module-layout">
      <aside class="module-side">
        <div class="metric-card"><span>${esc(providerName)} Status</span><strong>${provider.enabled ? "Running" : "Disabled"}</strong></div>
        <div class="metric-card"><span>Retry Limit</span><strong>${esc(data.retry_max || 3)}</strong></div>
        <div class="metric-card"><span>Queue</span><strong>${esc(data.queue || 0)}</strong></div>
        <div class="metric-card"><span>Log</span><strong>${data.log_ok === false ? "Check" : "Ready"}</strong></div>
      </aside>
      <section class="module-main">
        <div class="grid two-col-grid dfr-receiver-test-grid">
          <div class="card dfr-provider-card">
            <div class="card-title-row"><h3>${esc(providerName)} Receiver</h3><span class="status-badge ${statusClass}">${esc(provider.status || (provider.enabled ? "Enabled" : "Disabled"))}</span></div>
            <p class="muted">Receiver endpoint: <code>${esc(endpoint)}</code></p>
            <div class="button-row">
              <button class="secondary small-btn" onclick="testDfrProvider('${escAttr(providerName)}')">Test FH2 Trigger</button>
              ${hasPermission("dfr_settings") ? `<button class="${provider.enabled ? "ghost" : "secondary"} small-btn" onclick="toggleDfrProvider('${escAttr(providerName)}', ${provider.enabled ? "false" : "true"})">${provider.enabled ? "Disable" : "Enable"} ${esc(providerName)}</button>` : ""}
            </div>
          </div>
          <div class="card dfr-test-card">
            <div class="card-title-row"><h3>Paste Test Event JSON</h3><button class="ghost small-btn" onclick="fillDfrTestExample('${escAttr(providerName)}')">Use Example</button></div>
            <textarea id="dfrTestPayload" class="code-textarea" rows="8" spellcheck="false" wrap="off" onpaste="setTimeout(formatDfrTestJson, 0)" oninput="clearTimeout(window.dfrJsonFormatTimer); window.dfrJsonFormatTimer = setTimeout(formatDfrTestJson, 500)" onblur="formatDfrTestJson()">${esc(testExampleJson)}</textarea>
            <p class="muted">Paste the exact event body from ${esc(providerName)} here.</p>
          </div>
        </div>
        ${selectedDfrTab === "settings" ? (settingsPanel || `<div class="card"><p class="muted">DFR Settings permission required.</p></div>`) : monitorPanel}
      </section>
    </div>
  `;
}

function isEditingDfr() {
  if (activePage !== "DFR") return false;
  if (selectedDfrTab === "settings") return true;
  const active = document.activeElement;
  return Boolean(active?.closest?.(".dfr-settings-card, .dfr-test-card"));
}

function scheduleDfrRefresh() {
  if (dfrRefreshTimer || activePage !== "DFR") return;
  dfrRefreshTimer = setInterval(async () => {
    if (activePage !== "DFR") {
      clearInterval(dfrRefreshTimer);
      dfrRefreshTimer = null;
      return;
    }
    if (isEditingDfr()) return;
    try {
      await renderDfr(document.getElementById("content"));
    } catch (err) {
      console.warn("DFR refresh failed", err);
    }
  }, 5000);
}

function dfrExamplePayload(provider) {
  const providerName = normalizeDfrProviderName(provider);
  if (providerName === "Scylla") {
    return {
      alarm_source_name: "Aeronex Office Boundary Camera",
      latitude: 25.456,
      longitude: 55.456,
      description: "Person detected"
    };
  }
  return {
    alarm_source_name: "Aeronex Office Boundary Camera",
    latitude: 25.456,
    longitude: 55.456,
    description: "Person detected"
  };
}

function fillDfrTestExample(provider) {
  const box = document.getElementById("dfrTestPayload");
  if (!box) return;
  box.value = JSON.stringify(dfrExamplePayload(provider), null, 2);
}

function formatDfrTestJson() {
  const box = document.getElementById("dfrTestPayload");
  if (!box) return;
  const raw = box.value.trim();
  if (!raw) return;
  try {
    box.value = JSON.stringify(JSON.parse(raw), null, 2);
  } catch (err) {
    // Keep the user's text as-is until they finish editing valid JSON.
  }
}

async function testDfrProvider(provider) {
  const providerName = normalizeDfrProviderName(provider);
  if (!providerName) {
    showToast(`${provider} DFR integration is not connected yet`, "error");
    return;
  }
  let payload = null;
  formatDfrTestJson();
  const rawPayload = document.getElementById("dfrTestPayload")?.value?.trim() || "";
  if (rawPayload) {
    try {
      payload = JSON.parse(rawPayload);
    } catch (err) {
      showToast(`Invalid test JSON: ${err.message}`, "error");
      return;
    }
  }
  try {
    const result = await api("/api/dfr/test", {
      method: "POST",
      body: JSON.stringify({provider: providerName, payload})
    });
    if (result.ok) {
      showToast(`${providerName} FH2 trigger test sent`);
    } else {
      showToast(`FH2 test failed: ${result.error || result.message || "check DFR event"}`, "error");
    }
    await renderDfr(document.getElementById("content"));
  } catch (err) {
    showToast(err.message || "FH2 trigger test failed", "error");
  }
}

async function toggleDfrProvider(provider, enabled) {
  if (!hasPermission("dfr_settings")) {
    showToast("DFR settings permission required", "error");
    return;
  }
  const providerName = normalizeDfrProviderName(provider);
  const key = dfrProviderConfigKey(providerName);
  const current = settingsCache.settings.modules.dfr || {};
  const dfr = {
    ...current,
    enabled: true,
    [key]: {
      ...(current[key] || {}),
      enabled: Boolean(enabled)
    }
  };
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({settings: {modules: {dfr}}})
    });
    settingsCache = {settings: data.settings, urls: data.urls};
    showToast(`${providerName} ${enabled ? "enabled" : "disabled"}`);
    await renderDfr(document.getElementById("content"));
  } catch (err) {
    showToast(err.message || `Failed to update ${providerName}`, "error");
  }
}

async function saveDfrSettings() {
  const current = settingsCache.settings.modules.dfr || {};
  const getValue = (id, fallback = "") => {
    const el = document.getElementById(id);
    if (!el) return fallback;
    if (el.type === "password" && el.value === "********") return fallback;
    return el.value;
  };
  const getBool = (id, fallback = false) => {
    const el = document.getElementById(id);
    if (!el) return Boolean(fallback);
    return el.value === "true";
  };
  const projectsInput = document.getElementById("dfrProjects")?.value;
  const projects = projectsInput === undefined ? (current.projects || []) : projectsInput
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => {
      const parts = line.split("|");
      return {name: (parts[0] || "").trim(), uuid: (parts[1] || parts[0] || "").trim()};
    });
  const hikDocksPresent = Boolean(document.getElementById("dfrHikDockRows"));
  const hikCamerasPresent = Boolean(document.getElementById("dfrHikCameraRows"));
  const dfr = {
    ...current,
    enabled: true,
    retry_max: Number(getValue("dfrRetryMax", current.retry_max || 3) || 3),
    common: {
      ...(current.common || {}),
      fh2_endpoint: getValue("dfrFh2Endpoint", current.common?.fh2_endpoint || ""),
      workflow_uuid: getValue("dfrWorkflowUuid", current.common?.workflow_uuid || ""),
      organization_key: getValue("dfrOrgKey", current.common?.organization_key || ""),
      alert_level: Math.max(1, Math.min(5, Number(getValue("dfrAlertLevel", current.common?.alert_level || 3) || 3)))
    },
    projects,
    scylla: {
      ...(current.scylla || {}),
      enabled: getBool("dfrScyllaEnabled", current.scylla?.enabled),
      bearer_token: getValue("dfrScyllaToken", current.scylla?.bearer_token || ""),
      default_project_id: getValue("dfrScyllaProject", current.scylla?.default_project_id || "")
    },
    hikvision: {
      ...(current.hikvision || {}),
      enabled: getBool("dfrHikEnabled", current.hikvision?.enabled),
      token: current.hikvision?.token || "",
      docks: hikDocksPresent ? readDfrHikDocksFromDom() : (current.hikvision?.docks || []),
      cameras: hikCamerasPresent ? readDfrHikCamerasFromDom() : (current.hikvision?.cameras || [])
    }
  };
  if (dfr.hikvision.docks.length > 5) dfr.hikvision.docks = dfr.hikvision.docks.slice(0, 5);
  if (dfr.hikvision.cameras.length > 128) dfr.hikvision.cameras = dfr.hikvision.cameras.slice(0, 128);
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({settings: {modules: {dfr}}})
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  showToast("DFR settings saved");
  await renderDfr(document.getElementById("content"));
}

async function renderEvents(content) {
  const data = await api("/api/events");
  if (!isActiveContent(content, "Events")) return;
  const events = data.events || [];
  const latest = events[0];
  const selected = events.find(e => String(e.id) === String(selectedEventId)) || latest;
  selectedEventId = selected?.id || null;
  content.innerHTML = `
    <div class="module-header compact-module-header">
      <div><h1>EventAPI Receiver</h1></div>
      <span class="pill">${data.count} received</span>
    </div>
    <div class="compact-status-row">
      <span><small>Connection</small><strong>${data.available ? "Ready" : "Waiting"}</strong></span>
      <span><small>Total Events</small><strong>${esc(data.count)}</strong></span>
      <span><small>Latest Event</small><strong>${esc(latest?.event_type || "None")}</strong></span>
      <span><small>Signature</small><strong>${latest ? (latest.signature_valid ? "Valid" : "Unsigned") : "--"}</strong></span>
    </div>
    ${sourceNotice(data, "Event Receiver database path")}
    <div class="compact-two-panel">
      <div class="card">
        <h3>Received Events</h3>
        <table class="table compact-table">
          <thead><tr><th>ID</th><th>Time</th><th>Type</th><th>Device SN</th><th>Source</th><th>Signature</th></tr></thead>
          <tbody>
            ${events.slice(0, 10).map(e => `<tr class="selectable-row ${String(e.id) === String(selectedEventId) ? "selected-row" : ""}" onclick="selectEvent('${escAttr(e.id)}')">
              <td>${esc(e.id)}</td><td>${esc(formatDubaiTime(e.received_at))}</td><td>${esc(e.event_type)}</td>
              <td>${esc(e.device_sn)}</td><td>${esc(e.source_ip)}</td><td>${e.signature_valid ? "OK" : "Unsigned"}</td>
            </tr>`).join("") || `<tr><td colspan="6">No events received</td></tr>`}
          </tbody>
        </table>
      </div>
      <div class="card">
        <div class="card-title-row"><h3>Selected Event Details</h3><span class="pill">ID ${esc(selected?.id || "--")}</span></div>
        <pre class="json-box compact-detail-json">${esc(selected ? JSON.stringify(selected.raw || selected.raw_json || {}, null, 2) : "No event received")}</pre>
      </div>
    </div>
  `;
}

async function selectEvent(id) {
  selectedEventId = id;
  await renderEvents(document.getElementById("content"));
}

function ridStatusPill(status) {
  const value = String(status || "offline").toLowerCase() === "online" ? "Online" : "Offline";
  return `<span class="pill ${value === "Online" ? "ok" : ""}">${value}</span>`;
}

function ridSourceDetails(x) {
  const parts = [];
  if (x.country) parts.push(`Country: ${esc(x.country)}`);
  if (x.battery_percent != null) parts.push(`Battery: ${esc(x.battery_percent)}%`);
  if (x.gps_number != null) parts.push(`GPS: ${esc(x.gps_number)}`);
  if (x.device_version) parts.push(`Version: ${esc(x.device_version)}`);
  return parts.length ? `<div class="rid-device-meta">${parts.map(v=>`<span>${v}</span>`).join("")}</div>` : "";
}

function ridMessageTypeLabel(m) { return String(m?.message_type || "RID").replace(/_/g," "); }
function ridCompactPayload(m) {
  const p=m?.payload||{};
  const d=(p&&typeof p==="object")?p:{};
  const flat=[];
  const walk=(o,prefix="")=>{ if(!o||typeof o!=="object")return; Object.entries(o).slice(0,40).forEach(([k,v])=>{ const key=prefix?`${prefix}.${k}`:k; if(v===null||v===undefined)return; if(typeof v==="object") walk(v,key); else if(flat.length<14) flat.push([key,v]); }); };
  walk(d);
  return flat;
}
function ridSelectedMessageHtml(m) {
  if(!m) return '<div class="empty-state">Select a message from Last 20 Received Messages.</div>';
  const rows=ridCompactPayload(m);
  return `<div class="rid-selected-head"><strong>${esc(ridMessageTypeLabel(m))}</strong><span>${esc(formatDubaiTime(m.received_at))}</span></div><div class="rid-selected-grid">${rows.map(([k,v])=>`<div class="rid-selected-field"><span>${esc(k)}</span><strong>${esc(String(v))}</strong></div>`).join('')}</div>`;
}

async function renderRid(content) {
  const data = await api("/api/rid");
  if (!isActiveContent(content, "RID")) return;
  const sources=data.sources||[], targets=data.targets||[], recent=data.recent_messages||[], live=data.live_messages||[], status=data.status_messages||[];
  if(!window._ridSelectedMessageId && recent[0]) window._ridSelectedMessageId=recent[0].id;
  const selected=recent.find(x=>String(x.id)===String(window._ridSelectedMessageId))||recent[0]||null;
  content.innerHTML = `
    <div class="page-head rid-page-head">
      <div><h1>RID - Remote Device Identification</h1></div>
      <div class="toolbar-actions">
        <button class="secondary" onclick="openRidRawData()">Raw Data</button>
        <button class="secondary" onclick="openRidHistory()">History</button>
        <button class="secondary" onclick="openRidMap()">Map</button>
        ${hasPermission("settings") ? `<button class="secondary" onclick="openRidSettings()">Settings</button>` : ""}
      </div>
    </div>
    <div class="rid-kpi-grid">
      <div class="metric-card"><span>RID Online</span><strong>${esc(data.online_sources||0)}</strong></div>
      <div class="metric-card"><span>RID Offline</span><strong>${esc(data.offline_sources||0)}</strong></div>
      <div class="metric-card"><span>Aircraft Detected</span><strong>${esc(data.active_targets||0)}</strong></div>
      <div class="metric-card"><span>Active Tracks</span><strong>${esc(data.active_tracks||0)}</strong></div>
    </div>
    <div class="rid-active-strip">
      <div class="rid-active-strip-title"><strong>Active Tracks</strong><span class="pill">${esc(targets.length)}</span></div>
      <div class="rid-active-strip-list">
        ${targets.map(x=>`<button class="rid-active-track" onclick="openRidTrack('${escAttr(x.track_id||x.trace_id||'')}')"><strong>${esc(x.model||x.uav_id||x.id||'RID Aircraft')}</strong><span>${esc(x.uav_id||x.id||'')}</span><small>${esc(x.altitude??'-')} m | ${esc(x.speed??'-')} m/s | ${esc(x.heading??x.azimuth??'-')}° | ${esc(x.source_name||x.source_id||'')}</small></button>`).join('')||'<span class="rid-active-empty">No active RID aircraft detected.</span>'}
      </div>
    </div>
    <div class="rid-message-grid">
      <div class="card rid-message-card"><div class="panel-title"><h3>Live Messages</h3><span class="pill">${esc(data.message_count||0)}</span></div><div class="rid-live-list">${live.map(m=>`<div class="rid-live-row"><strong>${esc(ridMessageTypeLabel(m))}</strong><span>${esc(m.device_name||m.source_sn||'')}</span><small>${esc(formatDubaiTime(m.received_at))}</small></div>`).join('')||'<div class="empty-state">No RID messages received.</div>'}</div></div>
      <div class="card rid-message-card"><div class="panel-title"><h3>Message Status</h3></div><div class="mqtt-status-list">${status.map(x=>`<div class="mqtt-status-item ${escAttr(x.level||'info')}"><strong>${esc(x.title||'RID')}</strong><span>${esc(x.message||'')}</span><small>${esc(formatDubaiTime(x.time))}</small></div>`).join('')||'<div class="empty-state">No RID status yet.</div>'}</div></div>
      <div class="card rid-message-card"><div class="panel-title"><h3>Last 20 Received Messages</h3></div><div class="rid-last20-list">${recent.map(m=>`<button class="rid-message-row ${String(selected?.id)===String(m.id)?'selected':''}" onclick="selectRidMessage('${escAttr(m.id)}')"><span>${esc(formatDubaiTime(m.received_at))}</span><strong>${esc(ridMessageTypeLabel(m))}</strong><small>${esc(m.device_name||m.source_sn||'')}</small><em>${esc(m.bytes||0)} B</em></button>`).join('')||'<div class="empty-state">No RID messages received.</div>'}</div></div>
      <div class="card rid-message-card"><div class="panel-title"><h3>Selected Message</h3></div><div id="ridSelectedMessage">${ridSelectedMessageHtml(selected)}</div></div>
    </div>`;
  if (ridRefreshTimer) clearInterval(ridRefreshTimer);
  ridRefreshTimer=setInterval(async()=>{ if(activePage!=="RID"||document.getElementById("ridMapCanvas"))return; try{await renderRid(content);}catch(_){} },3000);
}

function selectRidMessage(id){ window._ridSelectedMessageId=id; if(activePage==="RID") renderRid(document.getElementById("content")).catch(console.error); }

async function openRidRawData(){
  let modal=document.getElementById('ridRawModal'); if(!modal){modal=document.createElement('div');modal.id='ridRawModal';modal.className='aerosync-modal';document.body.appendChild(modal);} modal.classList.add('show');
  modal.innerHTML=`<div class="aerosync-modal-panel raw-modal-panel rid-raw-panel"><div class="card-title-row"><h2>RID Raw Data</h2><div class="toolbar-actions"><button id="ridRawAutoBtn" class="secondary small-btn" onclick="toggleRidRawAutoScroll()">Auto Scroll ON</button><button class="secondary small-btn" onclick="copyRidRawAll()">Copy All</button><button class="secondary" onclick="closeRidRawData()">Close</button></div></div><div class="raw-filter-grid"><label class="field"><span>Search</span><input id="ridRawSearch"></label><label class="field"><span>RID Receiver</span><input id="ridRawSource"></label><label class="field"><span>Limit</span><select id="ridRawLimit"><option>100</option><option>250</option><option>500</option></select></label></div><div class="toolbar-actions"><button class="primary" onclick="loadRidRawData(false)">Search</button></div><div id="ridRawResults" class="rid-raw-stream"></div></div>`;
  ridRawAutoScroll=true; await loadRidRawData(true); if(ridRawRefreshTimer)clearInterval(ridRawRefreshTimer); ridRawRefreshTimer=setInterval(()=>{if(document.getElementById('ridRawModal')?.classList.contains('show'))loadRidRawData(true).catch(()=>{});},1500);
}
function closeRidRawData(){document.getElementById('ridRawModal')?.classList.remove('show');if(ridRawRefreshTimer){clearInterval(ridRawRefreshTimer);ridRawRefreshTimer=null;}}
function toggleRidRawAutoScroll(){ridRawAutoScroll=!ridRawAutoScroll;const b=document.getElementById('ridRawAutoBtn');if(b)b.textContent=`Auto Scroll ${ridRawAutoScroll?'ON':'OFF'}`;}
function copyRidRawAll(){const el=document.getElementById('ridRawStreamText');if(el)copyText(el.textContent||'');}
async function loadRidRawData(liveRefresh=false){
  const p=new URLSearchParams(); [['q','ridRawSearch'],['source','ridRawSource'],['limit','ridRawLimit']].forEach(([k,id])=>{const v=document.getElementById(id)?.value?.trim();if(v)p.set(k,v)}); const box=document.getElementById('ridRawResults'); if(!box)return; if(!liveRefresh)box.innerHTML='<div class="loading-card">Loading...</div>';
  try{const d=await api(`/api/rid/raw?${p}`); const rows=(d.messages||[]).slice().reverse(); const text=rows.map(r=>`${formatDubaiTime(r.received_at)} | ${r.device_name||r.source_sn||'RID'} | ${r.topic||''}\n${JSON.stringify(r.payload||{},null,2)}`).join('\n\n'); box.innerHTML=rows.length?`<pre id="ridRawStreamText" class="rid-raw-stream-text">${esc(text)}</pre>`:'<div class="empty-state">No RID raw data.</div>'; if(ridRawAutoScroll)box.scrollTop=box.scrollHeight;}catch(e){if(!liveRefresh)box.innerHTML=`<div class="warn-card">${esc(e.message)}</div>`;}
}

async function openRidHistory(){ let modal=document.getElementById('ridHistoryModal'); if(!modal){modal=document.createElement('div');modal.id='ridHistoryModal';modal.className='aerosync-modal';document.body.appendChild(modal);} modal.classList.add('show'); modal.innerHTML=`<div class="aerosync-modal-panel raw-modal-panel"><div class="card-title-row"><h2>RID History</h2><button class="secondary" onclick="document.getElementById('ridHistoryModal')?.classList.remove('show')">Close</button></div><div class="raw-filter-grid"><label class="field"><span>Track / UAV / Model</span><input id="ridHistorySearch"></label><label class="field"><span>RID Receiver</span><input id="ridHistorySource"></label></div><div class="toolbar-actions"><button class="primary" onclick="loadRidHistory()">Search</button></div><div id="ridHistoryResults" class="raw-results"></div></div>`; loadRidHistory(); }
async function loadRidHistory(){const p=new URLSearchParams();const q=document.getElementById('ridHistorySearch')?.value?.trim();const src=document.getElementById('ridHistorySource')?.value?.trim();if(q)p.set('q',q);if(src)p.set('source',src);const box=document.getElementById('ridHistoryResults');if(box)box.innerHTML='<div class="loading-card">Loading...</div>';try{const d=await api(`/api/rid/history?${p}`);const rows=d.tracks||[];if(box)box.innerHTML=rows.length?rows.map(x=>`<button class="rid-history-row" onclick="openRidTrack('${escAttr(x.track_id)}')"><span><strong>${esc(x.track_id)}</strong><small>${esc(x.uav_id||'')} | ${esc(x.model||'Unknown')} | ${esc(x.source_name||x.source_id||'')}</small></span><span>${esc(formatDubaiTime(x.history_start||x.first_seen))}</span><span>${esc(x.point_count||0)} updates</span></button>`).join(''):'<div class="empty-state">No RID tracks found.</div>';}catch(e){if(box)box.innerHTML=`<div class="warn-card">${esc(e.message)}</div>`;} }
async function openRidTrack(trackId){if(!trackId)return showToast('Track ID not available','error');const d=await api(`/api/rid/track?track_id=${encodeURIComponent(trackId)}`);let modal=document.getElementById('ridTrackModal');if(!modal){modal=document.createElement('div');modal.id='ridTrackModal';modal.className='aerosync-modal';document.body.appendChild(modal);}modal.classList.add('show');const s=d.summary||{};modal.innerHTML=`<div class="aerosync-modal-panel raw-modal-panel"><div class="card-title-row"><div><h2>Track ${esc(trackId)}</h2><small>${esc(s.model||'')} | ${esc(s.uav_id||'')}</small></div><button class="secondary" onclick="document.getElementById('ridTrackModal')?.classList.remove('show')">Close</button></div><div class="rid-track-detail-grid">${[['UAV ID',s.uav_id],['Model',s.model],['Source',s.source_name||s.source_id],['Altitude',s.altitude],['Height',s.height],['Speed',s.speed],['Heading',s.heading],['Frequency',s.frequency],['Distance',s.distance],['Azimuth',s.azimuth],['Pilot Lat',s.pilot_lat],['Pilot Lon',s.pilot_lng],['Home Lat',s.home_lat],['Home Lon',s.home_lng],['RSSI',s.rssi],['SNR',s.snr],['Confidence',s.confidence],['Duration',s.duration],['User ID',s.user_id],['Area',s.area_flag],['Whitelist',s.whitelist_id]].map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v??'-')}</strong></div>`).join('')}</div><div class="toolbar-actions"><button class="secondary" onclick="openRidMap('${escAttr(s.id||s.uav_id||'')}')">Map</button></div><div class="panel-title"><h3>Track Updates</h3><span class="pill">${esc(d.count||0)}</span></div><div class="rid-track-points">${(d.points||[]).slice(-200).reverse().map(x=>`<div><span>${esc(formatDubaiTime(x.recorded_at||x.last_seen))}</span><strong>${esc(x.lat??'-')}, ${esc(x.lng??'-')}</strong><small>${esc(x.altitude??'-')} m | ${esc(x.speed??'-')} m/s</small></div>`).join('')}</div></div>`;}

async function openRidSettings() {
  if (!hasPermission("settings")) return showToast("Settings permission required", "error");
  ridSettingsData = await api("/api/rid");
  let modal = document.getElementById("ridSettingsModal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "ridSettingsModal";
    modal.className = "aerosync-modal show";
    document.body.appendChild(modal);
  } else {
    modal.classList.add("show");
  }
  renderRidSettingsModal();
}

function closeRidSettings() {
  document.getElementById("ridSettingsModal")?.remove();
}

function renderRidSettingsModal(editSerial="") {
  const modal = document.getElementById("ridSettingsModal");
  if (!modal) return;
  const sources = ridSettingsData?.sources || [];
  const maxDevices = Number(ridSettingsData?.max_devices || 5);
  const edit = sources.find(x=>String(x.serial_no||x.id)===String(editSerial));
  const showForm = Boolean(edit) || sources.length === 0 || modal.dataset.adding === "1";
  modal.innerHTML = `<div class="aerosync-modal-panel rid-settings-panel">
    <div class="card-title-row"><h2>RID Settings</h2><button class="secondary" onclick="closeRidSettings()">Close</button></div>
    <div class="rid-settings-list">
      ${sources.map(x=>`<div class="rid-setting-row">
        <div><strong>${esc(x.name||x.serial_no||x.id)}</strong><small>${esc(x.brand||"")} | ${esc(x.serial_no||x.id||"")}</small></div>
        <div>${ridStatusPill(x.status)}</div>
        <div class="toolbar-actions"><button class="secondary small-btn" onclick="editRidDevice('${escAttr(x.serial_no||x.id)}')">Edit</button><button class="ghost small-btn" onclick="removeRidDevice('${escAttr(x.serial_no||x.id)}')">Remove</button></div>
      </div>`).join("")}
    </div>
    ${showForm ? `<div class="card rid-device-form">
      <h3>${edit ? "Edit RID Device" : "Add RID Device"}</h3>
      <div class="settings-grid two-col">
        <label class="field"><span>Serial No.</span><input id="ridSerialNo" value="${escAttr(edit?.serial_no||edit?.id||"")}" autocomplete="off"></label>
        <label class="field"><span>Device Name</span><input id="ridDeviceName" value="${escAttr(edit?.name||"")}" autocomplete="off"></label>
        <label class="field"><span>Brand</span><select id="ridBrand"><option value="Terjin" ${edit?.brand==="Terjin"?"selected":""}>Terjin</option><option value="ArcGine" ${(!edit||edit?.brand==="ArcGine")?"selected":""}>ArcGine</option></select></label>
      </div>
      <div class="toolbar-actions"><button class="primary" onclick="saveRidDevice('${escAttr(edit?.serial_no||edit?.id||"")}')">Save</button>${sources.length ? `<button class="secondary" onclick="cancelRidDeviceForm()">Cancel</button>` : ""}</div>
    </div>` : ""}
    ${!showForm && sources.length < maxDevices ? `<div class="toolbar-actions"><button class="primary" onclick="addRidDeviceForm()">+ Add RID Device</button></div>` : ""}
  </div>`;
}

function addRidDeviceForm() {
  const modal=document.getElementById("ridSettingsModal"); if(!modal) return;
  modal.dataset.adding="1"; renderRidSettingsModal();
}

function cancelRidDeviceForm() {
  const modal=document.getElementById("ridSettingsModal"); if(!modal) return;
  modal.dataset.adding="0"; renderRidSettingsModal();
}

function editRidDevice(serialNo) {
  const modal=document.getElementById("ridSettingsModal"); if(modal) modal.dataset.adding="0";
  renderRidSettingsModal(serialNo);
}

async function saveRidDevice(originalSerial="") {
  const serialNo = document.getElementById("ridSerialNo")?.value.trim() || "";
  const deviceName = document.getElementById("ridDeviceName")?.value.trim() || "";
  const brand = document.getElementById("ridBrand")?.value || "ArcGine";
  if (!serialNo || !deviceName) return showToast("Serial No. and Device Name are required", "error");
  try {
    ridSettingsData = await api("/api/rid/device", {method:"POST", body:JSON.stringify({action: originalSerial ? "update" : "add", serial_no: originalSerial || serialNo, new_serial_no: serialNo, device_name: deviceName, brand})});
    const modal=document.getElementById("ridSettingsModal"); if(modal) modal.dataset.adding="0";
    renderRidSettingsModal();
    showToast("RID device saved");
    if (activePage === "RID") await renderRid(document.getElementById("content"));
  } catch (err) { showToast(err.message || "RID device save failed", "error"); }
}

async function removeRidDevice(serialNo) {
  if (!confirm(`Remove RID device ${serialNo}?`)) return;
  try {
    ridSettingsData = await api("/api/rid/device", {method:"POST", body:JSON.stringify({action:"remove", serial_no:serialNo})});
    renderRidSettingsModal();
    showToast("RID device removed");
    if (activePage === "RID") await renderRid(document.getElementById("content"));
  } catch (err) { showToast(err.message || "RID device remove failed", "error"); }
}

function openRidMap(selectedId="") {
  if (ridRefreshTimer) { clearInterval(ridRefreshTimer); ridRefreshTimer=null; }
  const content=document.getElementById("content");
  content.innerHTML=`<div class="page-head"><div><h1>RID Live Map</h1></div><button class="secondary" onclick="goModule('RID')">Back to RID</button></div><div class="card rid-map-card"><div id="ridMapCanvas" class="rid-map-canvas"><div class="map-loading">Loading map...</div></div></div>`;
  setTimeout(()=>loadRidMap(selectedId),50);
}

async function loadRidMap(selectedId="") {
  const data=await api("/api/rid");
  const el=document.getElementById("ridMapCanvas"); if(!el)return;
  try{
    await loadLeaflet(); if(!window.L)return;
    if(window._ridLeafletMap){window._ridLeafletMap.remove();window._ridLeafletMap=null;}
    const targets=(data.targets||[]).filter(x=>Number.isFinite(Number(x.lat))&&Number.isFinite(Number(x.lng)));
    const sources=(data.sources||[]).filter(x=>Number.isFinite(Number(x.lat))&&Number.isFinite(Number(x.lng)));
    const cfg=settingsCache?.settings?.modules?.map||{};
    const selected=targets.find(x=>String(x.id)===String(selectedId)||String(x.uav_id)===String(selectedId));
    const first=selected||targets[0]||sources[0];
    const center=first?[Number(first.lat),Number(first.lng)]:[Number(cfg.default_lat||25.2048),Number(cfg.default_lng||55.2708)];
    const map=L.map(el,{zoomControl:true,attributionControl:true}).setView(center,Number(cfg.default_zoom||12)); window._ridLeafletMap=map;
    const mode=cfg.mode==='offline'?'offline':'online'; const tileUrl=mode==='offline'?'/map/tiles/{z}/{x}/{y}.png':(cfg.online_tile_url||'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png');
    L.tileLayer(tileUrl,{maxZoom:20,subdomains:['a','b','c'],attribution:mode==='offline'?'Offline tiles':'OpenStreetMap'}).addTo(map);
    const bounds=[];
    const ridReceiverIcon=L.icon({iconUrl:'/static/assets/rid-receiver.svg',iconSize:[40,40],iconAnchor:[20,36],popupAnchor:[0,-34]});
    sources.forEach(x=>{const p=[Number(x.lat),Number(x.lng)];bounds.push(p);L.marker(p,{icon:ridReceiverIcon}).addTo(map).bindPopup(`<b>${esc(x.name||x.id)}</b><br>${esc(x.brand||'')}<br>${esc(x.serial_no||x.id||'')}`);});
    targets.forEach(x=>{const p=[Number(x.lat),Number(x.lng)];bounds.push(p);const title=x.model||x.uav_id||'RID Aircraft';L.marker(p).addTo(map).bindPopup(`<b>${esc(title)}</b><br>RID: ${esc(x.uav_id||x.id)}<br>Altitude: ${esc(x.altitude??'-')} m<br>Height: ${esc(x.height??'-')} m<br>Speed: ${esc(x.speed??'-')} m/s<br>Track: ${esc(x.heading??'-')}°<br>Source: ${esc(x.source_name||x.source_id||'')}`);const trail=(x.trail||[]).map(t=>[Number(t.lat),Number(t.lng)]).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));if(trail.length>1){L.polyline(trail).addTo(map);trail.forEach(p=>bounds.push(p));}if(x.pilot_lat!=null&&x.pilot_lng!=null){const q=[Number(x.pilot_lat),Number(x.pilot_lng)];if(Number.isFinite(q[0])&&Number.isFinite(q[1])){bounds.push(q);L.marker(q).addTo(map).bindPopup(`<b>Operator / Pilot</b><br>${esc(x.uav_id||x.id)}`);}}});
    if(bounds.length>1)map.fitBounds(bounds,{padding:[30,30]}); else if(selected)map.setView([Number(selected.lat),Number(selected.lng)],Math.max(Number(cfg.default_zoom||12),16));
    setTimeout(()=>map.invalidateSize(),120);
  }catch(err){el.innerHTML=`<div class="map-fallback"><strong>RID map unavailable.</strong><p>${esc(err.message||err)}</p></div>`;}
}

async function renderMqtt(content) {
  const data = await api("/api/mqtt");
  if (!isActiveContent(content, "MQTT")) return;
  const cfg = settingsCache.settings.modules.mqtt;
  const messages = data.messages || [];
  mqttMessagesCache = messages;
  const latest = messages[0];
  const selected = messages.find(m => String(m.id) === String(selectedMqttId)) || latest;
  selectedMqttId = selected?.id || null;
  const mqttConnected = Boolean(data.available || data.module?.broker_ready || data.module?.subscriber_ready);
  const statusMessages = data.status_messages || [];
  content.innerHTML = `
    <div class="module-header compact-module-header">
      <div><h1>MQTT Client Dashboard</h1></div>
      <div class="toolbar-actions">
        <button class="secondary small-btn" onclick="openMqttRawData()">Raw Data</button>
        <button class="secondary small-btn" onclick="openMqttHistory()">History</button>
        <button class="secondary small-btn" onclick="toggleMqttBrokerSettings()">Settings</button>
      </div>
    </div>
    <div class="compact-status-row mqtt-compact-status">
      <span><small>Status</small><strong>${mqttConnected ? "Connected" : "Waiting"}</strong></span>
      <span><small>Broker</small><strong>${esc(cfg.host || "127.0.0.1")}:${esc(settingsCache.settings.ports.mqtt_broker)}</strong></span>
      <span><small>Client</small><strong>mqtt-dashboard</strong></span>
      <span><small>Messages</small><strong>${esc(data.count)}</strong></span>
      <span><small>Broker / Subscriber</small><strong>${data.module?.broker_ready ? "Ready" : "Starting"} / ${data.module?.subscriber_ready ? "Ready" : "Starting"}</strong></span>
      <span><small>Topic</small><strong>${esc(cfg.topic || "#")}</strong></span>
    </div>
    <div id="mqttBrokerSettings" class="compact-settings-strip" style="display:none">
      <span><small>Host</small><strong>${esc(cfg.host || "")}</strong></span>
      <span><small>Port</small><strong>${esc(settingsCache.settings.ports.mqtt_broker)}</strong></span>
      <span><small>User</small><strong>${esc(cfg.username || "")}</strong></span>
      <span><small>Topic</small><strong>${esc(cfg.topic || "#")}</strong></span>
    </div>
    ${mqttConnected ? "" : sourceNotice(data, "MQTT capture log path")}
    <section class="module-main compact-main">
        <div class="mqtt-four-grid">
          <div class="card mqtt-column-card">
            <div class="card-title-row">
              <h3>Live Raw Message</h3>
              <div class="toolbar-actions">
                <span id="mqttLiveMeta" class="pill">${esc(latest?.payload_type || "RAW")} | ${esc(latest?.bytes || 0)} B</span>
                <button class="secondary small-btn" onclick="clearMqttLive()">Clear</button>
              </div>
            </div>
            <div id="mqttLiveHead" class="mqtt-message-head selected-message-head">
              <strong>${esc(latest?.id || "--")}</strong>
              <strong>${esc(latest?.time || "--")}</strong>
              <strong>${esc(latest?.topic || "No topic")}</strong>
            </div>
            <pre id="mqttLivePayload" class="json-box mqtt-column-box">${esc(latest?.payload || "No live MQTT message received")}</pre>
          </div>
          <div class="card mqtt-column-card">
            <div class="card-title-row"><h3>Last 10 Received Message</h3><span class="pill">${data.count} captured</span></div>
            <table class="table compact-table mqtt-list-table">
              <thead><tr><th>ID</th><th>Time</th><th>Topic</th><th>Bytes</th></tr></thead>
              <tbody>
                ${messages.slice(0, 10).map(m => `<tr class="selectable-row ${String(m.id) === String(selectedMqttId) ? "selected-row" : ""}" data-mqtt-id="${escAttr(m.id)}" onclick="selectMqttMessage('${escAttr(m.id)}')">
                  <td>${esc(m.id || "--")}</td>
                  <td>${esc(m.time || "--")}</td>
                  <td>${esc(m.topic || "No topic")}</td>
                  <td>${esc(m.bytes || 0)} B</td>
                </tr>`).join("") || `<tr><td colspan="4">No MQTT message captured</td></tr>`}
              </tbody>
            </table>
          </div>
          <div class="card mqtt-column-card">
            <div class="card-title-row">
              <h3>Selected Message</h3>
              <div class="toolbar-actions">
                <span id="mqttSelectedMeta" class="pill">${esc(selected?.payload_type || "RAW")} | ${esc(selected?.bytes || 0)} B</span>
                <button class="secondary small-btn" onclick="clearMqttSelected()">Clear</button>
              </div>
            </div>
            <div id="mqttSelectedHead" class="mqtt-message-head selected-message-head">
              <strong>${esc(selected?.id || "--")}</strong>
              <strong>${esc(selected?.time || "--")}</strong>
              <strong>${esc(selected?.topic || "No topic")}</strong>
            </div>
            <pre id="mqttSelectedPayload" class="json-box mqtt-column-box">${esc(selected?.payload || "No selected message")}</pre>
          </div>
          <div class="card mqtt-column-card status-panel">
            <div class="card-title-row"><h3>Message Status</h3><span class="pill">${statusMessages.length || 0} item</span></div>
            <div class="mqtt-status-list">
              ${statusMessages.length ? statusMessages.map(mqttStatusItem).join("") : `<div class="empty-state">${esc(mqttConnected ? "Waiting for readable FH2 telemetry/status messages." : "MQTT is not connected. Check broker, IP, port, username, password, and FH2 bridge status.")}</div>`}
            </div>
          </div>
        </div>
    </section>
  `;
  startMqttRefresh();
}

function toggleMqttBrokerSettings() {
  const box = document.getElementById("mqttBrokerSettings");
  if (box) box.style.display = box.style.display === "none" ? "flex" : "none";
}

function startMqttRefresh() {
  if (mqttRefreshTimer || activePage !== "MQTT") return;
  mqttRefreshTimer = setInterval(() => {
    if (activePage !== "MQTT") {
      clearInterval(mqttRefreshTimer);
      mqttRefreshTimer = null;
      return;
    }
    renderMqtt(document.getElementById("content")).catch(console.error);
  }, 5000);
}

function mqttStatusItem(item) {
  return `
    <div class="mqtt-status-item ${escAttr(item.level || "info")}">
      <strong>${esc(item.title || "Status")}</strong>
      <span>${esc(item.message || "")}</span>
      <small>${esc(formatDubaiTime(item.time))} | ${esc(item.topic || "")}</small>
    </div>
  `;
}

async function selectMqttMessage(id) {
  selectedMqttId = id;
  const selected = mqttMessagesCache.find(m => String(m.id) === String(id));
  if (!selected) return;
  document.querySelectorAll(".mqtt-list-table .selectable-row").forEach(row => {
    row.classList.toggle("selected-row", String(row.dataset.mqttId) === String(id));
  });
  const meta = document.getElementById("mqttSelectedMeta");
  const head = document.getElementById("mqttSelectedHead");
  const payload = document.getElementById("mqttSelectedPayload");
  if (meta) meta.textContent = `${selected.payload_type || "RAW"} | ${selected.bytes || 0} B`;
  if (head) head.innerHTML = `<strong>${esc(selected.id || "--")}</strong><strong>${esc(selected.time || "--")}</strong><strong>${esc(selected.topic || "No topic")}</strong>`;
  if (payload) payload.textContent = selected.payload || "No selected message";
}

function clearMqttLive() {
  const meta = document.getElementById("mqttLiveMeta");
  const head = document.getElementById("mqttLiveHead");
  const payload = document.getElementById("mqttLivePayload");
  if (meta) meta.textContent = "Cleared";
  if (head) head.innerHTML = "<strong>--</strong><strong>--</strong><strong>Waiting for new live message</strong>";
  if (payload) payload.textContent = "Cleared. Waiting for new MQTT message.";
}

function clearMqttSelected() {
  selectedMqttId = null;
  const meta = document.getElementById("mqttSelectedMeta");
  const head = document.getElementById("mqttSelectedHead");
  const payload = document.getElementById("mqttSelectedPayload");
  document.querySelectorAll(".mqtt-list-table .selectable-row").forEach(row => row.classList.remove("selected-row"));
  if (meta) meta.textContent = "Cleared";
  if (head) head.innerHTML = "<strong>--</strong><strong>--</strong><strong>No selected message</strong>";
  if (payload) payload.textContent = "Cleared. Select a received message from the list.";
}

async function openMqttRawData() {
  if (mqttRefreshTimer) { clearInterval(mqttRefreshTimer); mqttRefreshTimer = null; }
  let modal = document.getElementById("mqttRawModal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "mqttRawModal";
    modal.className = "aerosync-modal";
    modal.innerHTML = `<div class="aerosync-modal-panel raw-data-panel live-raw-panel">
      <div class="card-title-row">
        <div><h2>MQTT Raw Data</h2></div>
        <div class="toolbar-actions">
          <button id="mqttRawPauseBtn" class="secondary" onclick="toggleMqttRawPause()">Pause</button>
          <button class="secondary" onclick="copyText(document.getElementById('mqttRawConsole')?.textContent||'')">Copy All</button>
          <button class="secondary" onclick="clearMqttRawView()">Clear View</button>
          <button class="secondary" onclick="openMqttHistory()">History</button>
          <button class="ghost" onclick="closeMqttRawData()">Close</button>
        </div>
      </div>
      <div id="mqttRawLiveStatus" class="muted">Starting live capture...</div>
      <pre id="mqttRawConsole" class="json-box mqtt-live-console">Waiting for new MQTT messages...</pre>
    </div>`;
    document.body.appendChild(modal);
  }
  modal.classList.add("show");
  mqttRawLivePaused = false;
  mqttRawLiveInitialized = false;
  mqttRawLiveSeen = new Set();
  mqttRawLiveLines = [];
  const btn = document.getElementById("mqttRawPauseBtn"); if (btn) btn.textContent = "Pause";
  await pollMqttRawLive();
  if (mqttRawLiveTimer) clearInterval(mqttRawLiveTimer);
  mqttRawLiveTimer = setInterval(pollMqttRawLive, 1000);
}
function closeMqttRawData(){
  document.getElementById("mqttRawModal")?.classList.remove("show");
  if (mqttRawLiveTimer) { clearInterval(mqttRawLiveTimer); mqttRawLiveTimer = null; }
  if(activePage==="MQTT") startMqttRefresh();
}
function mqttRawFingerprint(row){return `${row.id||''}|${row.time||''}|${row.topic||''}|${row.bytes||''}|${row.payload||''}`;}
function mqttRawLine(row){
  const head=`[${row.time||'--'}] ${row.topic||'No topic'} | ${row.payload_type||'RAW'} | ${row.bytes||0} B`;
  return `${head}\n${row.payload||''}`;
}
async function pollMqttRawLive(){
  if (mqttRawLivePaused || !document.getElementById("mqttRawModal")?.classList.contains("show")) return;
  try{
    const data=await api('/api/mqtt?limit=500');
    const rows=(data.messages||[]).slice().reverse();
    if(!mqttRawLiveInitialized){
      rows.forEach(r=>mqttRawLiveSeen.add(mqttRawFingerprint(r)));
      mqttRawLiveInitialized=true;
      const status=document.getElementById('mqttRawLiveStatus'); if(status)status.textContent=`Live capture ready | ${data.count||0} messages stored in history`;
      return;
    }
    let added=0;
    rows.forEach(r=>{
      const fp=mqttRawFingerprint(r); if(mqttRawLiveSeen.has(fp))return;
      mqttRawLiveSeen.add(fp); mqttRawLiveLines.push(mqttRawLine(r)); added++;
    });
    if(mqttRawLiveLines.length>5000)mqttRawLiveLines=mqttRawLiveLines.slice(-5000);
    const box=document.getElementById('mqttRawConsole');
    if(box && added){box.textContent=mqttRawLiveLines.join('\n\n');box.scrollTop=box.scrollHeight;}
    const status=document.getElementById('mqttRawLiveStatus'); if(status)status.textContent=`Live | ${mqttRawLiveLines.length} messages in this view | ${data.count||0} stored in history`;
  }catch(err){const status=document.getElementById('mqttRawLiveStatus');if(status)status.textContent=`Live read error: ${err.message}`;}
}
function toggleMqttRawPause(){
  mqttRawLivePaused=!mqttRawLivePaused;
  const btn=document.getElementById('mqttRawPauseBtn'); if(btn)btn.textContent=mqttRawLivePaused?'Resume':'Pause';
  const status=document.getElementById('mqttRawLiveStatus'); if(status && mqttRawLivePaused)status.textContent='Paused - incoming MQTT continues to be stored in History';
  if(!mqttRawLivePaused)pollMqttRawLive();
}
function clearMqttRawView(){
  mqttRawLiveLines=[];
  const box=document.getElementById('mqttRawConsole'); if(box)box.textContent='View cleared. Waiting for new MQTT messages...';
}

async function openMqttHistory(){
  let modal=document.getElementById('mqttHistoryModal');
  if(!modal){
    modal=document.createElement('div'); modal.id='mqttHistoryModal'; modal.className='aerosync-modal';
    modal.innerHTML=`<div class="aerosync-modal-panel raw-data-panel">
      <div class="card-title-row"><div><h2>MQTT History</h2></div><button class="ghost" onclick="closeMqttHistory()">Close</button></div>
      <div class="raw-filter-grid">
        <label class="field"><span>Search</span><input id="mqttHistorySearch" placeholder="Topic, device/SN, payload value..."></label>
        <label class="field"><span>Topic</span><input id="mqttHistoryTopic" placeholder="camera/01 or thing/product"></label>
        <label class="field"><span>From</span><input id="mqttHistoryFrom" type="date"></label>
        <label class="field"><span>To</span><input id="mqttHistoryTo" type="date"></label>
        <label class="field"><span>Limit</span><select id="mqttHistoryLimit"><option>100</option><option selected>250</option><option>500</option><option>1000</option></select></label>
      </div>
      <div class="toolbar-actions raw-toolbar"><button class="primary" onclick="loadMqttHistory()">Search History</button><button class="secondary" onclick="copyText(document.getElementById('mqttHistoryCopyAll')?.textContent||'')">Copy Results</button></div>
      <div id="mqttHistorySummary" class="muted"></div><div id="mqttHistoryResults" class="raw-results"></div><pre id="mqttHistoryCopyAll" style="display:none"></pre>
    </div>`; document.body.appendChild(modal);
    ['mqttHistorySearch','mqttHistoryTopic'].forEach(id=>document.getElementById(id)?.addEventListener('keydown',e=>{if(e.key==='Enter')loadMqttHistory();}));
  }
  modal.classList.add('show');
}
function closeMqttHistory(){document.getElementById('mqttHistoryModal')?.classList.remove('show');}
async function loadMqttHistory(){
  const p=new URLSearchParams();
  [['q','mqttHistorySearch'],['topic','mqttHistoryTopic'],['from','mqttHistoryFrom'],['to','mqttHistoryTo'],['limit','mqttHistoryLimit']].forEach(([k,id])=>{const v=document.getElementById(id)?.value?.trim();if(v)p.set(k,v);});
  const box=document.getElementById('mqttHistoryResults'),sum=document.getElementById('mqttHistorySummary');if(box)box.innerHTML='<div class="loading-card">Searching stored MQTT log...</div>';
  try{
    const data=await api(`/api/mqtt/raw?${p.toString()}`),rows=data.messages||[];
    if(sum)sum.textContent=`${data.matched||0} matched | ${data.count||0} total stored | source: ${data.source||'--'}`;
    const copy=rows.map(r=>`${r.time||''}\t${r.topic||''}\n${r.payload||''}`).join('\n\n');const all=document.getElementById('mqttHistoryCopyAll');if(all)all.textContent=copy;
    if(box)box.innerHTML=rows.length?rows.map((r,i)=>`<div class="raw-message-card"><div class="card-title-row"><div><strong>${esc(r.topic||'No topic')}</strong><small>${esc(formatDubaiTime(r.time))} | ${esc(r.payload_type||'RAW')} | ${esc(r.bytes||0)} B</small></div><button class="secondary small-btn" onclick="copyText(document.getElementById('mqttHistoryPayload${i}').textContent)">Copy</button></div><pre id="mqttHistoryPayload${i}" class="json-box raw-payload">${esc(r.payload||'')}</pre></div>`).join(''):'<div class="empty-state">No matching MQTT history.</div>';
  }catch(err){if(box)box.innerHTML=`<div class="warn-card">${esc(err.message)}</div>`;}
}

async function renderMedia(content) {
  const data = await api("/api/media");
  if (!isActiveContent(content, "Media / S3")) return;
  const cfg = settingsCache.settings.modules.local_s3;
  content.innerHTML = `
    <div class="module-header">
      <div><h1>Media / S3</h1></div>
      <span class="pill">${data.count} files | ${formatBytes(data.bytes || 0)}</span>
    </div>
    <div class="module-layout">
      <aside class="module-side">
        <div class="metric-card"><span>S3 Status</span><strong>${data.available ? "Ready" : "Waiting"}</strong></div>
        <div class="metric-card"><span>Bucket</span><strong>${esc(cfg.bucket || "aeronex")}</strong></div>
        <div class="metric-card"><span>AK / SK</span><strong>${esc(cfg.access_key || "aeronex")} / ${cfg.secret_key ? "Set" : "Not set"}</strong></div>
        <div class="metric-card"><span>Region</span><strong>${esc(cfg.region || "us-east-1")}</strong></div>
        <div class="metric-card"><span>Storage Used</span><strong>${formatBytes(data.bytes || 0)}</strong></div>
        <div class="metric-card"><span>Total Files</span><strong>${data.count}</strong></div>
      </aside>
      <section class="module-main">
        ${sourceNotice(data, "Local S3 storage path")}
        <div class="card">
          <div class="card-title-row"><h3>Received Files / Folder Manager</h3><span class="pill">${data.count} files</span></div>
          <div class="kv-grid compact-kv">
            <span>Bucket Type</span><strong>${esc(cfg.bucket_type || "Self-Hosted S3 Protocol Storage")}</strong>
            <span>Bucket AK</span><strong>${esc(cfg.access_key || "aeronex")}</strong>
            <span>Bucket SK</span><strong>${cfg.secret_key ? "Configured" : "Not configured"}</strong>
            <span>Endpoint</span><strong>${esc(cfg.endpoint || settingsCache.urls?.local?.s3 || "")}</strong>
            <span>Region</span><strong>${esc(cfg.region || "us-east-1")}</strong>
            <span>Preset Path</span><strong>${esc(cfg.preset_path || "Blank")}</strong>
          </div>
          <table class="table">
            <thead><tr><th>Name</th><th>Size</th><th>Modified</th><th>Path</th></tr></thead>
            <tbody>
              ${(data.files || []).map(f => `<tr>
                <td>${esc(f.name)}</td><td>${formatBytes(f.size)}</td><td>${esc(f.modified)}</td><td>${esc(f.path)}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  `;
  startMediaRefresh();
}

function startMediaRefresh() {
  if (mediaRefreshTimer || activePage !== "Media / S3") return;
  mediaRefreshTimer = setInterval(() => {
    if (activePage !== "Media / S3") {
      clearInterval(mediaRefreshTimer);
      mediaRefreshTimer = null;
      return;
    }
    renderMedia(document.getElementById("content")).catch(console.error);
  }, 5000);
}

async function renderLiveMap(content) {
  const [data,ridData]=await Promise.all([api("/api/map"),api("/api/rid").catch(()=>({targets:[]}))]); if(!isActiveContent(content,"Live Map"))return;
  const cfg=data.settings||{}, devices=data.devices||[], onlineDevices=data.online_devices||devices;
  const onlineCount=data.online_count??onlineDevices.length, visibleCount=devices.length;
  const ridTargets=(ridData.targets||[]).filter(x=>String(x.status||'live').toLowerCase()==='live');
  content.innerHTML=`
    <div class="module-header map-header"><div><h1>Live Map</h1></div><div class="toolbar-actions"><span class="pill">${esc(cfg.mode||'online').toUpperCase()}</span>${hasPermission('settings')?'<button class="secondary small-btn" onclick="openMapSettings()">Map Settings</button>':''}</div></div>
    <div class="map-kpi-grid compact-map-kpis">
      <div class="metric-card devices-online-card"><span>Devices Online</span><strong>${esc(onlineCount)}</strong><small>Dock ${esc(data.dock_count||0)} | Drone ${esc(data.drone_count||0)} | O4 GS ${esc(data.o4_count||0)} | RID ${esc(data.rid_count||0)}</small></div>
      <div class="metric-card"><span>On Map</span><strong>${esc(visibleCount)}</strong></div>
    </div>
    <section class="map-workspace card map-workspace-full"><div class="map-toolbar"><div><strong>Device Position Map</strong></div><div class="toolbar-actions"><button class="secondary small-btn" onclick="openMapHistory()">History</button><button class="secondary small-btn" onclick="refreshLiveMap()">Refresh</button><button class="secondary small-btn" onclick="fitMapDevices()">Fit Devices</button></div></div><div class="map-stage"><div id="mapCanvas" class="map-canvas"><div class="map-loading">Loading map...</div></div><div id="ridMapAlerts" class="rid-map-alerts">${ridMapAlertsHtml(ridTargets)}</div></div></section>`;
  await initMap(data); updateRidMapTargets(ridTargets); const seconds=Math.max(2,Number(cfg.refresh_seconds||5)); mapRefreshTimer=setInterval(refreshLiveMap,seconds*1000);
}

function ridMapAlertsHtml(targets){
  return (targets||[]).slice(0,5).map(x=>{const hasPos=Number.isFinite(Number(x.lat??x.latitude))&&Number.isFinite(Number(x.lng??x.longitude));return `<button class="rid-map-alert" onclick="focusRidTarget('${escAttr(x.id||x.uav_id||x.track_id||'')}','${escAttr(x.track_id||x.trace_id||'')}')"><div><strong>${esc(x.model||x.uav_id||'RID Aircraft')}</strong><span>${esc(x.uav_id||x.id||'')}</span></div><small>${hasPos?`${esc(x.altitude??'-')} m | ${esc(x.speed??'-')} m/s | ${esc(x.heading??x.azimuth??'-')}°`:'Location pending'}${x.frequency?` | ${esc(x.frequency)}`:''}${x.rssi!=null?` | RSSI ${esc(x.rssi)}`:''}</small><em>${esc(formatDubaiTime(x.last_seen||x.recorded_at))}</em></button>`;}).join('');
}

function updateRidMapTargets(targets){
  window._ridMapTargets={};
  const seen=new Set();
  (targets||[]).forEach(x=>{
    const lat=Number(x.lat??x.latitude), lng=Number(x.lng??x.longitude); if(!Number.isFinite(lat)||!Number.isFinite(lng)||!mapInstance||!window.L)return;
    const id=String(x.id||x.uav_id||x.track_id||`${lat},${lng}`), key=`rid:${id}`; seen.add(key); window._ridMapTargets[id]=x;
    const icon=L.divIcon({className:'map-pin-icon',html:`<div class="map-pin drone online rid-target-pin"><span></span></div>`,iconSize:[30,30],iconAnchor:[15,15]});
    const popup=`<strong>${esc(x.model||x.uav_id||'RID Aircraft')}</strong><br>RID/UAV ID: ${esc(x.uav_id||x.id||'--')}<br>Altitude: ${esc(x.altitude??'-')} m<br>Speed: ${esc(x.speed??'-')} m/s<br>Heading: ${esc(x.heading??x.azimuth??'-')}°<br>Source: ${esc(x.source_name||x.source_id||'--')}`;
    if(!mapMarkers[key]) mapMarkers[key]=L.marker([lat,lng],{icon}).addTo(mapInstance); else mapMarkers[key].setLatLng([lat,lng]);
    mapMarkers[key].bindPopup(popup);
  });
  Object.keys(mapMarkers).filter(k=>k.startsWith('rid:')&&!seen.has(k)).forEach(k=>{mapInstance?.removeLayer(mapMarkers[k]);delete mapMarkers[k]});
}

function focusRidTarget(id,trackId=''){
  const target=(window._ridMapTargets||{})[String(id)]; const marker=mapMarkers[`rid:${id}`];
  if(marker&&mapInstance){mapInstance.setView(marker.getLatLng(),Math.max(mapInstance.getZoom(),16));marker.openPopup();}
  if(!target)return;
  let modal=document.getElementById('ridAircraftDetailModal'); if(!modal){modal=document.createElement('div');modal.id='ridAircraftDetailModal';modal.className='aerosync-modal';document.body.appendChild(modal);} modal.classList.add('show');
  const fields=[['RID / UAV ID',target.uav_id||target.id],['Model',target.model],['Track ID',target.track_id||target.trace_id],['Latitude',target.lat??target.latitude],['Longitude',target.lng??target.longitude],['Altitude',target.altitude],['Real Height',target.height],['Speed',target.speed],['Heading / Track Angle',target.heading??target.azimuth],['Frequency',target.frequency],['Source',target.source_name||target.source_id],['Last Seen',formatDubaiTime(target.last_seen||target.recorded_at)],['Pilot Lat',target.pilot_lat],['Pilot Lon',target.pilot_lng],['Home Lat',target.home_lat],['Home Lon',target.home_lng],['Distance',target.distance],['RSSI',target.rssi],['SNR',target.snr],['Confidence',target.confidence]];
  modal.innerHTML=`<div class="aerosync-modal-panel rid-aircraft-detail-modal"><div class="card-title-row"><div><h2>${esc(target.model||'RID Aircraft')}</h2><small>${esc(target.uav_id||target.id||'')}</small></div><button class="secondary" onclick="document.getElementById('ridAircraftDetailModal')?.classList.remove('show')">Close</button></div><div class="rid-track-detail-grid">${fields.map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v??'-')}</strong></div>`).join('')}</div><div class="toolbar-actions">${trackId?`<button class="primary" onclick="document.getElementById('ridAircraftDetailModal')?.classList.remove('show');openRidTrack('${escAttr(trackId)}')">Track Details / History</button>`:''}</div></div>`;
}

function openMapSettings(){
  const cfg=settingsCache.settings.modules.map||{}; let modal=document.getElementById('mapSettingsModal'); if(!modal){modal=document.createElement('div');modal.id='mapSettingsModal';modal.className='aerosync-modal';document.body.appendChild(modal);} modal.classList.add('show');
  modal.innerHTML=`<div class="aerosync-modal-panel map-settings-modal"><div class="card-title-row"><h2>Map Settings</h2><button class="secondary" onclick="document.getElementById('mapSettingsModal')?.classList.remove('show')">Close</button></div><label class="field"><span>Map Mode</span><select id="mapMode"><option value="online" ${cfg.mode!=='offline'?'selected':''}>Online</option><option value="offline" ${cfg.mode==='offline'?'selected':''}>Offline</option></select></label><label class="field"><span>Online Tile URL</span><input id="mapOnlineTileUrl" value="${esc(cfg.online_tile_url||'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')}"></label><label class="field"><span>Tile Folder Path</span><input id="mapOfflineTilePath" value="${esc(cfg.offline_tile_path||'')}"></label><div class="toolbar-actions"><button class="secondary" onclick="document.getElementById('mapTileZip').click()">Upload Tiles</button><input id="mapTileZip" type="file" accept=".zip" style="display:none" onchange="uploadMapTiles(this)"></div><div class="form-grid map-settings-grid"><label class="field"><span>Default Lat</span><input id="mapDefaultLat" type="number" step="0.000001" value="${esc(cfg.default_lat??25.2048)}"></label><label class="field"><span>Default Lng</span><input id="mapDefaultLng" type="number" step="0.000001" value="${esc(cfg.default_lng??55.2708)}"></label><label class="field"><span>Zoom</span><input id="mapDefaultZoom" type="number" value="${esc(cfg.default_zoom||12)}"></label><label class="field"><span>Refresh Seconds</span><input id="mapRefreshSeconds" type="number" value="${esc(cfg.refresh_seconds||5)}"></label></div><div class="toolbar-actions"><button class="primary" onclick="saveMapSettings()">Save</button></div></div>`;
}

function deviceListItem(d) {
  const hasGps = Number.isFinite(Number(d.lat)) && Number.isFinite(Number(d.lng));
  const kind = d.kind || "device";
  const status = String(d.status || "").toLowerCase();
  const isOnline = status === "online";
  const age = Number(d.age_seconds || 0);
  const statusText = status === "disconnected" ? "Disconnected" : isOnline ? "Online" : "Offline";
  const ageText = age ? ` | Age: ${age}s` : "";
  return `
    <button class="map-device-item ${hasGps ? "" : "no-gps"} ${escAttr(status || "unknown")}" onclick="focusMapDevice('${escAttr(d.sn)}')">
      <span class="map-kind-icon ${escAttr(kind)} ${escAttr(status || "unknown")}"></span>
      <span>
        <strong>${esc(d.name || d.sn || "Device")}</strong>
        <small>${esc(kind.toUpperCase())} | SN: ${esc(d.sn || "--")}</small>
        <small>${statusText} / ${hasGps ? "GPS ready" : "No GPS"} | Last: ${esc(formatDubaiTime(d.last_seen))}${esc(ageText)}</small>
      </span>
      <span class="map-device-meta">
        <strong>${d.battery == null ? "--" : `${esc(Math.round(d.battery))}%`}</strong>
        <small>${d.altitude == null ? "--" : `${esc(Math.round(d.altitude))} m`}</small>
      </span>
    </button>
  `;
}

async function renderNvrSync(content) {
  if (nvrRefreshTimer) {
    clearInterval(nvrRefreshTimer);
    nvrRefreshTimer = null;
  }
  await loadSettings();
  const data = await api("/api/nvr-sync");
  if (!isActiveContent(content, "NVR Sync")) return;
  const cfg = settingsCache.settings.modules.nvr_sync || {};
  const nvrs = cfg.nvrs || [];
  const mappings = data.mappings || [];
  const logs = data.sync_log || [];
  content.innerHTML = `
    <div class="module-header">
      <div><h1>NVR Sync</h1></div>
      <span class="pill">${esc(data.used_channels || 0)} used | ${esc(data.free_channels || 0)} free | SDK ${data.sdk?.available ? "found" : "missing"}</span>
    </div>
    <div class="module-layout">
      <aside class="module-side">
        <div class="metric-card"><span>Sync Status</span><strong>${data.enabled ? "Enabled" : "Disabled"}</strong></div>
        <div class="metric-card"><span>NVR Servers</span><strong>${esc((data.servers || []).length)}</strong></div>
        <div class="metric-card"><span>Total Channels</span><strong>${esc(data.total_channels || 0)}</strong></div>
        <div class="metric-card"><span>Auto Assign</span><strong>${data.auto_assign ? "On" : "Off"}</strong></div>
        <div class="metric-card"><span>SDK</span><strong>${esc(data.sdk_status || "Pending")}</strong></div>
      </aside>
      <section class="module-main">
        <div class="nvr-grid">
          <div class="card nvr-config-card">
            <div class="card-title-row">
              <h3>NVR Servers</h3>
              <div class="toolbar-actions">
                <button class="secondary small-btn" onclick="addNvrRow()">Add</button>
                <button class="primary small-btn" onclick="saveNvrSync()">Save</button>
              </div>
            </div>
            <div class="form-grid nvr-mode-grid">
              <label class="field"><span>NVR Sync</span><select id="nvrSyncEnabled"><option value="false" ${!cfg.enabled ? "selected" : ""}>Disabled</option><option value="true" ${cfg.enabled ? "selected" : ""}>Enabled</option></select></label>
              <label class="field"><span>Auto Assign</span><select id="nvrAutoAssign"><option value="true" ${cfg.auto_assign !== false ? "selected" : ""}>Enabled</option><option value="false" ${cfg.auto_assign === false ? "selected" : ""}>Disabled</option></select></label>
            </div>
            <div id="nvrServerRows" class="nvr-server-list">
              ${nvrs.length ? nvrs.map((nvr, index) => nvrServerRow(nvr, index)).join("") : nvrServerRow({}, 0)}
            </div>
          </div>
          <div class="card nvr-capacity-card">
            <div class="card-title-row">
              <h3>NVR Channel Capacity</h3>
              <div class="toolbar-actions">
                <span class="pill">${esc(data.free_channels || 0)} free</span>
                <button class="secondary small-btn" onclick="refreshNvrStatus(false)">Check All</button>
                <button class="danger small-btn" onclick="clearAeroSyncNvrChannels()">Clear AERO SYNC Channels</button>
              </div>
            </div>
            <table class="table compact-table">
              <thead><tr><th>NVR</th><th>IP</th><th>SDK Port</th><th>Used</th><th>Free</th><th>Status</th><th>NVR Reply</th><th>Action</th></tr></thead>
              <tbody>
                ${(data.servers || []).map(nvr => `<tr>
                  <td>${esc(nvr.name)}</td>
                  <td>${esc(nvr.host || "--")}</td>
                  <td>${esc(nvr.sdk_port || 8000)}</td>
                  <td>${esc(nvr.used_channels || 0)} / ${esc(nvr.max_channels || 0)}</td>
                  <td>${esc(nvr.free_channels || 0)}</td>
                  <td><span class="status-badge ${esc(nvr.status || "configured")}">${esc(nvr.status || "--")}</span></td>
                  <td>${esc(cleanSdkText(nvr.online_message || "--"))}<br><span class="muted">${nvr.last_sdk_error_code == null ? "" : `SDK Code: ${esc(nvr.last_sdk_error_code)}`} ${nvr.last_checked_at ? `| ${esc(formatDubaiTime(nvr.last_checked_at))}` : ""}</span></td>
                  <td><button class="secondary small-btn" onclick="checkNvr('${esc(nvr.id || "")}')">Check</button></td>
                </tr>`).join("") || `<tr><td colspan="8">No NVR configured.</td></tr>`}
              </tbody>
            </table>
            <div class="muted small-note">SDK status is checked locally.</div>
          </div>
          <div class="card nvr-wide-card">
            <div class="card-title-row"><h3>DJI To NVR Mapping</h3><span class="pill">${esc(mappings.length)} mapped</span></div>
            <table class="table compact-table">
              <thead><tr><th>DJI Source</th><th>NVR</th><th>Channel</th><th>Stream Details</th><th>Status</th><th>Last Sync</th></tr></thead>
              <tbody>
                ${mappings.map(m => `<tr>
                  <td><strong>${esc(m.device_name || m.device_sn || "--")}</strong><br><span class="muted">${esc(m.device_sn || "--")} | ${esc(m.camera_index || "--")}</span></td>
                  <td>${esc(m.nvr_name || m.nvr_id || "--")}</td>
                  <td>${esc(m.nvr_channel || "--")}</td>
                  <td>${esc(formatStreamDetails(m.stream_info))}<br><span class="muted">${m.stream_info?.checked_at ? `Checked: ${esc(m.stream_info.checked_at)}` : `Path: ${esc(m.stream_path || "--")}`}</span></td>
                  <td>${esc(m.status || "--")}<br><span class="muted">${esc(m.message || "")}${m.last_sdk_error_code == null ? "" : ` | SDK Code: ${esc(m.last_sdk_error_code)}`}</span></td>
                  <td>${esc(formatDubaiTime(m.last_sdk_sync_at || m.last_sync_at))}</td>
                </tr>`).join("") || `<tr><td colspan="6">No DJI RTSP stream mapped yet. Waiting for live_rtsp_start.</td></tr>`}
              </tbody>
            </table>
          </div>
          <div class="card nvr-wide-card">
            <div class="card-title-row"><h3>Sync Log</h3><span class="pill">${esc(logs.length)} entries</span></div>
            <div class="mini-list nvr-sync-log">
              ${logs.length ? logs.slice(0, 20).map(item => `<div><strong>${esc(item.level || "info")}</strong> ${esc(formatDubaiTime(item.time))} | ${esc(item.message || "")}</div>`).join("") : `<div>No NVR sync log yet.</div>`}
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
  nvrRefreshTimer = setInterval(() => refreshNvrStatus(true), 30000);
}

function nvrServerRow(nvr = {}, index = 0) {
  return `
    <div class="nvr-server-row" data-nvr-row>
      <div class="card-title-row nvr-row-title">
        <h3>${esc(nvr.name || `NVR ${index + 1}`)}</h3>
        <button class="ghost small-btn" onclick="this.closest('[data-nvr-row]').remove()">Remove</button>
      </div>
      <div class="nvr-server-fields">
        <label class="field"><span>Name</span><input data-nvr-name value="${esc(nvr.name || `NVR ${index + 1}`)}"></label>
        <label class="field"><span>NVR IP</span><input data-nvr-host value="${esc(nvr.host || "")}" placeholder="192.168.120.xxx"></label>
        <label class="field"><span>Username</span><input data-nvr-username value="${esc(nvr.username || "")}"></label>
        <label class="field"><span>Password</span><input data-nvr-password type="password" value="${esc(nvr.password || "")}"></label>
        <label class="field"><span>SDK Port</span><input data-nvr-sdk-port type="number" min="1" max="65535" value="${esc(nvr.sdk_port || 8000)}"></label>
        <label class="field"><span>Web Port</span><input data-nvr-web-port type="number" min="1" max="65535" value="${esc(nvr.web_port || 80)}"></label>
        <label class="field"><span>Channels</span><input data-nvr-max-channels type="number" min="1" value="${esc(nvr.max_channels || 32)}"></label>
        <label class="field"><span>Priority</span><input data-nvr-priority type="number" min="1" value="${esc(nvr.priority || index + 1)}"></label>
        <label class="field"><span>Status</span><select data-nvr-enabled><option value="true" ${nvr.enabled !== false ? "selected" : ""}>Enabled</option><option value="false" ${nvr.enabled === false ? "selected" : ""}>Disabled</option></select></label>
      </div>
      <input data-nvr-id type="hidden" value="${esc(nvr.id || `nvr_${Date.now()}_${index}`)}">
    </div>
  `;
}

function addNvrRow() {
  const list = document.getElementById("nvrServerRows");
  const index = document.querySelectorAll("[data-nvr-row]").length;
  list?.insertAdjacentHTML("beforeend", nvrServerRow({}, index));
}

function collectNvrs() {
  return Array.from(document.querySelectorAll("[data-nvr-row]")).map((row, index) => ({
    id: row.querySelector("[data-nvr-id]")?.value || `nvr_${index + 1}`,
    name: row.querySelector("[data-nvr-name]")?.value || `NVR ${index + 1}`,
    enabled: row.querySelector("[data-nvr-enabled]")?.value !== "false",
    host: row.querySelector("[data-nvr-host]")?.value || "",
    sdk_port: Number(row.querySelector("[data-nvr-sdk-port]")?.value || 8000),
    web_port: Number(row.querySelector("[data-nvr-web-port]")?.value || 80),
    username: row.querySelector("[data-nvr-username]")?.value || "",
    password: row.querySelector("[data-nvr-password]")?.value || "",
    max_channels: Number(row.querySelector("[data-nvr-max-channels]")?.value || 32),
    priority: Number(row.querySelector("[data-nvr-priority]")?.value || index + 1)
  })).filter(nvr => nvr.host || nvr.username);
}

async function saveNvrSync() {
  const current = settingsCache.settings.modules.nvr_sync || {};
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        settings: {
          modules: {
            nvr_sync: {
              ...current,
              enabled: document.getElementById("nvrSyncEnabled")?.value === "true",
              auto_assign: document.getElementById("nvrAutoAssign")?.value !== "false",
              nvrs: collectNvrs()
            }
          }
        }
      })
    });
    settingsCache = {settings: data.settings, urls: data.urls};
    await renderNvrSync(document.getElementById("content"));
    showToast("NVR Sync settings saved");
  } catch (err) {
    showToast(err.message || "NVR Sync save failed", "error");
  }
}

async function checkNvr(id) {
  try {
    const data = await api("/api/nvr-sync/check", {
      method: "POST",
      body: JSON.stringify({id})
    });
    await loadSettings();
    await renderNvrSync(document.getElementById("content"));
    const result = data.result || {};
    showToast(`NVR check: ${result.status || "unknown"} - ${result.message || ""}`, result.status === "online" ? "success" : "error");
  } catch (err) {
    showToast(err.message || "NVR check failed", "error");
  }
}

function isEditingNvrSettings() {
  const panel = document.getElementById("nvrServerRows");
  return Boolean(panel && panel.contains(document.activeElement));
}

async function refreshNvrStatus(silent = true) {
  if (activePage !== "NVR Sync") return;
  if (silent && isEditingNvrSettings()) return;
  try {
    const data = await api("/api/nvr-sync/check-all", {method: "POST"});
    await loadSettings();
    if (activePage === "NVR Sync" && !isEditingNvrSettings()) {
      await renderNvrSync(document.getElementById("content"));
    }
    if (!silent) {
      const offline = (data.results || []).filter(r => r.status !== "online").length;
      showToast(offline ? `NVR check complete: ${offline} issue found` : "NVR check complete: all online", offline ? "error" : "success");
    }
  } catch (err) {
    if (!silent) showToast(err.message || "NVR check failed", "error");
  }
}

async function clearAeroSyncNvrChannels() {
  if (!confirm("This will clear only NVR channels mapped by AERO SYNC and remove those AERO SYNC mappings. Continue?")) {
    return;
  }
  try {
    const data = await api("/api/nvr-sync/clear-aero-sync-channels", {
      method: "POST",
      body: JSON.stringify({confirm: true})
    });
    await loadSettings();
    if (activePage === "NVR Sync") {
      await renderNvrSync(document.getElementById("content"));
    }
    showToast(data.message || "AERO SYNC NVR channels cleared", data.ok ? "success" : "error");
  } catch (err) {
    if (String(err.message || "").includes("404")) {
      showToast("Clear channel API not loaded yet. Restart AERO SYNC and try again.", "error");
      return;
    }
    showToast(err.message || "Clear AERO SYNC channels failed", "error");
  }
}

function loadLeaflet() {
  if (window.L) return Promise.resolve();
  if (loadLeaflet.promise) return loadLeaflet.promise;
  loadLeaflet.promise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "/static/leaflet.css";
    document.head.appendChild(css);
    const script = document.createElement("script");
    script.src = "/static/leaflet.js";
    script.onload = resolve;
    script.onerror = () => reject(new Error("Leaflet map library could not load. Check the portable app static vendor folder."));
    document.head.appendChild(script);
  });
  return loadLeaflet.promise;
}

async function initMap(data) {
  const canvas = document.getElementById("mapCanvas");
  try {
    await loadLeaflet();
    if (!canvas || !window.L) return;
    const cfg = data.settings || {};
    const center = firstDeviceLatLng(data.devices) || [Number(cfg.default_lat || 25.2048), Number(cfg.default_lng || 55.2708)];
    mapInstance = L.map(canvas, {zoomControl: true, attributionControl: true}).setView(center, Number(cfg.default_zoom || 12));
    setMapTileLayer(cfg);
    updateMapMarkers(data.devices || []);
    setTimeout(() => mapInstance?.invalidateSize(), 100);
  } catch (err) {
    if (canvas) {
      canvas.innerHTML = `<div class="map-fallback"><strong>Map canvas unavailable.</strong><p>${esc(err.message)}</p><p class="muted">Device list and settings still work. Online map library can be bundled locally in the final installer.</p></div>`;
    }
  }
}

function setMapTileLayer(cfg) {
  if (!mapInstance || !window.L) return;
  if (mapTileLayer) mapInstance.removeLayer(mapTileLayer);
  const mode = cfg.mode === "offline" ? "offline" : "online";
  const url = mode === "offline" ? "/map/tiles/{z}/{x}/{y}.png" : (cfg.online_tile_url || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png");
  mapTileLayer = L.tileLayer(url, {
    maxZoom: 20,
    subdomains: ["a", "b", "c"],
    attribution: mode === "offline" ? "Offline tiles" : "OpenStreetMap"
  }).addTo(mapInstance);
}

function firstDeviceLatLng(devices) {
  const d = (devices || []).find(x => Number.isFinite(Number(x.lat)) && Number.isFinite(Number(x.lng)));
  return d ? [Number(d.lat), Number(d.lng)] : null;
}

function updateMapMarkers(devices) {
  if (!mapInstance || !window.L) return;
  const seen = new Set();
  (devices || []).forEach(d => {
    if (!Number.isFinite(Number(d.lat)) || !Number.isFinite(Number(d.lng))) return;
    const key = d.sn || d.id || `${d.lat},${d.lng}`;
    seen.add(key);
    const kind = d.kind === "drone" ? "drone" : d.kind === "dock" ? "dock" : d.kind === "rid" ? "rid" : "device";
    const onlineClass = String(d.status || "").toLowerCase() || "unknown";
    const heading = Number(d.heading || 0);
    const html = kind === "drone"
      ? `<div class="map-pin drone ${onlineClass}" style="--heading:${heading}deg"><span></span></div>`
      : `<div class="map-pin ${kind} ${onlineClass}"><span></span></div>`;
    const icon = kind === "rid"
      ? L.icon({iconUrl:'/static/assets/rid-receiver.svg',iconSize:[40,40],iconAnchor:[20,36],popupAnchor:[0,-34]})
      : L.divIcon({className: "map-pin-icon", html, iconSize: [28, 28], iconAnchor: [14, 14]});
    const popup = `<strong>${esc(d.name || d.sn || "Device")}</strong><br>Type: ${esc(kind)}<br>SN: ${esc(d.sn || "--")}<br>Last: ${esc(formatDubaiTime(d.last_seen))}<br>Battery: ${d.battery == null ? "--" : `${esc(Math.round(d.battery))}%`}<br>Altitude: ${d.altitude == null ? "--" : `${esc(Math.round(d.altitude))} m`}<br><button class="map-popup-action" onclick="goModule('Live Streams')">Live Stream</button>`;
    if (!mapMarkers[key]) {
      mapMarkers[key] = L.marker([Number(d.lat), Number(d.lng)], {icon}).addTo(mapInstance);
    } else {
      mapMarkers[key].setLatLng([Number(d.lat), Number(d.lng)]);
    }
    mapMarkers[key].bindPopup(popup);
  });
  Object.keys(mapMarkers).forEach(key => {
    const base = key.replace(":trail", "").replace(":history:", ":").split(":history:")[0];
    if (!seen.has(base)) {
      mapInstance.removeLayer(mapMarkers[key]);
      delete mapMarkers[key];
    }
  });
}

async function refreshLiveMap() {
  if (activePage !== "Live Map") return;
  const [data,ridData]=await Promise.all([api("/api/map"),api("/api/rid").catch(()=>({targets:[]}))]);
  const list = document.getElementById("mapDeviceList");
  const onlineDevices = data.online_devices || data.devices || [];
  if (list) list.innerHTML = onlineDevices.length ? onlineDevices.map(deviceListItem).join("") : `<div class="empty-state">No device data yet. Waiting for MQTT status or GPS payloads.</div>`;
  updateMapMarkers(data.devices || []);
  const ridTargets=(ridData.targets||[]).filter(x=>Number.isFinite(Number(x.lat??x.latitude))&&Number.isFinite(Number(x.lng??x.longitude)));
  updateRidMapTargets(ridTargets);
  const alertBox=document.getElementById('ridMapAlerts'); if(alertBox)alertBox.innerHTML=ridMapAlertsHtml(ridTargets);
}

function focusMapDevice(sn) {
  const marker = mapMarkers[sn];
  if (marker && mapInstance) {
    selectedMapDeviceSn = sn;
    mapInstance.setView(marker.getLatLng(), Math.max(mapInstance.getZoom(), 15));
    marker.openPopup();
  } else {
    showToast("Device is online but no valid GPS point is available yet", "error");
  }
}

async function showMapDeviceHistory(sn) {
  selectedMapDeviceSn = sn;
  const data = await api("/api/map");
  const device = (data.devices || []).find(d => String(d.sn || d.id || `${d.lat},${d.lng}`) === String(sn));
  if (!device) return showToast("No online GPS history available for this device", "error");
  drawMapDeviceHistory(device);
  renderMapHistoryList(device);
}

function clearMapHistoryLayers() {
  Object.keys(mapMarkers).forEach(key => {
    if (key.includes(":history") || key.endsWith(":trail")) {
      mapInstance?.removeLayer(mapMarkers[key]);
      delete mapMarkers[key];
    }
  });
}

function drawMapDeviceHistory(device) {
  if (!mapInstance || !window.L || !device) return;
  clearMapHistoryLayers();
  const key = device.sn || device.id || `${device.lat},${device.lng}`;
  const trail = (device.trail || []).filter(p => Number.isFinite(Number(p.lat)) && Number.isFinite(Number(p.lng))).slice(0, 50);
  if (trail.length > 1) {
    mapMarkers[`${key}:trail`] = L.polyline(trail.map(p => [Number(p.lat), Number(p.lng)]), {color: "#6ba6ff", weight: 3, opacity: 0.82}).addTo(mapInstance);
  }
  trail.slice(1).forEach((p, index) => {
    mapMarkers[`${key}:history:${index}`] = L.circleMarker([Number(p.lat), Number(p.lng)], {
      radius: 4,
      color: "#9dc5ff",
      fillColor: "#5f8cff",
      fillOpacity: 0.72,
      weight: 1
    }).addTo(mapInstance).bindPopup(`<strong>${esc(device.name || key)}</strong><br>History ${index + 1}<br>${esc(formatDubaiTime(p.time))}<br>${esc(Number(p.lat).toFixed(6))}, ${esc(Number(p.lng).toFixed(6))}`);
  });
}

function renderMapHistoryList(device) {
  const list = document.getElementById("mapHistoryList");
  if (!list) return;
  const trail = (device?.trail || []).slice(0, 50);
  list.innerHTML = trail.length ? trail.map((p, index) => `
    <button class="map-history-item" onclick="focusMapHistoryPoint('${escAttr(device.sn || device.id)}', ${esc(Number(p.lat))}, ${esc(Number(p.lng))})">
      <strong>${index === 0 ? "Latest" : `Point ${index + 1}`}</strong>
      <span>${esc(formatDubaiTime(p.time))}</span>
      <small>${esc(Number(p.lat).toFixed(6))}, ${esc(Number(p.lng).toFixed(6))}</small>
    </button>
  `).join("") : `<div class="empty-state">No GPS history for selected device.</div>`;
}

function focusMapHistoryPoint(sn, lat, lng) {
  if (!mapInstance || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lng))) return;
  selectedMapDeviceSn = sn;
  mapInstance.setView([Number(lat), Number(lng)], Math.max(mapInstance.getZoom(), 16));
}

function fitMapDevices() {
  if (!mapInstance) return;
  const markers = Object.entries(mapMarkers).filter(([key]) => !key.includes(":history") && !key.endsWith(":trail")).map(([, marker]) => marker);
  if (!markers.length) return showToast("No map devices to fit", "error");
  const group = L.featureGroup(markers);
  mapInstance.fitBounds(group.getBounds().pad(0.2));
}

let mapHistoryCache = null;
async function openMapHistory(){
  let modal=document.getElementById("mapHistoryModal");
  if(!modal){modal=document.createElement("div");modal.id="mapHistoryModal";modal.className="aerosync-modal";modal.innerHTML=`<div class="aerosync-modal-panel map-history-panel"><div class="card-title-row"><div><h2>Map History</h2></div><button class="ghost" onclick="closeMapHistory()">Close</button></div><div class="raw-filter-grid"><label class="field"><span>Device / SN</span><input id="mapHistoryDevice" placeholder="Serial number or name"></label><label class="field"><span>Search</span><input id="mapHistorySearch" placeholder="Name, type or topic"></label><label class="field"><span>From</span><input id="mapHistoryFrom" type="date"></label><label class="field"><span>To</span><input id="mapHistoryTo" type="date"></label></div><div class="toolbar-actions raw-toolbar"><button class="primary" onclick="loadMapHistory()">Search History</button></div><div id="mapHistoryResults" class="map-history-results"></div></div>`;document.body.appendChild(modal);}
  modal.classList.add("show");await loadMapHistory();
}
function closeMapHistory(){document.getElementById("mapHistoryModal")?.classList.remove("show");}
async function loadMapHistory(){
  const p=new URLSearchParams();[["device","mapHistoryDevice"],["q","mapHistorySearch"],["from","mapHistoryFrom"],["to","mapHistoryTo"]].forEach(([k,id])=>{const v=document.getElementById(id)?.value?.trim();if(v)p.set(k,v);});p.set("limit","3000");
  const box=document.getElementById("mapHistoryResults");if(box)box.innerHTML='<div class="loading-card">Loading map history...</div>';
  try{const data=await api(`/api/map/history?${p.toString()}`);mapHistoryCache=data;const devices=data.devices||[];if(box)box.innerHTML=devices.length?devices.map((d,i)=>`<div class="card map-history-summary"><div class="card-title-row"><div><h3>${esc(d.name||d.sn||'Device')}</h3><small>${esc(d.sn||'')} | ${esc(d.kind||'device')}</small></div><span class="pill">${esc(d.point_count||0)} points</span></div><div class="kv-grid compact-kv"><span>Start</span><strong>${esc(formatDubaiTime(d.start_time))}</strong><span>End</span><strong>${esc(formatDubaiTime(d.end_time))}</strong><span>Distance</span><strong>${esc((Number(d.distance_m||0)/1000).toFixed(2))} km</strong><span>Duration</span><strong>${esc(formatHistoryDuration(d.duration_seconds||0))}</strong></div><div class="toolbar-actions"><button class="primary small-btn" onclick="viewMapHistoryTrack(${i})">View Track on Map</button><button class="secondary small-btn" onclick="showMapHistorySnapshot(${i})">Saved Map Snapshot</button><button class="secondary small-btn" onclick="copyText(JSON.stringify(mapHistoryCache.devices[${i}],null,2))">Copy Data</button></div></div>`).join(''):'<div class="empty-state">No historical GPS points match this search.</div>';}catch(err){if(box)box.innerHTML=`<div class="warn-card">${esc(err.message)}</div>`;}
}
function formatHistoryDuration(sec){sec=Number(sec||0);const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=Math.floor(sec%60);return h?`${h}h ${m}m`:`${m}m ${s}s`;}
function viewMapHistoryTrack(index){const d=mapHistoryCache?.devices?.[index];if(!d||!mapInstance)return;clearMapHistoryLayers();const pts=(d.points||[]).filter(p=>Number.isFinite(Number(p.lat))&&Number.isFinite(Number(p.lng)));if(!pts.length)return;const key=d.sn||d.name||'history';mapMarkers[`${key}:trail`]=L.polyline(pts.map(p=>[Number(p.lat),Number(p.lng)]),{color:'#6ba6ff',weight:4,opacity:.9}).addTo(mapInstance);pts.forEach((p,i)=>{if(i===0||i===pts.length-1)mapMarkers[`${key}:history:${i}`]=L.circleMarker([Number(p.lat),Number(p.lng)],{radius:7,color:i===0?'#22c55e':'#ef4444',fillOpacity:.9}).addTo(mapInstance).bindPopup(`${i===0?'Start':'End'}<br>${esc(formatDubaiTime(p.time))}`);});mapInstance.fitBounds(L.latLngBounds(pts.map(p=>[Number(p.lat),Number(p.lng)])).pad(.2));closeMapHistory();showToast(`History loaded: ${pts.length} points`,'success');}
function showMapHistorySnapshot(index){const d=mapHistoryCache?.devices?.[index];if(!d)return;const p=new URLSearchParams();p.set('device',d.sn||d.name||'');[['from','mapHistoryFrom'],['to','mapHistoryTo']].forEach(([k,id])=>{const v=document.getElementById(id)?.value?.trim();if(v)p.set(k,v);});let modal=document.getElementById('mapSnapshotModal');if(!modal){modal=document.createElement('div');modal.id='mapSnapshotModal';modal.className='aerosync-modal';modal.innerHTML=`<div class="aerosync-modal-panel snapshot-panel"><div class="card-title-row"><h2>Saved Map Snapshot</h2><button class="ghost" onclick="document.getElementById('mapSnapshotModal').classList.remove('show')">Close</button></div><img id="mapSnapshotImage" alt="Historical route snapshot" class="map-snapshot-image"></div>`;document.body.appendChild(modal);}document.getElementById('mapSnapshotImage').src=`/api/map/history/snapshot?${p.toString()}&t=${Date.now()}`;modal.classList.add('show');}

async function saveMapSettings() {
  if (!hasPermission("settings")) return showToast("You do not have permission to change map settings", "error");
  const current = settingsCache.settings.modules.map || {};
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {
        modules: {
          map: {
            ...current,
            mode: document.getElementById("mapMode")?.value || "online",
            online_tile_url: document.getElementById("mapOnlineTileUrl")?.value || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            offline_tile_path: document.getElementById("mapOfflineTilePath")?.value || "",
            default_lat: Number(document.getElementById("mapDefaultLat")?.value || 25.2048),
            default_lng: Number(document.getElementById("mapDefaultLng")?.value || 55.2708),
            default_zoom: Number(document.getElementById("mapDefaultZoom")?.value || 12),
            refresh_seconds: Number(document.getElementById("mapRefreshSeconds")?.value || 5)
          }
        }
      }
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  showToast("Map settings saved");
  document.getElementById("mapSettingsModal")?.classList.remove("show");
  await renderLiveMap(document.getElementById("content"));
}

function uploadMapTiles(input) {
  if (!hasPermission("settings")) return showToast("You do not have permission to upload map tiles", "error");
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const result = await api("/api/map/upload-tiles", {
        method: "POST",
        body: JSON.stringify({filename: file.name, content: String(reader.result).split(",", 2)[1]})
      });
      showToast(`Tiles uploaded: ${result.result?.saved || 0} files`);
      await renderLiveMap(document.getElementById("content"));
    } catch (err) {
      showToast(`Tile upload failed: ${err.message}`, "error");
    } finally {
      input.value = "";
    }
  };
  reader.readAsDataURL(file);
}

async function renderLogs(content) {
  const data = await api("/api/logs");
  if (!isActiveContent(content, "Logs")) return;
  const retention = data.retention?.settings || {};
  const logGroups = data.retention?.groups || [];
  const logFiles = data.retention?.files || [];
  const logText = (lines) => String((lines || []).join("\n") || "No log data").replace(/\\n/g, "\n");
  content.innerHTML = `
    <div class="page-title"><h1>Logs</h1></div>
    <div class="card log-size-card">
      <div class="card-title-row"><h3>Storage Used By Logs</h3><span class="pill">${formatBytes(logGroups.reduce((sum, item) => sum + Number(item.size || 0), 0))}</span></div>
      <div class="log-size-grid">
        ${logGroups.map(item => `<div><strong>${esc(item.name)}</strong><span>${formatBytes(item.size || 0)}</span><small>${esc(item.files || 0)} files</small></div>`).join("") || `<div>No log files found</div>`}
      </div>
      <table class="table compact-table log-size-table">
        <thead><tr><th>File</th><th>Type</th><th>Size</th><th>Modified</th></tr></thead>
        <tbody>
          ${logFiles.slice(0, 10).map(file => `<tr><td>${esc(file.name)}</td><td>${esc(file.category || "--")}</td><td>${formatBytes(file.size || 0)}</td><td>${esc(formatDubaiTime(file.modified))}</td></tr>`).join("") || `<tr><td colspan="4">No log files found.</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="card log-retention-card">
      <div class="card-title-row">
        <h3>Log Retention</h3>
        <div class="toolbar-actions">
          <button class="secondary" onclick="saveLogRetention()">Save Log Retention</button>
          <button class="secondary" onclick="runLogCleanup()">Run Cleanup Now</button>
        </div>
      </div>
      <div class="form-grid retention-grid">
        <label class="field"><span>Daily Rotation</span><select id="logDailyRotation"><option value="true" ${retention.daily_rotation !== false ? "selected" : ""}>Enabled</option><option value="false" ${retention.daily_rotation === false ? "selected" : ""}>Disabled</option></select></label>
        <label class="field"><span>Compress Old Logs</span><select id="logCompress"><option value="false" ${!retention.compress_old_logs ? "selected" : ""}>Disabled</option><option value="true" ${retention.compress_old_logs ? "selected" : ""}>Enabled</option></select></label>
        <label class="field"><span>Module Logs Keep Days</span><input id="logModuleDays" type="number" min="1" value="${esc(retention.module_retention_days || 30)}"></label>
        <label class="field"><span>MQTT Capture Keep Days</span><input id="logMqttDays" type="number" min="1" value="${esc(retention.mqtt_capture_retention_days || 30)}"></label>
        <label class="field"><span>Audit/User Activity Keep Days</span><input id="logAuditDays" type="number" min="1" value="${esc(retention.audit_retention_days || 90)}"></label>
        <label class="field"><span>EventAPI DB Keep Days</span><input id="logEventDays" type="number" min="1" value="${esc(retention.event_db_retention_days || 180)}"></label>
        <label class="field"><span>Max Log Size MB</span><input id="logMaxMb" type="number" min="1" value="${esc(retention.max_log_size_mb || 100)}"></label>
        <label class="field"><span>Drive Usage Limit %</span><input id="logDriveLimitPercent" type="number" min="50" max="98" value="${esc(retention.drive_usage_limit_percent || 80)}"></label>
      </div>
      <p class="muted">Last cleanup: ${esc(formatDubaiTime(retention.last_cleanup_at))}</p>
    </div>
    <div class="log-grid">
      ${(data.logs || []).map(log => `<div class="card">
        <h3>${esc(log.name)}</h3>
        <p class="muted">${esc(log.path || "Path not set")}</p>
        <pre class="log-box">${esc(logText(log.lines))}</pre>
      </div>`).join("")}
    </div>
  `;
}

async function saveLogRetention() {
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {
        log_retention: {
          daily_rotation: document.getElementById("logDailyRotation")?.value === "true",
          compress_old_logs: document.getElementById("logCompress")?.value === "true",
          module_retention_days: Number(document.getElementById("logModuleDays")?.value || 30),
          mqtt_capture_retention_days: Number(document.getElementById("logMqttDays")?.value || 30),
          audit_retention_days: Number(document.getElementById("logAuditDays")?.value || 90),
          event_db_retention_days: Number(document.getElementById("logEventDays")?.value || 180),
          max_log_size_mb: Number(document.getElementById("logMaxMb")?.value || 100),
          drive_usage_limit_percent: Number(document.getElementById("logDriveLimitPercent")?.value || 80)
        }
      }
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  await renderLogs(document.getElementById("content"));
  showToast("Log retention saved");
}

async function runLogCleanup() {
  const result = await api("/api/logs/cleanup", {method: "POST", body: JSON.stringify({})});
  await renderLogs(document.getElementById("content"));
  showToast(`Cleanup complete: ${result.result?.deleted || 0} deleted`);
}

async function renderBackup(content) {
  const data = await api("/api/backup");
  if (!isActiveContent(content, "Backup")) return;
  const cfg = data.settings || {};
  content.innerHTML = `
    <div class="page-title">
      <h1>Backup</h1>
      <button class="primary" onclick="runBackupNow()">Backup Now</button>
    </div>
    <div class="forms">
      <div class="card">
        <h3>Backup Settings</h3>
        <label class="field"><span>Backup Path</span><input id="backupPath" value="${esc(cfg.backup_path || "")}" placeholder="D:\\OperationCenter\\Backups"></label>
        <label class="field"><span>Automatic Backup</span><select id="autoBackup"><option value="true" ${cfg.auto_backup ? "selected" : ""}>Enabled</option><option value="false" ${!cfg.auto_backup ? "selected" : ""}>Disabled</option></select></label>
        <label class="field"><span>Frequency</span><select id="backupFrequency">
          ${["daily", "7_days", "30_days"].map(v => `<option value="${v}" ${cfg.frequency === v ? "selected" : ""}>${v === "daily" ? "Every day" : v === "7_days" ? "Every 7 days" : "Every 30 days"}</option>`).join("")}
        </select></label>
        <label class="field"><span>Retention</span><select id="backupRetention">
          ${[2,7,30].map(v => `<option value="${v}" ${Number(cfg.retention_days) === v ? "selected" : ""}>Keep ${v} days</option>`).join("")}
        </select></label>
        <button class="secondary" onclick="saveBackupSettings()">Save Backup Settings</button>
        <p class="muted">Backups include users, roles, settings, certificates, audit logs, and configured module data paths.</p>
      </div>
      <div class="card">
        <h3>Latest Backups</h3>
        ${data.backups && data.backups.length ? `<table class="table"><thead><tr><th>Name</th><th>Size</th><th>Modified</th></tr></thead><tbody>${data.backups.map(b => `<tr><td>${esc(b.name)}</td><td>${formatBytes(b.size)}</td><td>${esc(b.modified)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">No backups created yet.</p>`}
      </div>
    </div>
  `;
}

async function saveBackupSettings() {
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {
        backup: {
          backup_path: document.getElementById("backupPath").value,
          auto_backup: document.getElementById("autoBackup").value === "true",
          frequency: document.getElementById("backupFrequency").value,
          retention_days: Number(document.getElementById("backupRetention").value)
        }
      }
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  renderBackup(document.getElementById("content"));
  showToast("Backup settings saved");
}

async function runBackupNow() {
  await api("/api/backup", {method: "POST", body: JSON.stringify({})});
  renderBackup(document.getElementById("content"));
  showToast("Backup created");
}

async function renderUsers(content) {
  const data = await api("/api/users");
  const roles = Object.keys(data.roles || {});
  content.innerHTML = `
    <div class="page-title"><h1>Users</h1></div>
    <div class="user-admin-layout">
      <div class="card user-form-card">
        <div class="card-title-row"><h3>Add / Update User</h3><button class="secondary small-btn" onclick="clearUserForm()">Clear</button></div>
        <div class="user-form-grid">
          <label class="field"><span>Name</span><input id="userName" placeholder="Full name"></label>
          <label class="field"><span>Email</span><input id="userEmail" placeholder="name@example.com"></label>
          <label class="field"><span>Username</span><input id="userUsername" placeholder="username"></label>
          <label class="field"><span>Password</span><input id="userPassword" type="password" placeholder="minimum 8 characters"></label>
          <label class="field"><span>Role</span><select id="userRole">${roles.map(r => `<option value="${esc(r)}">${esc(r)}</option>`).join("")}</select></label>
          <label class="field"><span>Account Status</span><select id="userLocked"><option value="false">Unlocked</option><option value="true">Locked</option></select></label>
        </div>
        <button class="primary" style="width:100%" onclick="saveUser()">Save User</button>
      </div>
      <div class="card user-list-card">
        <div class="card-title-row"><h3>User List</h3><span class="pill">${esc((data.users || []).length)} users</span></div>
        <table class="table">
          <thead><tr><th>Name</th><th>Email</th><th>Username</th><th>Role</th><th>Status</th><th>Action</th></tr></thead>
          <tbody>
            ${(data.users || []).map(u => `<tr>
              <td><strong>${esc(u.name || u.username)}</strong></td><td>${esc(u.email || "--")}</td><td>${esc(u.username)}</td><td><span class="pill">${esc(u.role)}</span></td>
              <td><span class="status-badge ${u.locked ? "offline" : "online"}">${u.locked ? "Locked" : "Active"}</span></td>
              <td>
                <button class="ghost small-btn" onclick='loadUserForm(${JSON.stringify(u).replace(/'/g, "&apos;")})'>Edit</button>
                ${u.role === "Admin" ? `<button class="ghost small-btn" disabled title="Admin users cannot be deleted">Delete</button>` : `<button class="ghost small-btn danger-btn" onclick="deleteUser('${escAttr(u.username)}')">Delete</button>`}
              </td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>
      <div class="card settings-card-wide">
        <div class="card-title-row">
          <h3>Role Permissions</h3>
          <button class="primary small-btn" onclick="saveRolePermissions()">Save Permissions</button>
        </div>
        <p class="muted">Admin role is fixed with full access. User and Support permissions can be selected here.</p>
        ${rolePermissionMatrix(data.roles || {})}
      </div>
    </div>
  `;
}

function clearUserForm() {
  ["userName", "userEmail", "userUsername", "userPassword"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const role = document.getElementById("userRole");
  if (role) role.value = "User";
  const locked = document.getElementById("userLocked");
  if (locked) locked.value = "false";
}

function rolePermissionMatrix(roles) {
  const roleNames = Object.keys(roles || {});
  return `
    <table class="table compact-table role-permission-table">
      <thead>
        <tr><th>Module</th>${roleNames.map(role => `<th>${esc(role)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${permissionLabels.map(([permission, label]) => `
          <tr>
            <td>${esc(label)}</td>
            ${roleNames.map(role => {
              const checked = (roles[role] || []).includes(permission);
              const locked = role === "Admin";
              return `<td><input type="checkbox" data-role-permission="${escAttr(role)}:${escAttr(permission)}" ${checked ? "checked" : ""} ${locked ? "disabled" : ""}></td>`;
            }).join("")}
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function loadUserForm(u) {
  document.getElementById("userName").value = u.name || "";
  document.getElementById("userEmail").value = u.email || "";
  document.getElementById("userUsername").value = u.username || "";
  document.getElementById("userPassword").value = "";
  document.getElementById("userRole").value = u.role || "User";
  document.getElementById("userLocked").value = String(Boolean(u.locked));
}

async function saveUser() {
  await api("/api/users", {
    method: "POST",
    body: JSON.stringify({
      name: document.getElementById("userName").value,
      email: document.getElementById("userEmail").value,
      username: document.getElementById("userUsername").value,
      password: document.getElementById("userPassword").value,
      role: document.getElementById("userRole").value,
      locked: document.getElementById("userLocked").value === "true"
    })
  });
  renderUsers(document.getElementById("content"));
  showToast("User saved");
}

async function deleteUser(username) {
  if (!username) return;
  if (!confirm(`Delete user ${username}?`)) return;
  try {
    await api("/api/users/delete", {
      method: "POST",
      body: JSON.stringify({username})
    });
    await renderUsers(document.getElementById("content"));
    showToast("User deleted");
  } catch (err) {
    showToast(err.message || "User delete failed", "error");
  }
}

async function saveRolePermissions() {
  const currentRoles = settingsCache.settings.roles || {};
  const roles = {};
  Object.keys(currentRoles).forEach(role => {
    roles[role] = role === "Admin"
      ? permissionLabels.map(([permission]) => permission)
      : permissionLabels
        .filter(([permission]) => document.querySelector(`[data-role-permission="${cssEscape(role)}:${cssEscape(permission)}"]`)?.checked)
        .map(([permission]) => permission);
  });
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({settings: {roles}})
    });
    settingsCache = {settings: data.settings, urls: data.urls};
    await renderUsers(document.getElementById("content"));
    showToast("Role permissions saved");
  } catch (err) {
    showToast(err.message || "Role permission save failed", "error");
  }
}

async function renderLicense(content) {
  const data = await api("/api/license/status");
  licenseState = data.license;
  const lic = data.license || {};
  const isValid = lic.status === "valid";
  const isExpired = lic.status === "expired";
  const statusClass = isValid ? "success" : (isExpired ? "warn" : "danger");
  content.innerHTML = `
    <div class="module-header">
      <div><h1>License</h1></div>
      <span class="pill ${statusClass}">${esc(lic.message || lic.status || "Unknown")}</span>
    </div>
    <div class="license-grid">
      <div class="card">
        <h3>Machine Code</h3>
        <p class="muted">Send this code to AERO NEX when requesting or renewing a license.</p>
        <div class="machine-code wide">${esc(lic.machine_code || "--")}</div>
        <div class="button-row">
          <button class="secondary" onclick="copyText('${escAttr(lic.machine_code || "")}')">Copy Machine Code</button>
          <label class="primary file-btn">Import License<input type="file" accept=".lic,.json,application/json" onchange="importLicenseFile(this)" hidden></label>
        </div>
        <label class="field">
          <span>Paste License Code</span>
          <textarea id="licensePasteCode" rows="8" placeholder="ASLIC-START ... ASLIC-END"></textarea>
        </label>
        <button class="primary" onclick="activateLicenseCode()">Activate License</button>
      </div>
      <div class="card">
        <h3>License Details</h3>
        <div class="detail-grid">
          <span>Status</span><strong>${esc(lic.status || "--")}</strong>
          <span>Company Name</span><strong>${esc(lic.company || "--")}</strong>
          <span>Email Address</span><strong>${esc(lic.email || "--")}</strong>
          <span>License Type</span><strong>${esc(lic.license_type || "--")}</strong>
          <span>Edition</span><strong>${esc(lic.edition || "--")}</strong>
          <span>Expiry Date</span><strong>${esc(lic.expires_at || "--")}</strong>
        </div>
        ${isExpired ? `<p class="error">License expired. Admin can stay logged in only to import a renewed license.</p>` : ""}
      </div>
    </div>
    <div class="license-branding card">
      <strong>AERO SYNC</strong>
      <span>Designed &amp; Developed by AERO NEX FZCO</span>
      <span>2025 Aero Nex FZCO. All Rights Reserved.</span>
      <span>Contact us : <a href="mailto:Support@aeronex.ae">Support@aeronex.ae</a></span>
    </div>
  `;
}

async function renderReports(content) {
  const today = new Date();
  const prior = new Date(today.getTime() - 7 * 86400000);
  const toDate = today.toISOString().slice(0, 10);
  const fromDate = prior.toISOString().slice(0, 10);
  const templates = settingsCache.settings.modules.email?.templates || [];
  content.innerHTML = `
    <div class="page-title">
      <h1>Reports</h1>
      <div class="toolbar-actions">
        <button class="primary" onclick="generateReport()">Generate Report</button>
        <button class="secondary" onclick="exportReportCsv()">Export CSV</button>
        <button class="secondary" onclick="exportReportJson()">Export JSON</button>
        <button class="secondary" onclick="emailReport()">Email Report</button>
      </div>
    </div>
    <div class="card report-filters">
      <label class="field"><span>From</span><input id="reportFrom" type="date" value="${fromDate}"></label>
      <label class="field"><span>To</span><input id="reportTo" type="date" value="${toDate}"></label>
      <label class="field"><span>Section</span><select id="reportSection">
        ${["All", "EventAPI", "MQTT", "Media / S3", "Live Streams", "Map History", "User Activity", "Users / Security", "System Health"].map(v => `<option value="${esc(v.toLowerCase())}">${esc(v)}</option>`).join("")}
      </select></label>
      <label class="field"><span>Device / Keyword</span><input id="reportDevice" placeholder="SN, channel, file, topic"></label>
      <label class="field"><span>Email Template</span><select id="reportEmailTemplate">
        ${templates.length ? templates.map(t => `<option value="${esc(t.id || t.name)}">${esc(t.name || t.id)}</option>`).join("") : `<option value="">No template configured</option>`}
      </select></label>
    </div>
    <div id="reportResult"></div>
  `;
  await generateReport();
}

async function generateReport() {
  const params = new URLSearchParams({
    from: document.getElementById("reportFrom")?.value || "",
    to: document.getElementById("reportTo")?.value || "",
    section: document.getElementById("reportSection")?.value || "all",
    device: document.getElementById("reportDevice")?.value || ""
  });
  reportsCache = await api(`/api/reports?${params.toString()}`);
  renderReportResult();
  showToast("Report generated");
}

async function emailReport() {
  if (!reportsCache) await generateReport();
  const email = settingsCache.settings.modules.email || {};
  try {
    const result = await api("/api/reports/email", {
      method: "POST",
      body: JSON.stringify({
        from: document.getElementById("reportFrom")?.value || "",
        to_date: document.getElementById("reportTo")?.value || "",
        template_id: document.getElementById("reportEmailTemplate")?.value || ""
      })
    });
    showToast(`Report emailed to ${(result.result?.to || []).join(", ") || "recipient"}`);
  } catch (err) {
    showToast(`Email failed: ${err.message}`, "error");
  }
}

function renderReportResult() {
  const target = document.getElementById("reportResult");
  if (!target || !reportsCache) return;
  const s = reportsCache.summary || {};
  const rows = reportsCache.rows || [];
  target.innerHTML = `
    <div class="report-summary">
      ${[
        ["EventAPI", s.events || 0],
        ["MQTT", s.mqtt || 0],
        ["Media / S3", s.media || 0],
        ["Live Streams", s.streams || 0],
        ["Map History", s.map_history || 0],
        ["Activity", s.activity || 0],
        ["Security", s.security || 0],
        ["System", s.system || 0]
      ].map(([label, value]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}
    </div>
    <div class="card">
      <div class="card-title-row"><h3>Report Details</h3><span class="pill">${esc(s.total || rows.length)} rows</span></div>
      <table class="table">
        <thead><tr><th>Section</th><th>Time</th><th>Type</th><th>Device</th><th>Status</th><th>Details</th></tr></thead>
        <tbody>
          ${rows.length ? rows.slice(0, 500).map(r => `<tr>
            <td>${esc(r.section)}</td>
            <td>${esc(formatDubaiTime(r.time))}</td>
            <td>${esc(r.type)}</td>
            <td>${esc(r.device || "--")}</td>
            <td>${esc(r.status)}</td>
            <td>${esc(r.details)}</td>
          </tr>`).join("") : `<tr><td colspan="6">No report data available.</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function exportReportJson() {
  if (!reportsCache) return showToast("Generate report first");
  downloadText(`OperationCenter_Report_${currentDateStamp()}.json`, JSON.stringify(reportsCache, null, 2), "application/json");
}

function exportReportCsv() {
  if (!reportsCache) return showToast("Generate report first");
  const rows = reportsCache.rows || [];
  const headers = ["section", "time", "type", "device", "status", "details"];
  const csv = [
    headers.join(","),
    ...rows.map(row => headers.map(h => csvCell(row[h])).join(","))
  ].join("\n");
  downloadText(`OperationCenter_Report_${currentDateStamp()}.csv`, csv, "text/csv");
}

function csvCell(value) {
  const text = String(value ?? "");
  return `"${text.replace(/"/g, '""')}"`;
}

function downloadText(filename, text, mime) {
  const blob = new Blob([text], {type: `${mime};charset=utf-8`});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function currentDateStamp() {
  const d = new Date();
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function sourceNotice(data, label) {
  if (data.available) return "";
  return `<div class="card warn-card"><strong>${esc(label)} not available.</strong><p class="muted">Set the path in Settings to connect this module.</p></div><div style="height:12px"></div>`;
}

function kvList(obj) {
  const entries = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).slice(0, 8);
  if (!entries.length) return `<p class="muted">No data available.</p>`;
  return `<div class="mini-list">${entries.map(([k, v]) => `<div><strong>${esc(v)}</strong> ${esc(k)}</div>`).join("")}</div>`;
}

function formatBandwidth(bytesPerSecond) {
  const value = Math.max(0, Number(bytesPerSecond || 0)) * 8;
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)} Mbps`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)} Kbps`;
  return `${value.toFixed(0)} bps`;
}
function formatBytes(n) {
  n = Number(n || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${i === 0 ? n : n.toFixed(1)} ${units[i]}`;
}

function formatDubaiTime(value) {
  if (!value) return "--";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const dubai = new Date(d.getTime() + 4 * 60 * 60 * 1000);
  const pad = n => String(n).padStart(2, "0");
  return `${dubai.getUTCFullYear()}-${pad(dubai.getUTCMonth() + 1)}-${pad(dubai.getUTCDate())} ${pad(dubai.getUTCHours())}:${pad(dubai.getUTCMinutes())}:${pad(dubai.getUTCSeconds())} UTC+4`;
}

function formatStreamDetails(info = {}) {
  if (!info || !info.available) return "Codec: -- | Resolution: -- | FPS: -- | Bitrate: --";
  const resolution = info.width && info.height ? `${info.width}x${info.height}` : "--";
  const fps = info.fps ? `${info.fps}` : "--";
  const bitrate = info.bitrate_kbps ? `${info.bitrate_kbps} kbps` : "--";
  return `Codec: ${info.codec || "--"} | Resolution: ${resolution} | FPS: ${fps} | Bitrate: ${bitrate}`;
}

function streamMetaText(info = {}, status = "Idle") {
  return `${formatStreamDetails(info)} | Status: ${status || "Idle"}`;
}

function renderPlaceholder(content, name) {
  content.innerHTML = `
    <div class="page-title"><h1>${esc(name)}</h1></div>
    <div class="card">
      <h3>${esc(name)} Module</h3>
      <p class="muted">This module page is prepared. The existing ${esc(name)} tool will be connected here in the next build step.</p>
    </div>
  `;
}

async function renderLiveStreams(content, keepSettingsOpen = false) {
  await loadSettings();
  if (!isActiveContent(content, "Live Streams")) return;
  const streamStatus = await api("/api/stream/status").catch(() => ({stream_info: {}}));
  const cfg = settingsCache.settings.modules.live_streams;
  const channels = (cfg.channels || []).map(ch => ({
    ...ch,
    stream_info: streamStatus.stream_info?.[String(ch.channel)] || {}
  }));
  const savedLayout = Number(cfg.layout || 4);
  const layout = [4, 8].includes(savedLayout) ? savedLayout : 4;
  if (streamPageStart >= channels.length) streamPageStart = 0;
  const shown = channels.slice(streamPageStart, streamPageStart + layout);
  const gridClass = `layout-${layout}`;
  const dwellLabel = cfg.dwell_seconds ? `${cfg.dwell_seconds}s` : "Off";
  const end = Math.min(streamPageStart + layout, channels.length);
  content.innerHTML = `
    <div class="streams">
      <div class="page-title">
        <h1>Live Streams</h1>
        <span class="pill">20 Channels | ${channels.filter(c => c.enabled).length} Enabled | ${channels.filter(c => !c.enabled).length} Disabled</span>
      </div>
      <div class="video-grid ${gridClass}">
        ${shown.map(c => `
          <div class="video-tile" id="streamTile${c.channel}">
            <div class="stream-title">${esc(c.name)}</div>
            <div class="time-osd">${currentTimestamp()}</div>
            <div class="video-empty">Preview stopped</div>
            <div class="stream-meta">${streamMetaText(c.stream_info, c.enabled ? "Ready" : "Idle")}</div>
            <div class="stream-controls">
              <button class="secondary mini-action" onclick="streamAction(${c.channel}, 'start')">Start Live</button>
              <button class="ghost mini-action" onclick="streamAction(${c.channel}, 'stop')">Stop Live</button>
              <button class="ghost mini-action" onclick="streamAction(${c.channel}, 'record')">Record</button>
              <button class="ghost mini-action" onclick="streamAction(${c.channel}, 'capture')">Capture</button>
              <button class="ghost mini-action" onclick="fullScreenTile(${c.channel})">Full Screen</button>
              <label class="check mini-check"><input type="checkbox" checked> OSD</label>
            </div>
          </div>
        `).join("")}
      </div>
      <div class="bottom-controls">
        <strong>Layout</strong>
        <span class="seg">${[4,8].map(v => `<button onclick="setStreamLayout(${v})" class="${layout === v ? "active" : ""}">${v}</button>`).join("")}</span>
        <strong>Dwell</strong>
        <span class="seg">${["Off","3s","5s","10s","20s"].map(v => `<button onclick="setStreamDwell('${v}')" class="${dwellLabel === v ? "active" : ""}">${v}</button>`).join("")}</span>
        <button class="secondary" onclick="previousStreamGroup()">Previous</button>
        <button class="primary" onclick="setStreamDwell('Off')">Pause</button>
        <button class="secondary" onclick="nextStreamGroup()">Next</button>
        <button class="ghost" onclick="toggleStreamSettings()">Settings</button>
        <span class="muted">Showing ${streamPageStart + 1}-${end} of 20</span>
      </div>
      <div class="stream-settings" id="streamSettings">
        <div class="page-title">
          <h3>Live Stream Settings</h3>
          <button class="primary" onclick="saveLiveStreamSettings()">Save Stream Settings</button>
        </div>
        <label class="field">
          <span>Capture / Recording Save Path</span>
          <input id="streamSavePath" value="${esc(cfg.save_path || "")}" placeholder="D:\\OperationCenter\\Recordings">
        </label>
        <div class="channel-settings">
          ${channels.map(ch => `
            <div class="channel-row">
              <label><input data-stream-enabled="${ch.channel}" type="checkbox" ${ch.enabled ? "checked" : ""}> ${String(ch.channel).padStart(2, "0")}</label>
              <input data-stream-name="${ch.channel}" value="${esc(ch.name || "")}" placeholder="Camera name">
              <input data-stream-url="${ch.channel}" value="${esc(ch.rtsp_url || "")}" placeholder="RTSP or RTMP URL">
              <div class="channel-meta">SN: ${esc(ch.device_sn || "--")} | Updated: ${esc(formatDubaiTime(ch.updated_at))}</div>
              <button class="ghost mini-action" type="button" onclick="resetLiveStreamChannel(${Number(ch.channel)})">Clear</button>
            </div>
          `).join("")}
        </div>
      </div>
    </div>
  `;
  if (keepSettingsOpen) document.getElementById("streamSettings")?.classList.add("show");
  scheduleDwell();
  scheduleLiveSettingsRefresh();
}

async function streamAction(channel, action) {
  const tile = document.getElementById(`streamTile${channel}`);
  if (tile) tile.querySelector(".stream-meta").textContent = streamMetaText({}, "checking...");
  try {
    const result = await api("/api/stream/action", {
      method: "POST",
      body: JSON.stringify({channel, action})
    });
    if (tile) {
      tile.classList.toggle("stream-error", !result.ok);
      const status = result.ok
        ? (result.status || (result.recording ? "Recording" : "Saved"))
        : `Error: ${result.error || "Stream failed"}`;
      tile.querySelector(".stream-meta").textContent = streamMetaText(result.stream_info, status);
      const empty = tile.querySelector(".video-empty");
      if (empty && result.preview_url) {
        const baseUrl = result.preview_url.split("?")[0];
        empty.innerHTML = `<img class="stream-preview" data-snapshot="${esc(baseUrl)}" src="${esc(result.preview_url)}" alt="Channel ${esc(channel)} live preview">`;
        startPreviewRefresh();
      } else if (empty && result.message) {
        empty.textContent = result.message;
      } else if (empty && action === "stop") {
        empty.textContent = "Preview stopped";
      }
    }
  } catch (err) {
    if (tile) {
      tile.classList.add("stream-error");
      tile.querySelector(".stream-meta").textContent = streamMetaText({}, `Error: ${err.message}`);
      const empty = tile.querySelector(".video-empty");
      if (empty) empty.textContent = err.message;
    }
  }
}

function startPreviewRefresh() {
  if (previewRefreshTimer) return;
  previewRefreshTimer = setInterval(() => {
    document.querySelectorAll("img.stream-preview[data-snapshot]").forEach(img => {
      img.src = `${img.dataset.snapshot}?t=${Date.now()}`;
    });
  }, 700);
}

function liveChannelSignature(cfg = settingsCache?.settings?.modules?.live_streams) {
  return JSON.stringify((cfg?.channels || []).map(ch => ({
    channel: ch.channel,
    name: ch.name,
    enabled: ch.enabled,
    rtsp_url: ch.rtsp_url,
    rtsp_source_url: ch.rtsp_source_url,
    updated_at: ch.updated_at,
    event_timestamp: ch.event_timestamp,
    device_sn: ch.device_sn,
    camera_index: ch.camera_index,
    converter_name: ch.converter_name
  })));
}

function scheduleLiveSettingsRefresh() {
  if (liveSettingsRefreshTimer || activePage !== "Live Streams") return;
  let lastSignature = liveChannelSignature();
  liveSettingsRefreshTimer = setInterval(async () => {
    if (activePage !== "Live Streams") {
      clearInterval(liveSettingsRefreshTimer);
      liveSettingsRefreshTimer = null;
      return;
    }
    const keepSettingsOpen = document.getElementById("streamSettings")?.classList.contains("show");
    try {
      const data = await api("/api/settings");
      const nextSignature = liveChannelSignature(data.settings.modules.live_streams);
      if (nextSignature !== lastSignature) {
        settingsCache = data;
        lastSignature = nextSignature;
        if (!isEditingStreamSettings()) {
          await renderLiveStreams(document.getElementById("content"), keepSettingsOpen);
          showToast("Live stream channels updated from EventAPI");
        } else {
          syncLiveStreamSettingsDom(data.settings.modules.live_streams);
          showToast("Live stream URL updated from EventAPI");
        }
      }
    } catch (err) {
      console.warn("Live stream settings refresh failed", err);
    }
  }, 3000);
}

function fullScreenTile(channel) {
  const tile = document.getElementById(`streamTile${channel}`);
  if (!tile) return;
  if (tile.requestFullscreen) tile.requestFullscreen();
  else if (tile.webkitRequestFullscreen) tile.webkitRequestFullscreen();
  else tile.classList.toggle("tile-expanded");
}

async function setStreamLayout(layout) {
  const current = settingsCache.settings.modules.live_streams;
  const keepSettingsOpen = document.getElementById("streamSettings")?.classList.contains("show");
  streamPageStart = 0;
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {modules: {live_streams: {...current, layout: Number(layout)}}}
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  await renderLiveStreams(document.getElementById("content"), keepSettingsOpen);
  showToast("Layout saved");
}

async function setStreamDwell(value) {
  const current = settingsCache.settings.modules.live_streams;
  const keepSettingsOpen = document.getElementById("streamSettings")?.classList.contains("show");
  const seconds = value === "Off" ? 0 : Number(value.replace("s", ""));
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {modules: {live_streams: {...current, dwell_seconds: seconds}}}
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  await renderLiveStreams(document.getElementById("content"), keepSettingsOpen);
  showToast("Dwell saved");
}

async function nextStreamGroup() {
  const current = settingsCache.settings.modules.live_streams;
  const layout = Number(current.layout || 4);
  const total = (current.channels || []).length || 20;
  const keepSettingsOpen = document.getElementById("streamSettings")?.classList.contains("show");
  streamPageStart = streamPageStart + layout >= total ? 0 : streamPageStart + layout;
  await renderLiveStreams(document.getElementById("content"), keepSettingsOpen);
}

async function previousStreamGroup() {
  const current = settingsCache.settings.modules.live_streams;
  const layout = Number(current.layout || 4);
  const total = (current.channels || []).length || 20;
  const keepSettingsOpen = document.getElementById("streamSettings")?.classList.contains("show");
  streamPageStart = streamPageStart - layout < 0 ? Math.max(total - layout, 0) : streamPageStart - layout;
  await renderLiveStreams(document.getElementById("content"), keepSettingsOpen);
}

function scheduleDwell() {
  if (dwellTimer) clearInterval(dwellTimer);
  dwellTimer = null;
  const seconds = Number(settingsCache.settings.modules.live_streams.dwell_seconds || 0);
  if (!seconds || activePage !== "Live Streams") return;
  dwellTimer = setInterval(() => {
    if (activePage === "Live Streams" && !isEditingStreamSettings()) nextStreamGroup();
  }, seconds * 1000);
}

function toggleStreamSettings() {
  document.getElementById("streamSettings")?.classList.toggle("show");
}

async function resetLiveStreamChannel(channel) {
  try {
    const data = await api("/api/live-stream/channel/clear", {
      method: "POST",
      body: JSON.stringify({channel: Number(channel)})
    });
    await loadSettings();
    await renderLiveStreams(document.getElementById("content"), true);
    const nvrText = data.released_nvr_mappings ? ` | ${data.released_nvr_mappings} NVR mapping(s) released` : "";
    showToast(`Channel ${String(channel).padStart(2, "0")} cleared${nvrText}`);
  } catch (err) {
    showToast(err.message || "Channel clear failed", "error");
  }
}

async function saveLiveStreamSettings() {
  await loadSettings();
  const current = settingsCache.settings.modules.live_streams;
  const channels = (current.channels || []).map(ch => ({
    ...ch,
    enabled: Boolean(document.querySelector(`[data-stream-enabled="${ch.channel}"]`)?.checked),
    name: document.querySelector(`[data-stream-name="${ch.channel}"]`)?.value || ch.name,
    rtsp_url: document.querySelector(`[data-stream-url="${ch.channel}"]`)?.value || ""
  }));
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {
        modules: {
          live_streams: {
            ...current,
            save_path: document.getElementById("streamSavePath").value,
            channels
          }
        }
      }
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  await renderLiveStreams(document.getElementById("content"), true);
  showToast("Live stream settings saved");
}

function currentTimestamp() {
  const d = new Date();
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function reportSectionOptions(selected = "all") {
  return ["all", "eventapi", "mqtt", "media / s3", "live streams", "map history", "user activity", "users / security", "system health"]
    .map(v => `<option value="${esc(v)}" ${String(selected).toLowerCase() === v ? "selected" : ""}>${esc(v === "all" ? "All" : v.replace(/\b\w/g, c => c.toUpperCase()))}</option>`)
    .join("");
}

function emailTemplateRow(template = {}, index = 0) {
  const id = template.id || `template_${Date.now()}_${index}`;
  const formats = Array.isArray(template.formats) ? template.formats.join(", ") : (template.formats || "csv, json");
  const title = template.name || `Template ${index + 1}`;
  return `
    <details class="email-template-row" data-email-template="${index}">
      <summary>
        <strong>Template ${index + 1}</strong>
        <span>${esc(title)}</span>
        <button class="ghost small-btn" type="button" onclick="event.preventDefault(); this.closest('.email-template-row').remove()">Remove</button>
      </summary>
      <div class="form-grid">
        <label class="field"><span>Template Name</span><input data-et-name value="${esc(template.name || "")}" placeholder="Daily Operations"></label>
        <label class="field"><span>Template ID</span><input data-et-id value="${esc(id)}"></label>
      </div>
      <div class="form-grid">
        <label class="field"><span>Scheduled Email</span><select data-et-schedule-enabled><option value="false" ${!template.schedule_enabled ? "selected" : ""}>Disabled</option><option value="true" ${template.schedule_enabled ? "selected" : ""}>Enabled</option></select></label>
        <label class="field"><span>Frequency</span><select data-et-schedule-frequency>
          ${["daily", "weekly", "monthly"].map(v => `<option value="${v}" ${template.schedule_frequency === v ? "selected" : ""}>${v[0].toUpperCase() + v.slice(1)}</option>`).join("")}
        </select></label>
        <label class="field"><span>Send Time</span><input data-et-schedule-time type="time" value="${esc(template.schedule_time || "08:00")}"></label>
        <label class="field"><span>Monthly Day</span><input data-et-schedule-day type="number" min="1" max="31" value="${esc(template.schedule_day || 1)}"></label>
      </div>
      <label class="field"><span>Recipients</span><textarea data-et-to rows="2" placeholder="person1@example.com, person2@example.com">${esc(template.to || "")}</textarea></label>
      <div class="form-grid">
        <label class="field"><span>Sender Override</span><input data-et-from value="${esc(template.from_address || "")}" placeholder="blank = first SMTP sender"></label>
        <label class="field"><span>Attachment Formats</span><input data-et-formats value="${esc(formats)}" placeholder="csv, json, pdf, xlsx"></label>
        <label class="field"><span>Report Section</span><select data-et-section>${reportSectionOptions(template.section || "all")}</select></label>
        <label class="field"><span>Device / Keyword Filter</span><input data-et-device value="${esc(template.device || "")}"></label>
      </div>
      <div class="form-grid">
        <label class="field"><span>CC</span><input data-et-cc value="${esc(template.cc || "")}"></label>
        <label class="field"><span>BCC</span><input data-et-bcc value="${esc(template.bcc || "")}"></label>
      </div>
      <label class="field"><span>Subject</span><input data-et-subject value="${esc(template.subject || "Operation Center Report - {date}")}"></label>
      <label class="field"><span>Body</span><textarea data-et-body rows="4">${esc(template.body || "")}</textarea></label>
    </details>
  `;
}

function addEmailTemplate() {
  const list = document.getElementById("emailTemplateList");
  const index = document.querySelectorAll("[data-email-template]").length;
  list.insertAdjacentHTML("beforeend", emailTemplateRow({
    name: "New Report Template",
    subject: "Operation Center Report - {date}",
    body: "Dear Team,\\n\\nPlease find attached the Operation Center report.\\n\\nRows: {rows}\\n\\nRegards,\\nOperation Center",
    section: "all",
    formats: ["csv", "json"],
    schedule_enabled: false,
    schedule_frequency: "daily",
    schedule_time: "08:00",
    schedule_day: 1
  }, index));
}

function collectEmailTemplates() {
  return Array.from(document.querySelectorAll("[data-email-template]")).map((row, index) => ({
    id: row.querySelector("[data-et-id]")?.value || `template_${index + 1}`,
    name: row.querySelector("[data-et-name]")?.value || `Template ${index + 1}`,
    from_address: row.querySelector("[data-et-from]")?.value || "",
    to: row.querySelector("[data-et-to]")?.value || "",
    cc: row.querySelector("[data-et-cc]")?.value || "",
    bcc: row.querySelector("[data-et-bcc]")?.value || "",
    subject: row.querySelector("[data-et-subject]")?.value || "",
    body: row.querySelector("[data-et-body]")?.value || "",
    section: row.querySelector("[data-et-section]")?.value || "all",
    device: row.querySelector("[data-et-device]")?.value || "",
    formats: (row.querySelector("[data-et-formats]")?.value || "csv,json").split(",").map(x => x.trim().toLowerCase()).filter(Boolean),
    schedule_enabled: row.querySelector("[data-et-schedule-enabled]")?.value === "true",
    schedule_frequency: row.querySelector("[data-et-schedule-frequency]")?.value || "daily",
    schedule_time: row.querySelector("[data-et-schedule-time]")?.value || "08:00",
    schedule_day: Number(row.querySelector("[data-et-schedule-day]")?.value || 1),
    last_sent_at: row.dataset.lastSentAt || ""
  }));
}

function renderEmail(content) {
  const email = settingsCache.settings.modules.email || {};
  content.innerHTML = `
    <div class="page-title">
      <h1>Email</h1>
      <button class="primary" onclick="saveEmailSettings()">Save Email Settings</button>
    </div>
    <div class="email-layout">
      <div class="card">
        <h3>SMTP Settings</h3>
        <div class="email-smtp-grid">
          <label class="field"><span>Email Support</span><select id="emailEnabled"><option value="false" ${!email.enabled ? "selected" : ""}>Disabled</option><option value="true" ${email.enabled ? "selected" : ""}>Enabled</option></select></label>
          <label class="field"><span>Security</span><select id="emailSecurity">
            ${["starttls", "ssl", "none"].map(v => `<option value="${v}" ${email.security === v ? "selected" : ""}>${v.toUpperCase()}</option>`).join("")}
          </select></label>
          <label class="field"><span>SMTP Host</span><input id="emailHost" value="${esc(email.smtp_host || "")}" placeholder="smtp.office365.com"></label>
          <label class="field"><span>SMTP Port</span><input id="emailPort" type="number" min="1" max="65535" value="${esc(email.smtp_port || 587)}"></label>
          <label class="field"><span>SMTP Username</span><input id="emailUsername" value="${esc(email.username || "")}"></label>
          <label class="field"><span>SMTP Password</span><input id="emailPassword" type="password" value="${esc(email.password || "")}"></label>
        </div>
        <label class="field"><span>Sender Email Address(es)</span><textarea id="emailFrom" rows="2" placeholder="sender1@example.com, sender2@example.com">${esc(email.from_addresses || "")}</textarea></label>
        <div class="toolbar-actions">
          <button class="secondary" type="button" onclick="testEmail()">Send Test Email</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title-row">
          <h3>Email Report Templates</h3>
          <button class="secondary" type="button" onclick="addEmailTemplate()">Add Email Template</button>
        </div>
        <p class="muted">Each template can send a different report to different people. Click a template to edit it.</p>
        <div id="emailTemplateList" class="email-template-list">
          ${(email.templates && email.templates.length ? email.templates : [{
            id: "daily_operations",
            name: "Daily Operations",
            to: email.default_recipients || "",
            cc: email.cc_recipients || "",
            bcc: email.bcc_recipients || "",
            subject: email.template_subject || "Operation Center Report - {date}",
            body: email.template_body || "",
            section: "all",
            formats: email.default_attachment_formats || ["csv", "json"],
            schedule_enabled: false,
            schedule_frequency: "daily",
            schedule_time: "08:00",
            schedule_day: 1
          }]).map((template, index) => emailTemplateRow(template, index)).join("")}
        </div>
        <p class="muted">Template variables: {date}, {report_type}, {user}, {rows}.</p>
      </div>
    </div>
  `;
}


function openApiExtractList(result) {
  const payload = result?.data || {};
  return payload?.data?.list || payload?.list || [];
}

function openApiModuleFromSettings() {
  const cfg = settingsCache?.settings || {};
  const module = cfg?.modules?.openapi || {enabled:true, active_connection_id:"", connections:[]};
  return {
    ...module,
    connections: Array.isArray(module.connections) ? module.connections : []
  };
}

function openApiConnectionId() {
  const module = openApiModuleFromSettings();
  const saved = sessionStorage.getItem("aerosync_openapi_connection") || module.active_connection_id || "";
  return module.connections.some(c => c.id === saved && c.enabled !== false) ? saved : (module.connections.find(c => c.enabled !== false)?.id || "");
}

function openApiProjectStorageKey(connectionId = openApiConnectionId()) {
  return `aerosync_openapi_project_${connectionId || "default"}`;
}

function openApiSelectedProjectUuid(connectionId = openApiConnectionId()) {
  return sessionStorage.getItem(openApiProjectStorageKey(connectionId)) || "";
}

function openApiProjectQuery(path) {
  const projectUuid = openApiSelectedProjectUuid();
  const sep = path.includes("?") ? "&" : "?";
  return projectUuid ? `${path}${sep}project_uuid=${encodeURIComponent(projectUuid)}` : path;
}

async function openApiLoadProjects(connectionId = openApiConnectionId()) {
  if (!connectionId) return [];
  const result = await api(openApiQuery("/api/openapi/projects?page=1&page_size=100"));
  return openApiExtractList(result);
}

function openApiQuery(path) {
  const id = openApiConnectionId();
  const sep = path.includes("?") ? "&" : "?";
  return id ? `${path}${sep}connection_id=${encodeURIComponent(id)}` : path;
}

function openApiResetCache(connectionId = "") {
  openApiPageCache = {connectionId, overview:null, projects:null, devicesByProject:{}, loadedAt:""};
}

async function openApiLoadInitial(force = false) {
  const connectionId = openApiConnectionId();
  if (force || openApiPageCache.connectionId !== connectionId) openApiResetCache(connectionId);
  if (!force && openApiPageCache.overview && Array.isArray(openApiPageCache.projects)) return openApiPageCache;
  if (openApiPageLoading) return openApiPageLoading;
  openApiPageLoading = (async () => {
    const data = await api(openApiQuery("/api/openapi/overview"));
    openApiPageCache.overview = data.overview || {};
    let projects = [];
    try { projects = await openApiLoadProjects(connectionId); } catch (_) { projects = []; }
    openApiPageCache.projects = projects;
    const stored = openApiSelectedProjectUuid(connectionId);
    const selected = projects.find(p => p.uuid === stored) || projects[0] || null;
    if (selected?.uuid && selected.uuid !== stored) sessionStorage.setItem(openApiProjectStorageKey(connectionId), selected.uuid);
    if (selected?.uuid) {
      try {
        const result = await api(openApiQuery(openApiProjectQuery("/api/openapi/devices?scope=project")));
        openApiPageCache.devicesByProject[selected.uuid] = openApiExtractList(result);
      } catch (_) {
        openApiPageCache.devicesByProject[selected.uuid] = [];
      }
    }
    openApiPageCache.loadedAt = new Date().toISOString();
    return openApiPageCache;
  })();
  try { return await openApiPageLoading; }
  finally { openApiPageLoading = null; }
}

async function renderOpenApi(content, force = false) {
  content.innerHTML = `<div class="card loading-card"><strong>Loading OpenAPI...</strong></div>`;
  let cache;
  try { cache = await openApiLoadInitial(force); }
  catch (err) {
    if (isActiveContent(content, "OpenAPI")) content.innerHTML = `<div class="card warn-card"><strong>OpenAPI unavailable</strong><p>${esc(err.message)}</p></div>`;
    return;
  }
  if (!isActiveContent(content, "OpenAPI")) return;
  const overview = cache.overview || {};
  const stats = overview.statistics || {};
  const projects = cache.projects || [];
  const storedProject = openApiSelectedProjectUuid(overview.connection_id || openApiConnectionId());
  const selectedProject = projects.find(p => p.uuid === storedProject) || projects[0] || null;
  const deviceRows = selectedProject?.uuid ? (cache.devicesByProject[selectedProject.uuid] || []) : [];
  let onlineCount = 0, dockCount = 0;
  deviceRows.forEach(row => {
    const gateway = row.gateway || {}, drone = row.drone || {};
    if (gateway.device_online_status) onlineCount += 1;
    if (drone.device_online_status) onlineCount += 1;
    if (gateway.sn || gateway.callsign) dockCount += 1;
  });
  const moduleDefs = overview.modules || [
    {key:"overview",label:"Overview"},{key:"projects",label:"Projects"},{key:"devices",label:"Devices"},
    {key:"hms",label:"HMS"},{key:"livestream",label:"Livestream"},{key:"tasks",label:"Flight Tasks"},
    {key:"waylines",label:"Waylines"},{key:"maps",label:"Maps & Airspace"},{key:"models",label:"Models"},
    {key:"explorer",label:"API Explorer"},{key:"logs",label:"Logs"}
  ];
  const capabilities = overview.capabilities || {};
  const tabs = moduleDefs.map(m => [m.key, m.label, capabilities[m.key]?.supported !== false]);
  const connected = !!overview.configured;
  const lastUpdated = cache.loadedAt ? new Date(cache.loadedAt).toLocaleTimeString() : "--";
  content.innerHTML = `
    <style>
      .openapi-shell{display:flex;flex-direction:column;gap:12px}.openapi-head{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.openapi-head h1{margin:0;font-size:24px}.openapi-status{display:flex;align-items:center;gap:8px;font-weight:700;white-space:nowrap}.openapi-status-dot{width:9px;height:9px;border-radius:50%;background:${connected?'#35d07f':'#f0b84b'};box-shadow:0 0 10px ${connected?'#35d07f88':'#f0b84b88'}}.openapi-controlbar{display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) auto;gap:10px;align-items:end;padding:12px 14px}.openapi-control-actions{display:flex;gap:8px;align-items:center;white-space:nowrap}.openapi-kpis{display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));gap:8px}.openapi-kpi{padding:11px 14px;border:1px solid rgba(255,255,255,.1);border-radius:9px;background:rgba(255,255,255,.025);min-height:62px}.openapi-kpi span{display:block;font-size:11px;opacity:.72;margin-bottom:6px}.openapi-kpi strong{font-size:21px;line-height:1}.openapi-tabs{display:flex;gap:4px;align-items:center;padding:0 10px;border-bottom:1px solid rgba(255,255,255,.1);overflow-x:auto}.openapi-tab{background:transparent;border:0;border-bottom:2px solid transparent;border-radius:0;padding:11px 15px;color:inherit;opacity:.75;white-space:nowrap}.openapi-tab.active{opacity:1;color:#70a7ff;border-bottom-color:#4f8cff}.openapi-tab:disabled{opacity:.35;cursor:not-allowed;border-bottom-color:transparent}.openapi-main-card{padding:0;overflow:hidden}.openapi-updated{font-size:11px;opacity:.65;margin-left:auto}@media(max-width:1200px){.openapi-kpis{grid-template-columns:repeat(3,1fr)}}@media(max-width:800px){.openapi-controlbar{grid-template-columns:1fr}.openapi-kpis{grid-template-columns:repeat(2,1fr)}.openapi-control-actions{flex-wrap:wrap}}
    </style>
    <div class="openapi-shell">
      <div class="openapi-head"><h1>OpenAPI Center</h1><div class="openapi-status"><span class="openapi-status-dot"></span>${connected?'Connected':'Not configured'}<span class="pill">${esc(overview.mode_label||'--')}</span><span class="pill">${esc(overview.version_label||'--')}</span></div></div>
      <div class="card openapi-controlbar">
        <label class="field compact-field"><span>Connection</span><select id="openApiConnectionSelect" onchange="openApiChangeConnection(this.value)">${(overview.connections||[]).map(c=>`<option value="${escAttr(c.id)}" ${c.id===overview.connection_id?'selected':''}>${esc(c.name)}</option>`).join('')||'<option value="">No enabled connections</option>'}</select></label>
        <label class="field compact-field"><span>Project</span><select id="openApiProjectSelect" onchange="openApiChangeProject(this.value)" ${projects.length?'':'disabled'}>${projects.map(p=>`<option value="${escAttr(p.uuid||'')}" ${p.uuid===selectedProject?.uuid?'selected':''}>${esc(p.name||p.uuid||'Unnamed Project')}</option>`).join('')||'<option value="">No projects</option>'}</select></label>
        <div class="openapi-control-actions"><button id="openApiRefreshBtn" class="secondary small-btn" onclick="openApiRefreshCurrent()">Refresh</button>${hasPermission('settings')?`<button class="secondary small-btn" onclick="goModule('Settings')">Settings</button>`:''}</div>
      </div>
      <div class="openapi-kpis"><div class="openapi-kpi"><span>Projects</span><strong>${projects.length}</strong></div><div class="openapi-kpi"><span>Devices</span><strong>${deviceRows.length}</strong></div><div class="openapi-kpi"><span>Online Devices</span><strong>${onlineCount}</strong></div><div class="openapi-kpi"><span>Docks / Gateways</span><strong>${dockCount}</strong></div><div class="openapi-kpi"><span>API Requests</span><strong>${esc(stats.requests||0)}</strong></div><div class="openapi-kpi"><span>Success Rate</span><strong>${stats.requests?Math.round(((stats.success||0)/stats.requests)*100):0}%</strong></div></div>
      <div class="card openapi-main-card"><div class="openapi-tabs">${tabs.map(([key,label,supported])=>`<button data-openapi-tab="${key}" class="openapi-tab ${openApiTab===key?'active':''}" ${supported?'':`disabled title="Not supported by this connection"`} onclick="openApiSelectTab('${key}')">${label}</button>`).join('')}<span class="openapi-updated">Last updated: ${esc(lastUpdated)}</span></div><div id="openApiPanel" style="padding:14px"></div></div>
    </div>`;
  await renderOpenApiTab(false);
}

async function openApiChangeConnection(connectionId) {
  sessionStorage.setItem("aerosync_openapi_connection", connectionId || "");
  openApiResetCache(connectionId || "");
  await renderOpenApi(document.getElementById("content"), true);
}

async function openApiChangeProject(projectUuid) {
  sessionStorage.setItem(openApiProjectStorageKey(), projectUuid || "");
  const cid = openApiConnectionId();
  if (projectUuid && !openApiPageCache.devicesByProject[projectUuid]) {
    try {
      const result = await api(openApiQuery(openApiProjectQuery("/api/openapi/devices?scope=project")));
      openApiPageCache.devicesByProject[projectUuid] = openApiExtractList(result);
    } catch (_) { openApiPageCache.devicesByProject[projectUuid] = []; }
  }
  openApiPageCache.connectionId = cid;
  openApiPageCache.loadedAt = new Date().toISOString();
  await renderOpenApi(document.getElementById("content"), false);
}

async function openApiRefreshCurrent() {
  const btn = document.getElementById("openApiRefreshBtn");
  if (btn?.disabled || openApiPageLoading) return;
  if (btn) { btn.disabled = true; btn.textContent = "Refreshing..."; }
  try { await renderOpenApi(document.getElementById("content"), true); }
  finally { const b=document.getElementById("openApiRefreshBtn"); if(b){b.disabled=false;b.textContent="Refresh";} }
}

async function openApiRefreshProjects() { return openApiRefreshCurrent(); }

async function openApiSelectTab(tab) {
  const capability = openApiPageCache.overview?.capabilities?.[tab];
  if (capability && capability.supported === false) {
    const target = document.getElementById("openApiPanel");
    if (target) target.innerHTML = `<div class="card warn-card"><strong>Not supported by this connection</strong><p>This module is unavailable for ${esc(openApiPageCache.overview?.mode_label||"selected deployment")} · ${esc(openApiPageCache.overview?.version_label||"selected version")}.</p></div>`;
    return;
  }
  openApiTab = tab;
  document.querySelectorAll('[data-openapi-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.openapiTab === tab));
  await renderOpenApiTab(false);
}

function openApiEndpointsForModule(module){return (openApiPageCache.overview?.allowed_endpoints||[]).filter(e=>e.module===module);}
function openApiReadPanel(module,title){
  const endpoints=openApiEndpointsForModule(module);
  if(!endpoints.length)return `<div class="card warn-card"><strong>Not supported by this connection</strong><p>No verified read-only endpoint is enabled for this module on the selected connection. Advanced official GET testing remains available in API Explorer.</p></div>`;
  const first=endpoints[0];
  return `<div class="card"><div class="card-title-row"><div><h3>${esc(title)}</h3><p class="muted">Read-only DJI OpenAPI data. AeroSync fills project/device context automatically where possible.</p></div><span class="pill">GET only</span></div>
    <div class="openapi-simple-row">
      <label class="field"><span>Read Endpoint</span><select id="openApiModuleEndpoint" onchange="renderOpenApiSimpleInputs()">${endpoints.map(e=>`<option value="${escAttr(e.key)}">${esc(e.label)}</option>`).join('')}</select></label>
      <div id="openApiSimpleInputs" class="openapi-simple-inputs"></div>
      <button class="primary openapi-get-btn" onclick="openApiModuleGetSimple()">Get Data</button>
    </div>
    <div id="openApiModuleHint" class="openapi-endpoint-hint"></div>
    <div id="openApiModuleResult" style="margin-top:12px"></div></div>`;
}
function openApiAllProjectDevices(){
  const projectUuid=openApiSelectedProjectUuid(),rows=openApiPageCache.devicesByProject?.[projectUuid]||[],out=[];
  rows.forEach(row=>{
    const pairs=[[row?.gateway,'Dock / Gateway'],[row?.drone,'Aircraft']];
    pairs.forEach(([d,type])=>{const sn=String(d?.sn||'').trim();if(sn&&!out.some(x=>x.sn===sn))out.push({sn,name:d?.callsign||d?.device_name||d?.device_model?.name||sn,type});});
  });
  return out;
}
function openApiSelectedEndpointMeta(){const key=document.getElementById('openApiModuleEndpoint')?.value;return (openApiPageCache.overview?.allowed_endpoints||[]).find(x=>x.key===key);}
function renderOpenApiSimpleInputs(){
  const e=openApiSelectedEndpointMeta(),box=document.getElementById('openApiSimpleInputs'),hint=document.getElementById('openApiModuleHint'); if(!box)return;
  if(hint)hint.innerHTML=e?`<strong>${esc(e.path||'')}</strong><small>${e.query_hint?`Optional filters: ${esc(e.query_hint)}`:'No additional filter required'}</small>`:'';
  if(!e){box.innerHTML='';return;}
  const pathParams=e.path_params||[],parts=[];
  pathParams.forEach(param=>{
    if(param==='device_sn'){
      const devices=openApiAllProjectDevices(); parts.push(`<label class="field"><span>Device</span><select data-openapi-simple-param="device_sn">${devices.map(d=>`<option value="${escAttr(d.sn)}">${esc(d.name)} - ${esc(d.sn)}</option>`).join('')||'<option value="">No project device loaded</option>'}</select></label>`);
    }else{
      parts.push(`<label class="field"><span>${esc(param.replaceAll('_',' '))}</span><input data-openapi-simple-param="${escAttr(param)}" placeholder="${escAttr(param)}"></label>`);
    }
  });
  if(e.key==='hms'){
    const sns=openApiSelectedDeviceSns(); parts.push(`<label class="field"><span>Devices</span><input data-openapi-simple-query="device_sn_list" value="${escAttr(sns.join(','))}" readonly></label>`);
  }else if(e.query_hint){
    parts.push(`<label class="field"><span>Filters (optional)</span><input id="openApiSimpleQuery" placeholder="${escAttr(e.query_hint)}"></label>`);
  }
  box.innerHTML=parts.join('');
}
function parseOpenApiSimpleQuery(text){const out={};String(text||'').trim().split('&').forEach(part=>{if(!part)return;const i=part.indexOf('=');const k=decodeURIComponent(i>=0?part.slice(0,i):part).trim(),v=decodeURIComponent(i>=0?part.slice(i+1):'').trim();if(k)out[k]=v;});return out;}
async function openApiModuleGetSimple(){
  const endpoint=document.getElementById('openApiModuleEndpoint')?.value,box=document.getElementById('openApiModuleResult');if(!box||!endpoint)return;
  const pathParams={};document.querySelectorAll('[data-openapi-simple-param]').forEach(el=>{const k=el.dataset.openapiSimpleParam,v=el.value?.trim();if(v)pathParams[k]=v;});
  let queryParams={};document.querySelectorAll('[data-openapi-simple-query]').forEach(el=>{const k=el.dataset.openapiSimpleQuery,v=el.value?.trim();if(v)queryParams[k]=v;});
  Object.assign(queryParams,parseOpenApiSimpleQuery(document.getElementById('openApiSimpleQuery')?.value||''));
  box.innerHTML='<div class="loading-card">Loading...</div>';
  try{const u=openApiProjectQuery(`/api/openapi/explorer?endpoint=${encodeURIComponent(endpoint)}&path_params=${encodeURIComponent(JSON.stringify(pathParams))}&query_params=${encodeURIComponent(JSON.stringify(queryParams))}`),result=await api(openApiQuery(u));box.innerHTML=`<div class="card-title-row"><strong>${result.ok===false?'DJI API Error':'HTTP '+esc(result.http_status)}${result.application_code!=null?' · Code '+esc(result.application_code):''}</strong><button class="secondary small-btn" onclick="copyText(document.getElementById('openApiModuleJson').textContent)">Copy JSON</button></div>${result.ok===false&&result.error?`<div class="warn-card">${esc(result.error)}</div>`:''}<pre id="openApiModuleJson" class="json-box openapi-json-result">${esc(JSON.stringify(result.data||result,null,2))}</pre>`;}catch(err){box.innerHTML=`<div class="warn-card">${esc(err.message)}</div>`;}
}

function openApiSelectedDeviceSns(){
  const projectUuid=openApiSelectedProjectUuid();
  const rows=openApiPageCache.devicesByProject?.[projectUuid]||[];
  const sns=[];
  rows.forEach(row=>{
    [row?.gateway?.sn,row?.drone?.sn].forEach(sn=>{sn=String(sn||'').trim();if(sn&&!sns.includes(sn))sns.push(sn);});
  });
  return sns;
}
function openApiEndpointHint(selectId,targetId){
  const key=document.getElementById(selectId)?.value,e=(openApiPageCache.overview?.allowed_endpoints||[]).find(x=>x.key===key),box=document.getElementById(targetId);
  if(box)box.innerHTML=e?`<strong>${esc(e.path||'')}</strong><small>${e.path_params?.length?`Path: ${esc(e.path_params.join(', '))}`:'No path parameters'}${e.query_hint?` | Example query: ${esc(e.query_hint)}`:''}</small>`:'';
  if(key==='hms'){
    const q=document.getElementById(selectId==='openApiEndpoint'?'openApiQueryParams':'openApiModuleQueryParams');
    if(q&&(!q.value.trim()||q.value.trim()==='{}')){
      const sns=openApiSelectedDeviceSns();
      if(sns.length)q.value=JSON.stringify({device_sn_list:sns.join(',')});
    }
  }
}
async function openApiModuleCustomGet(){const path=document.getElementById('openApiModuleCustomPath')?.value?.trim(),queryParams=document.getElementById('openApiModuleQueryParams')?.value||'{}',box=document.getElementById('openApiModuleResult');if(!box||!path)return showToast('Enter the official DJI GET path','error');box.innerHTML='<div class="loading-card">Loading...</div>';try{const u=openApiProjectQuery(`/api/openapi/explorer?endpoint=custom_get&custom_path=${encodeURIComponent(path)}&query_params=${encodeURIComponent(queryParams)}`),result=await api(openApiQuery(u));box.innerHTML=`<div class="card-title-row"><strong>${result.ok===false?'DJI API Error':'HTTP '+esc(result.http_status)}${result.application_code!=null?' · Code '+esc(result.application_code):''}</strong><button class="secondary small-btn" onclick="copyText(document.getElementById('openApiModuleJson').textContent)">Copy JSON</button></div>${result.ok===false&&result.error?`<div class="warn-card">${esc(result.error)}</div>`:''}<pre id="openApiModuleJson" class="json-box openapi-json-result">${esc(JSON.stringify(result.data||result,null,2))}</pre>`;}catch(err){box.innerHTML=`<div class="warn-card">${esc(err.message)}</div>`;}}
async function openApiModuleGet(){const endpoint=document.getElementById('openApiModuleEndpoint')?.value,pathParams=document.getElementById('openApiModulePathParams')?.value||'{}',queryParams=document.getElementById('openApiModuleQueryParams')?.value||'{}',box=document.getElementById('openApiModuleResult');if(!box)return;box.innerHTML='<div class="loading-card">Loading...</div>';try{const u=openApiProjectQuery(`/api/openapi/explorer?endpoint=${encodeURIComponent(endpoint)}&path_params=${encodeURIComponent(pathParams)}&query_params=${encodeURIComponent(queryParams)}`),result=await api(openApiQuery(u));box.innerHTML=`<div class="card-title-row"><strong>${result.ok===false?'DJI API Error':'HTTP '+esc(result.http_status)}${result.application_code!=null?' · Code '+esc(result.application_code):''}</strong><button class="secondary small-btn" onclick="copyText(document.getElementById('openApiModuleJson').textContent)">Copy JSON</button></div>${result.ok===false&&result.error?`<div class="warn-card">${esc(result.error)}</div>`:''}<pre id="openApiModuleJson" class="json-box openapi-json-result">${esc(JSON.stringify(result.data||result,null,2))}</pre>`;}catch(err){box.innerHTML=`<div class="warn-card">${esc(err.message)}</div>`;}}

async function renderOpenApiTab(force = false) {
  const target = document.getElementById("openApiPanel");
  if (!target) return;
  const cache = openApiPageCache;
  const capability = cache.overview?.capabilities?.[openApiTab];
  if (capability && capability.supported === false) {
    target.innerHTML = `<div class="card warn-card"><strong>Not supported by this connection</strong><p>This module is unavailable for ${esc(cache.overview?.mode_label||"selected deployment")} · ${esc(cache.overview?.version_label||"selected version")}.</p></div>`;
    return;
  }
  if (openApiTab === "overview") {
    const o = cache.overview || {}, s = o.statistics || {};
    target.innerHTML = `<div class="card"><h3>OpenAPI Status</h3><div class="settings-two-col"><div><p><strong>Deployment:</strong> ${esc(o.mode_label||'--')}</p><p><strong>OpenAPI Version:</strong> ${esc(o.version_label||'--')}</p><p><strong>Base URL:</strong> ${esc(o.base_url||'Not configured')}</p><p><strong>Token:</strong> ${o.token_configured?'Configured':'Not configured'}</p></div><div><p><strong>Success:</strong> ${esc(s.success||0)}</p><p><strong>Failed:</strong> ${esc(s.failed||0)}</p><p><strong>Average:</strong> ${esc(s.average_ms||0)} ms</p></div></div></div>`;
    return;
  }
  if (openApiTab === "projects") {
    const rows = cache.projects || [];
    target.innerHTML = `<div class="card"><div class="card-title-row"><h3>Projects</h3><span class="pill">${rows.length}</span></div><table class="table"><thead><tr><th>Name</th><th>UUID</th><th>Created</th><th>Updated</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.name||'--')}</td><td><code>${esc(row.uuid||'--')}</code></td><td>${esc(formatDubaiTime(row.created_at))}</td><td>${esc(formatDubaiTime(row.updated_at))}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">No projects returned.</td></tr>'}</tbody></table></div>`;
    return;
  }
  if (openApiTab === "devices") {
    const projectUuid = openApiSelectedProjectUuid();
    let rows = cache.devicesByProject[projectUuid];
    if (!Array.isArray(rows)) {
      target.innerHTML = `<div class="card loading-card"><strong>Loading devices...</strong></div>`;
      try { const result=await api(openApiQuery(openApiProjectQuery("/api/openapi/devices?scope=project"))); rows=openApiExtractList(result); cache.devicesByProject[projectUuid]=rows; }
      catch(err){target.innerHTML=`<div class="card warn-card"><strong>Devices unavailable</strong><p>${esc(err.message)}</p></div>`;return;}
    }
    target.innerHTML = `<div class="card"><div class="card-title-row"><h3>Project Devices</h3><span class="pill">${rows.length}</span></div><table class="table"><thead><tr><th>Dock / Gateway</th><th>Aircraft</th><th>Dock Status</th><th>Aircraft Status</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.gateway?.callsign||row.gateway?.sn||'--')}<br><small>${esc(row.gateway?.device_model?.name||'')}</small></td><td>${esc(row.drone?.callsign||row.drone?.sn||'--')}<br><small>${esc(row.drone?.device_model?.name||'')}</small></td><td>${row.gateway?.device_online_status?'Online':'Offline'}</td><td>${row.drone?.device_online_status?'Online':'Offline'}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">No devices returned.</td></tr>'}</tbody></table></div>`;
    return;
  }
  if (["hms","livestream","tasks","waylines","maps","models"].includes(openApiTab)) {
    const labels={hms:"HMS / Thing Model",livestream:"Livestream Information",tasks:"Flight Tasks & Records",waylines:"Waylines",maps:"Maps & Airspace",models:"Models & Reconstruction"};
    target.innerHTML=openApiReadPanel(openApiTab,labels[openApiTab]);
    setTimeout(()=>renderOpenApiSimpleInputs(),0);
    return;
  }
  if (openApiTab === "explorer") {
    const endpoints = cache.overview?.allowed_endpoints || [];
    target.innerHTML = `<div class="card"><div class="card-title-row"><div><h3>Read-Only API Explorer</h3></div><span class="pill">GET only</span></div><div class="settings-two-col"><label class="field"><span>Approved Endpoint</span><select id="openApiEndpoint" onchange="openApiEndpointHint('openApiEndpoint','openApiExplorerHint')"><option value="custom_get">Custom official GET path</option>${endpoints.map(e=>`<option value="${escAttr(e.key)}">${esc(e.label)}</option>`).join('')}</select></label><div id="openApiExplorerHint" class="openapi-endpoint-hint"></div><label class="field"><span>Custom Path (optional)</span><input id="openApiCustomPath" placeholder="/openapi/v2.0/..."></label><label class="field"><span>Path Parameters (JSON)</span><input id="openApiPathParams" placeholder='{"device_sn":"..."}'></label><label class="field"><span>Query Parameters (JSON)</span><input id="openApiQueryParams" placeholder='{"page":1,"page_size":50}'></label></div><button class="primary" onclick="openApiExplorerGet()">Get Data</button><div id="openApiExplorerResult" style="margin-top:12px"></div></div>`;
    return;
  }
  if (openApiTab === "logs") {
    target.innerHTML = `<div class="card loading-card"><strong>Loading logs...</strong></div>`;
    try { const data=await api(openApiQuery("/api/openapi/logs")); const rows=data.logs||[]; target.innerHTML=`<div class="card"><div class="card-title-row"><h3>OpenAPI Logs</h3><span class="pill">${rows.length}</span></div><table class="table"><thead><tr><th>Time</th><th>Connection</th><th>Endpoint</th><th>Status</th><th>Duration</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(formatDubaiTime(row.time))}</td><td>${esc(row.connection_id||'--')}</td><td>${esc(row.endpoint)}</td><td>${esc(row.status||'Error')}</td><td>${esc(row.elapsed_ms||0)} ms</td></tr>`).join('')||'<tr><td colspan="5" class="muted">No OpenAPI requests yet.</td></tr>'}</tbody></table></div>`; }
    catch(err){target.innerHTML=`<div class="card warn-card"><strong>Logs unavailable</strong><p>${esc(err.message)}</p></div>`;}
  }
}

async function openApiExplorerGet() {
  const endpoint=document.getElementById("openApiEndpoint")?.value||"system_status", resultBox=document.getElementById("openApiExplorerResult");
  if(!resultBox)return; resultBox.innerHTML=`<div class="loading-card">Loading...</div>`;
  const p=new URLSearchParams();p.set('endpoint',endpoint);p.set('path_params',document.getElementById('openApiPathParams')?.value||'{}');p.set('query_params',document.getElementById('openApiQueryParams')?.value||'{}');if(endpoint==='custom_get'){const cp=document.getElementById('openApiCustomPath')?.value?.trim();if(!cp){resultBox.innerHTML='<div class="warn-card">Enter the official DJI GET path.</div>';return;}p.set('custom_path',cp);}
  try { const result=await api(openApiQuery(openApiProjectQuery(`/api/openapi/explorer?${p.toString()}`))); resultBox.innerHTML=`<div class="card-title-row"><strong>HTTP ${esc(result.http_status)}</strong><button class="ghost small-btn" onclick="copyText(document.getElementById('openApiJson').textContent)">Copy JSON</button></div><pre id="openApiJson" class="json-box openapi-json-result">${esc(JSON.stringify(result.data||result,null,2))}</pre>`; }
  catch(err){resultBox.innerHTML=`<div class="warn-card"><strong>Request failed</strong><p>${esc(err.message)}</p></div>`;}
}

function openApiSettingsPanel() {
  if (!hasPermission("settings")) return "";
  const module = openApiModuleFromSettings();
  const rows = module.connections || [];
  return `<div class="settings-section">
    <div class="settings-section-title"><div><h2>OpenAPI Settings</h2><p>Advanced edition. Add and manage multiple FH2 Cloud and FH2 On-Prem OpenAPI connections.</p></div><button class="secondary" type="button" onclick="openApiAddConnection()">Add Connection</button></div>
    <div id="openApiConnectionsList">
      ${rows.map((c,i) => openApiConnectionCard(c,i)).join("") || `<div class="card"><p class="muted">No OpenAPI connections configured. Click Add Connection.</p></div>`}
    </div>
    
  </div>`;
}

function openApiConnectionCard(c, index) {
  return `<div class="card settings-card-wide openapi-connection-card" data-openapi-index="${index}" style="margin-bottom:12px">
    <input type="hidden" data-openapi-field="id" value="${escAttr(c.id || "")}">
    <div class="card-title-row"><h3>${esc(c.name || `Connection ${index+1}`)}</h3><div class="toolbar-actions"><button class="secondary small-btn" type="button" onclick="testOpenApiConnection(${index})">Test</button><button class="ghost small-btn" type="button" onclick="openApiRemoveConnection(${index})">Remove</button></div></div>
    <div class="settings-two-col">
      <label class="field"><span>Connection Name</span><input data-openapi-field="name" value="${escAttr(c.name || "")}" placeholder="Dubai Cloud"></label>
      <label class="field"><span>FlightHub Type</span><select data-openapi-field="platform"><option value="cloud" ${c.platform !== "onprem" ? "selected" : ""}>FH2 Cloud</option><option value="onprem" ${c.platform === "onprem" ? "selected" : ""}>FH2 On-Prem</option></select></label>
      <label class="field"><span>OpenAPI Version</span><select data-openapi-field="api_version"><option value="v1" ${(c.api_version||"v2") === "v1" ? "selected" : ""}>V1.0</option><option value="v2" ${(c.api_version||"v2") !== "v1" ? "selected" : ""}>V2.0</option></select></label>
      <label class="field"><span>API Base URL</span><input data-openapi-field="base_url" value="${escAttr(c.base_url || "")}" placeholder="https://your-flighthub-server"></label>
      <label class="field"><span>Organization Key / X-User-Token</span><input data-openapi-field="user_token" type="password" value="${escAttr(c.user_token || "")}" placeholder="Organization Key"></label>
      <label class="field"><span>Request Timeout</span><input data-openapi-field="timeout_seconds" type="number" min="3" max="120" value="${escAttr(c.timeout_seconds || 30)}"></label>
      <label class="field"><span>Verify SSL</span><select data-openapi-field="verify_ssl"><option value="true" ${c.verify_ssl !== false ? "selected" : ""}>Enabled</option><option value="false" ${c.verify_ssl === false ? "selected" : ""}>Disabled</option></select></label>
      <label class="field"><span>Connection Status</span><select data-openapi-field="enabled"><option value="true" ${c.enabled !== false ? "selected" : ""}>Enabled</option><option value="false" ${c.enabled === false ? "selected" : ""}>Disabled</option></select></label>
    </div>
  </div>`;
}

function collectOpenApiConnections() {
  return [...document.querySelectorAll(".openapi-connection-card")].map((card, index) => {
    const get = name => card.querySelector(`[data-openapi-field="${name}"]`);
    return {
      id: get("id")?.value || `conn_${Date.now()}_${index}`,
      name: get("name")?.value?.trim() || `Connection ${index+1}`,
      platform: get("platform")?.value === "onprem" ? "onprem" : "cloud",
      api_version: get("api_version")?.value === "v1" ? "v1" : "v2",
      base_url: get("base_url")?.value?.trim() || "",
      user_token: get("user_token")?.value || "",
      timeout_seconds: Number(get("timeout_seconds")?.value || 30),
      verify_ssl: get("verify_ssl")?.value !== "false",
      enabled: get("enabled")?.value !== "false"
    };
  });
}

function openApiAddConnection() {
  const module = openApiModuleFromSettings();
  module.connections.push({id:`conn_${Date.now()}`, name:"New OpenAPI Connection", platform:"cloud", api_version:"v2", enabled:true, base_url:"", user_token:"", timeout_seconds:30, verify_ssl:true});
  settingsCache.settings.modules.openapi = module;
  renderSettings(document.getElementById("content"));
}

function openApiRemoveConnection(index) {
  const module = openApiModuleFromSettings();
  module.connections.splice(index, 1);
  settingsCache.settings.modules.openapi = module;
  renderSettings(document.getElementById("content"));
}

async function testOpenApiConnection(index) {
  try {
    await saveSettings(true);
    const connections = openApiModuleFromSettings().connections || [];
    const connectionId = Number.isInteger(index) ? (connections[index]?.id || "") : openApiConnectionId();
    const result = await api("/api/openapi/test", {method:"POST", body:JSON.stringify({connection_id:connectionId})});
    showToast(result.ok ? "OpenAPI connection successful" : (result.error || "OpenAPI connection failed"), result.ok ? "success" : "error");
  } catch (err) { showToast(`OpenAPI test failed: ${err.message}`, "error"); }
}

function renderSettings(content) {
  const cfg = settingsCache.settings;
  const standardFolders = standardStoragePreview(cfg);
  const usingStandardFolders = cfg.storage?.use_module_subfolders !== false;
  content.innerHTML = `
    <div class="page-title">
      <h1>Settings</h1>
      <button class="primary" onclick="saveSettings()">Save Settings</button>
    </div>
    <div class="settings-section">
      <div class="settings-section-title">
        <div>
          <h2>Core Setup</h2>
          <p>Choose one protected data root and keep module data in standard subfolders.</p>
        </div>
      </div>
      <div class="card settings-card-wide">
        <div class="card-title-row">
          <h3>Data Storage</h3>
          <span class="pill">${usingStandardFolders ? "Root path controls module folders" : "Manual module paths enabled"}</span>
        </div>
        <div class="settings-two-col">
          <label class="field">
            <span>Data Root Path</span>
            <input id="dataRootPath" value="${esc(cfg.storage?.data_root_path || "")}" placeholder="C:\\ProgramData\\AERO NEX\\AERO SYNC\\data">
          </label>
          <label class="field">
            <span>Storage Mode</span>
            <select id="useModuleSubfolders">
              <option value="true" ${usingStandardFolders ? "selected" : ""}>Standard Subfolders (Recommended)</option>
              <option value="false" ${!usingStandardFolders ? "selected" : ""}>Manual Module Paths</option>
            </select>
          </label>
        </div>
        
        <div class="path-preview-grid">
          ${standardFolders.map(([name, path]) => `<div><span>${esc(name)}</span><code>${esc(path)}</code></div>`).join("")}
        </div>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">
        <div>
          <h2>HTTPS Certificate</h2>
          <p>Self-signed is used by default. For production, import a CA-issued PEM certificate and matching private key.</p>
        </div>
      </div>
      <div class="card settings-card-wide">
        <div class="card-title-row"><h3>Certificate Mode</h3><span class="pill">${esc(cfg.security?.ssl_mode || "self-signed")}</span></div>
        <div class="settings-two-col">
          <label class="field">
            <span>HTTPS Certificate Mode</span>
            <select id="sslMode">
              <option value="self-signed" ${(cfg.security?.ssl_mode || "self-signed") !== "custom" ? "selected" : ""}>Self-signed Default</option>
              <option value="custom" ${cfg.security?.ssl_mode === "custom" ? "selected" : ""}>Custom Certificate</option>
            </select>
          </label>
          <div class="field">
            <span>Supported Format</span>
            <div class="hint-box">Preferred: PEM certificate chain + PEM private key. Cert: .pem, .crt, .cer. Key: .key, .pem. Private key must match the certificate.</div>
          </div>
        </div>
        <div class="settings-two-col">
          <label class="field">
            <span>Upload PEM Certificate</span>
            <input id="certUploadFile" type="file" accept=".pem,.crt,.cer">
          </label>
          <label class="field">
            <span>Upload PEM Private Key</span>
            <input id="keyUploadFile" type="file" accept=".pem,.key">
          </label>
        </div>
        <button class="secondary" type="button" onclick="importHttpsCertificate()">Import Certificate Files</button>
        <p class="muted">After changing certificate mode or importing files, restart AERO SYNC for HTTPS to use the new certificate.</p>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">
        <div>
          <h2>Network & URLs</h2>
          <p>Set service ports and the Local/WAN addresses shown to DJI FH2.</p>
        </div>
      </div>
      <div class="settings-network-layout">
        <div class="settings-left-stack">
          <div class="card settings-ports-card">
            <div class="card-title-row"><h3>Ports</h3><span class="pill">Editable</span></div>
            <div class="form-grid">
              ${Object.entries(cfg.ports).map(([key, value]) => `
                <label class="field">
                  <span>${esc(labelPort(key))}</span>
                  <input data-port="${esc(key)}" type="number" min="1" max="65535" value="${esc(value)}">
                </label>
                <div class="muted">${esc(portHint(key))}</div>
              `).join("")}
            </div>
          </div>
          <div class="settings-subtitle">
            <h2>Connection Modules</h2>
            <p>Configure the services FH2 connects to. EventAPI receiver mode does not require DJI cloud token/API key.</p>
          </div>
          <div class="settings-module-stack">
            <div class="card">
              <div class="card-title-row"><h3>EventAPI Receiver</h3><span class="pill">Webhook</span></div>
              
              <details class="advanced-settings" ${usingStandardFolders ? "" : "open"}>
                <summary>Advanced path override</summary>
                <label class="field"><span>Event DB Path</span><input id="eventDbPath" value="${esc(cfg.modules.event_receiver.event_db_path || "")}" placeholder="${esc(standardFolders.find(x => x[0] === "Events DB")?.[1] || "")}"></label>
                <label class="field"><span>Event Log Path</span><input id="eventLogPath" value="${esc(cfg.modules.event_receiver.log_path || "")}" placeholder="${esc(standardFolders.find(x => x[0] === "Event Logs")?.[1] || "")}"></label>
              </details>
            </div>
            <div class="card">
              <div class="card-title-row"><h3>MQTT</h3><span class="pill">Bridge</span></div>
              <div class="settings-two-col">
                <label class="field"><span>Broker Host</span><input id="mqttHost" value="${esc(cfg.modules.mqtt.host || "127.0.0.1")}"></label>
                <label class="field"><span>Subscribe Topic</span><input id="mqttTopic" value="${esc(cfg.modules.mqtt.topic || "#")}"></label>
                <label class="field"><span>Username</span><input id="mqttUsername" value="${esc(cfg.modules.mqtt.username || "")}"></label>
                <label class="field"><span>Password</span><input id="mqttPassword" type="password" value="${esc(cfg.modules.mqtt.password || "")}"></label>
              </div>
              <details class="advanced-settings" ${usingStandardFolders ? "" : "open"}>
                <summary>Advanced path override</summary>
                <label class="field"><span>MQTT Capture Log Path</span><input id="mqttCaptureLogPath" value="${esc(cfg.modules.mqtt.capture_log_path || "")}" placeholder="${esc(standardFolders.find(x => x[0] === "MQTT Capture")?.[1] || "")}"></label>
                <label class="field"><span>MQTT Dashboard Log Path</span><input id="mqttDashboardLogPath" value="${esc(cfg.modules.mqtt.dashboard_log_path || "")}" placeholder="${esc(standardFolders.find(x => x[0] === "MQTT Logs")?.[1] || "")}"></label>
              </details>
            </div>
            <div class="card">
              <div class="card-title-row"><h3>Local S3</h3><span class="pill">Media Upload</span></div>
              <div class="settings-two-col">
                <label class="field"><span>Bucket Type</span><input id="s3BucketType" value="${esc(cfg.modules.local_s3.bucket_type || "Self-Hosted S3 Protocol Storage")}"></label>
                <label class="field"><span>Bucket</span><input id="s3Bucket" value="${esc(cfg.modules.local_s3.bucket)}"></label>
                <label class="field"><span>Bucket AK</span><input id="s3AccessKey" value="${esc(cfg.modules.local_s3.access_key || "")}"></label>
                <label class="field"><span>Bucket SK</span><input id="s3SecretKey" type="password" value="${esc(cfg.modules.local_s3.secret_key || "")}"></label>
                <label class="field"><span>Endpoint</span><input id="s3Endpoint" value="${esc(cfg.modules.local_s3.endpoint || "")}" placeholder="http://192.168.120.26:19004"></label>
                <label class="field"><span>Region</span><input id="s3Region" value="${esc(cfg.modules.local_s3.region || "us-east-1")}"></label>
              </div>
              <label class="field"><span>Preset Path</span><input id="s3PresetPath" value="${esc(cfg.modules.local_s3.preset_path || "")}" placeholder="Optional; usually blank"></label>
              <details class="advanced-settings" ${usingStandardFolders ? "" : "open"}>
                <summary>Advanced path override</summary>
                <label class="field"><span>S3 Storage Path</span><input id="storagePath" value="${esc(cfg.modules.local_s3.storage_path)}" placeholder="${esc(standardFolders.find(x => x[0] === "S3 Storage")?.[1] || "")}"></label>
                <label class="field"><span>Local S3 Log Path</span><input id="s3LogPath" value="${esc(cfg.modules.local_s3.log_path || "")}" placeholder="${esc(standardFolders.find(x => x[0] === "S3 Logs")?.[1] || "")}"></label>
              </details>
            </div>
          </div>
        </div>
        <div class="card settings-url-card">
          <div class="card-title-row"><h3>Network</h3><span class="pill">FH2 URLs</span></div>
          <div class="settings-two-col">
            <label class="field">
              <span>Local IP</span>
              <input id="localIp" value="${esc(cfg.network.local_ip)}" placeholder="192.168.120.26" oninput="previewSettingsUrls()">
            </label>
            <label class="field">
              <span>WAN / Public IP</span>
              <input id="wanIp" value="${esc(cfg.network.wan_ip)}" placeholder="83.xxx.xxx.xxx" oninput="previewSettingsUrls()">
            </label>
          </div>
          <h3>URLs</h3>
          <div id="urlPreview">${urlBlocks(settingsCache.urls)}</div>
        </div>
      </div>
    </div>
    ${openApiSettingsPanel()}
  `;
}

function standardStoragePreview(cfg) {
  const root = cfg.storage?.data_root_path || "C:\\ProgramData\\AERO NEX\\AERO SYNC\\data";
  const join = (...parts) => [root, ...parts].join("\\").replace(/\\+/g, "\\");
  return [
    ["Events DB", join("events", "events.db")],
    ["Event Logs", join("events", "event_receiver.log")],
    ["MQTT Capture", join("mqtt", "mqtt_capture.log")],
    ["MQTT Logs", join("mqtt", "mqtt_dashboard.log")],
    ["S3 Storage", join("s3", "storage")],
    ["S3 Logs", join("s3", "local_s3.log")],
    ["Recordings", join("live_streams", "recordings")],
    ["Map Tiles", join("maps", "tiles")],
    ["Backups", join("backups")]
  ];
}

function labelPort(key) {
  return {
    dashboard_https: "Operation Center HTTPS Dashboard",
    http_redirect: "HTTP Redirect",
    event_api: "FH2 EventAPI Receiver",
    mqtt_broker: "MQTT Broker",
    local_s3: "Local S3 Receiver",
    stream_bridge: "Stream Bridge / Video Service",
    internal_api: "Internal Module API"
  }[key] || key;
}

function portHint(key) {
  return {
    dashboard_https: "Browser dashboard",
    http_redirect: "Redirect to HTTPS",
    event_api: "/dji/event",
    mqtt_broker: "FH2 MQTT",
    local_s3: "FH2 S3 endpoint",
    stream_bridge: "Video service",
    internal_api: "Internal"
  }[key] || "";
}

function previewSettingsUrls() {
  const localIp = document.getElementById("localIp")?.value.trim();
  const wanIp = document.getElementById("wanIp")?.value.trim();
  const ports = {};
  document.querySelectorAll("[data-port]").forEach(input => ports[input.dataset.port] = Number(input.value));
  const build = (ip) => ({
    dashboard: `https://${ip}:${ports.dashboard_https || 19000}`,
    event_api: `http://${ip}:${ports.event_api || 19002}/dji/event`,
    event_api_dashboard: `https://${ip}:${ports.dashboard_https || 19000}/dji/event`,
    s3: `http://${ip}:${ports.local_s3 || 19004}`,
    mqtt: `${ip}:${ports.mqtt_broker || 19003}`,
    mqtt_status: `http://${ip}:${ports.internal_api || 19006}/mqtt/device-status`,
    stream: `https://${ip}:${ports.stream_bridge || 19005}`
  });
  const urls = {
    local: localIp ? build(localIp) : null,
    wan: wanIp ? build(wanIp) : null
  };
  const box = document.getElementById("urlPreview");
  if (box) box.innerHTML = urlBlocks(urls);
}

function urlBlocks(urls) {
  const blocks = [];
  if (urls.local) {
    blocks.push(`<h4>Local FH2 Connection URLs</h4>${connectionRows(urls.local)}`);
  } else {
    blocks.push(`<h4>Local FH2 Connection URLs</h4><div class="url-empty">Set <strong>Local IP</strong> in Settings to generate EventAPI, MQTT, S3, Dashboard, and Stream URLs.</div>`);
  }
  if (urls.wan) {
    blocks.push(`<h4>WAN FH2 Connection URLs</h4>${connectionRows(urls.wan)}`);
  } else {
    blocks.push(`<h4>WAN FH2 Connection URLs</h4><div class="url-empty">Set <strong>WAN / Public IP</strong> in Settings to generate public EventAPI, MQTT, S3, Dashboard, and Stream URLs.</div>`);
  }
  return blocks.join("");
}

function connectionRows(urls) {
  const mqttStatusTemplate = '{\\"username\\": \\"${username}\\", \\"timestamp\\": ${timestamp}, \\"reason\\": \\"${reason}\\", \\"action\\": \\"${event}\\", \\"clientid\\": \\"${clientid}\\"}';
  const labels = {
    dashboard: "Operation Center Dashboard",
    event_api: "FH2 EventAPI Receiver (HTTP)",
    event_api_dashboard: "FH2 EventAPI Receiver (HTTPS Dashboard Fallback)",
    s3: "FH2 Local S3 Endpoint",
    mqtt: "FH2 MQTT Broker",
    mqtt_status: "FH2 MQTT Device Status Callback",
    stream: "Live Stream Bridge"
  };
  return Object.entries(urls).map(([k, v]) => `
    <div class="url-row">
      <div class="url-row-head">
        <strong>${esc(labels[k] || k)}</strong>
        <button class="ghost small-btn" onclick="copyText('${escAttr(v)}')">Copy</button>
      </div>
      <code>${esc(v)}</code>
      ${k === "mqtt_status" ? `
        <div class="url-note">
          <div><strong>FH2 split fields:</strong> URL <code>${esc(v.replace("/mqtt/device-status", ""))}</code> | Path <code>/mqtt/device-status</code></div>
          <div class="url-row-head">
            <strong>Body Template</strong>
            <button class="ghost small-btn" onclick="copyText('${escAttr(mqttStatusTemplate)}')">Copy</button>
          </div>
          <code>${esc(mqttStatusTemplate)}</code>
        </div>
      ` : ""}
    </div>
  `).join("");
}

function escAttr(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/\n/g, "");
}

function copyText(value) {
  navigator.clipboard?.writeText(value);
  showToast("Copied");
}

function showToast(message, type = "success") {
  let toast = document.getElementById("toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    document.body.appendChild(toast);
  }
  toast.className = `toast show ${type}`;
  toast.textContent = message;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.classList.remove("show");
  }, 2400);
}

async function testEmail() {
  try {
    await saveEmailSettings();
    const email = settingsCache.settings.modules.email || {};
    const template = (email.templates || [])[0] || {};
    const result = await api("/api/email/test", {
      method: "POST",
      body: JSON.stringify({
        to: template.to,
        from: template.from_address || email.from_addresses,
        cc: template.cc,
        bcc: template.bcc,
        subject: template.subject || "Operation Center Test Email",
        body: "Operation Center test email sent successfully."
      })
    });
    showToast(`Test email sent to ${(result.result?.to || []).join(", ") || "recipient"}`);
  } catch (err) {
    showToast(`Email failed: ${err.message}`, "error");
  }
}

async function saveEmailSettings() {
  const current = settingsCache.settings.modules.email || {};
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {
        modules: {
          email: {
            ...current,
            enabled: document.getElementById("emailEnabled")?.value === "true",
            smtp_host: document.getElementById("emailHost")?.value || "",
            smtp_port: Number(document.getElementById("emailPort")?.value || 587),
            security: document.getElementById("emailSecurity")?.value || "starttls",
            username: document.getElementById("emailUsername")?.value || "",
            password: document.getElementById("emailPassword")?.value || "",
            from_addresses: document.getElementById("emailFrom")?.value || "",
            templates: collectEmailTemplates()
          }
        }
      }
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  renderEmail(document.getElementById("content"));
  showToast("Email settings saved");
}

async function saveSettings(silent = false) {
  const ports = {};
  document.querySelectorAll("[data-port]").forEach(input => ports[input.dataset.port] = Number(input.value));
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: {
        storage: {
          data_root_path: document.getElementById("dataRootPath")?.value || "",
          use_module_subfolders: document.getElementById("useModuleSubfolders")?.value !== "false"
        },
        ports,
        network: {
          local_ip: document.getElementById("localIp").value,
          wan_ip: document.getElementById("wanIp").value
        },
        security: {
          ssl_mode: document.getElementById("sslMode")?.value || "self-signed",
          custom_cert_path: settingsCache?.security?.custom_cert_path || "",
          custom_key_path: settingsCache?.security?.custom_key_path || ""
        },
        modules: {
          event_receiver: {
            event_db_path: document.getElementById("eventDbPath")?.value || "",
            log_path: document.getElementById("eventLogPath")?.value || ""
          },
          mqtt: {
            host: document.getElementById("mqttHost")?.value || "127.0.0.1",
            username: document.getElementById("mqttUsername")?.value || "",
            password: document.getElementById("mqttPassword")?.value || "",
            topic: document.getElementById("mqttTopic")?.value || "#",
            capture_log_path: document.getElementById("mqttCaptureLogPath")?.value || "",
            dashboard_log_path: document.getElementById("mqttDashboardLogPath")?.value || ""
          },
          local_s3: {
            bucket_type: document.getElementById("s3BucketType")?.value || "Self-Hosted S3 Protocol Storage",
            storage_path: document.getElementById("storagePath")?.value || "",
            bucket: document.getElementById("s3Bucket")?.value || "",
            access_key: document.getElementById("s3AccessKey")?.value || "",
            secret_key: document.getElementById("s3SecretKey")?.value || "",
            endpoint: document.getElementById("s3Endpoint")?.value || "",
            region: document.getElementById("s3Region")?.value || "us-east-1",
            preset_path: document.getElementById("s3PresetPath")?.value || "",
            log_path: document.getElementById("s3LogPath")?.value || ""
          },
          openapi: (() => {
            const current = openApiModuleFromSettings();
            const connections = collectOpenApiConnections();
            const active = current.active_connection_id && connections.some(c => c.id === current.active_connection_id)
              ? current.active_connection_id
              : (connections.find(c => c.enabled)?.id || "");
            return {...current, enabled:true, active_connection_id:active, connections};
          })()
        }
      }
    })
  });
  settingsCache = {settings: data.settings, urls: data.urls};
  renderSettings(document.getElementById("content"));
  if (!silent) showToast("Settings saved");
}

function readTextFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("File read failed"));
    reader.readAsText(file);
  });
}

async function importHttpsCertificate() {
  const certFile = document.getElementById("certUploadFile")?.files?.[0];
  const keyFile = document.getElementById("keyUploadFile")?.files?.[0];
  if (!certFile || !keyFile) {
    showToast("Select both certificate and private key PEM files", "error");
    return;
  }
  try {
    const [cert, key] = await Promise.all([readTextFile(certFile), readTextFile(keyFile)]);
    const data = await api("/api/certificate/import", {
      method: "POST",
      body: JSON.stringify({cert, key})
    });
    settingsCache = {settings: data.settings, urls: data.urls};
    renderSettings(document.getElementById("content"));
    showToast(data.message || "Certificate imported. Restart required.");
  } catch (err) {
    showToast(`Certificate import failed: ${err.message}`, "error");
  }
}

async function renderHelp(content) {
  const help = await api("/api/help");
  content.innerHTML = `
    <div class="page-title"><h1>Help</h1></div>
    <div class="card">
      <h3>Default Ports</h3>
      <table class="table">
        <tbody>
          ${Object.entries(help.ports).map(([k,v]) => `<tr><td>${esc(labelPort(k))}</td><td>${esc(v)}</td><td>${esc(portHint(k))}</td></tr>`).join("")}
        </tbody>
      </table>
    </div>
    <div style="height:16px"></div>
    <div class="card">
      <h3>Notes</h3>
      ${help.notes.map(n => `<p>${esc(n)}</p>`).join("")}
    </div>
  `;
}

function showInfo() {
  alert("AERO SYNC | Designed & Developed by AERO NEX FZCO | 漏 2025 Aero Nex FZCO. All Rights Reserved. | Contact us : Support@aeronex.ae");
}

function forgotPassword() {
  alert("Forgot password\\n\\nPlease contact the administrator or AERO NEX support to reset the account.");
}

function toggleCompact() {
  document.querySelector(".app-shell")?.classList.toggle("compact");
}

async function logout() {
  await api("/api/logout", {method: "POST"});
  location.reload();
}

boot().catch(err => {
  app.innerHTML = `<div class="login-shell"><div class="login-card"><h1>Error</h1><p>${esc(err.message)}</p></div></div>`;
});
























