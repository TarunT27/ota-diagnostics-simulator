const API_ROOT = "/api/v1";
const STAGES = ["precheck", "downloading", "validating", "installing", "verifying", "completed"];
const FAULT_NOTES = Object.freeze({
  none: "Run with no injected faults.",
  low_voltage: "Fail the precheck with an unsafe supply voltage.",
  connectivity_loss: "Drop connectivity during package download.",
  checksum_mismatch: "Tamper with payload bytes before SHA-256 validation.",
  install_failure: "Fail installation and trigger automatic rollback.",
});

const state = { controllers: [], summary: null, selectedId: "telematics", events: [], busy: false };
const byId = (id) => document.getElementById(id);

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || !payload.success) throw new Error(payload.error?.message || "Request failed.");
  return payload.data;
}

async function refresh() {
  const [summary, controllers] = await Promise.all([
    request(`${API_ROOT}/summary`),
    request(`${API_ROOT}/controllers`),
  ]);
  state.summary = summary;
  state.controllers = controllers;
  if (!controllers.some((controller) => controller.id === state.selectedId)) {
    state.selectedId = controllers[0]?.id || null;
  }
  state.events = state.selectedId
    ? await request(`${API_ROOT}/events?controller_id=${encodeURIComponent(state.selectedId)}&limit=100`)
    : [];
  render();
}

function render() {
  renderSummary();
  renderControllers();
  renderDetail();
  renderTimeline();
}

function renderSummary() {
  if (!state.summary) return;
  byId("total-count").textContent = state.summary.total_controllers;
  byId("healthy-count").textContent = state.summary.healthy_controllers;
  byId("attention-count").textContent = state.summary.attention_controllers;
  byId("dtc-count").textContent = state.summary.active_dtcs;
}

function renderControllers() {
  const rows = byId("controller-rows");
  rows.replaceChildren(...state.controllers.map((controller) => {
    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.dataset.controllerId = controller.id;
    row.className = controller.id === state.selectedId ? "selected" : "";
    row.setAttribute("aria-selected", String(controller.id === state.selectedId));
    row.innerHTML = `
      <td><span class="table-ecu-icon" aria-hidden="true">${controller.name.slice(0, 1)}</span><strong>${escapeHtml(controller.name)}</strong></td>
      <td>${escapeHtml(controller.current_version)}</td>
      <td><span class="status-dot ${controller.health}"></span>${capitalize(controller.health)}</td>
      <td>${stageLabel(controller.stage)}</td>
      <td>${controller.voltage.toFixed(1)} V</td>
      <td><span class="signal ${controller.connectivity}"><i></i><i></i><i></i><i></i></span><span class="sr-only">${escapeHtml(controller.connectivity)}</span></td>`;
    row.addEventListener("click", () => selectController(controller.id));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectController(controller.id); }
    });
    return row;
  }));
  byId("fleet-count").textContent = `Showing 1 to ${state.controllers.length} of ${state.controllers.length} ECUs`;
}

function renderDetail() {
  const controller = selectedController();
  if (!controller) return;
  byId("selected-name").textContent = controller.name;
  byId("selected-health").innerHTML = `<span class="status-dot ${controller.health}"></span>${capitalize(controller.health)}`;
  byId("current-version").textContent = `Current version: ${controller.current_version}`;
  byId("selected-voltage").textContent = `${controller.voltage.toFixed(1)} V`;
  byId("selected-connectivity").textContent = capitalize(controller.connectivity);
  byId("selected-last-seen").textContent = controller.last_seen;
  byId("selected-id").textContent = controller.id.toUpperCase();
  byId("rollback-button").disabled = !controller.previous_version || state.busy;
  byId("update-button").disabled = state.busy;
  byId("heading-update-button").disabled = state.busy;
  byId("reset-button").disabled = state.busy;
  renderDtcs(controller.active_dtcs);
}

function renderDtcs(dtcs) {
  const list = byId("dtc-list");
  if (!dtcs.length) {
    list.innerHTML = '<p class="empty-state success-empty">No active diagnostic trouble codes.</p>';
    return;
  }
  list.replaceChildren(...dtcs.map((dtc) => {
    const item = document.createElement("article");
    item.className = "dtc-item";
    item.innerHTML = `<strong>${escapeHtml(dtc.code)}</strong><h4>${escapeHtml(dtc.title)}</h4><p>${escapeHtml(dtc.detail)}</p>`;
    return item;
  }));
}

function renderTimeline() {
  const controller = selectedController();
  byId("timeline-title").textContent = controller ? `Event timeline (${controller.name})` : "Event timeline";
  byId("event-count").textContent = `${state.events.length} ${state.events.length === 1 ? "event" : "events"}`;
  let operationStart = 0;
  state.events.forEach((event, index) => {
    if (event.code === "UPDATE_STARTED") operationStart = index;
  });
  const operationEvents = state.events.slice(operationStart);
  const reached = new Set(operationEvents.map((event) => event.stage));
  const failureStageByCode = Object.freeze({ B1101: 0, U0100: 1, P0606: 2, U3000: 3 });
  const failedEvent = operationEvents.find((event) => Object.hasOwn(failureStageByCode, event.code));
  const failedIndex = failedEvent ? failureStageByCode[failedEvent.code] : -1;
  byId("stage-progress").replaceChildren(...STAGES.map((stage, index) => {
    const item = document.createElement("li");
    const isReached = reached.has(stage);
    const isFailed = index === failedIndex;
    item.className = isFailed ? "failed" : isReached ? "complete" : "pending";
    item.innerHTML = `<span>${isReached && !isFailed ? "✓" : index + 1}</span><strong>${stageLabel(stage)}</strong>`;
    return item;
  }));
  const log = byId("event-log");
  if (!state.events.length) {
    log.innerHTML = '<p class="empty-state">No update events for this controller yet.</p>';
    return;
  }
  log.replaceChildren(...state.events.slice().reverse().map((event) => {
    const item = document.createElement("div");
    item.className = `event-entry ${event.severity}`;
    const time = new Date(event.timestamp).toLocaleTimeString([], { hour12: false });
    item.innerHTML = `<time>${time}</time><strong>${escapeHtml(event.code)}</strong><span>${escapeHtml(event.message)}</span>`;
    return item;
  }));
  if (byId("auto-scroll").checked) log.scrollTop = 0;
}

async function selectController(controllerId) {
  state.selectedId = controllerId;
  state.events = await request(`${API_ROOT}/events?controller_id=${encodeURIComponent(controllerId)}&limit=100`);
  render();
}

async function performAction(action, successMessage) {
  if (state.busy) return;
  state.busy = true;
  renderDetail();
  try {
    await action();
    await refresh();
    showToast(successMessage, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    state.busy = false;
    renderDetail();
  }
}

byId("update-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const controller = selectedController();
  if (!controller) return;
  const targetVersion = byId("target-version").value.trim();
  const fault = byId("fault-scenario").value;
  performAction(
    () => request(`${API_ROOT}/controllers/${encodeURIComponent(controller.id)}/updates`, {
      method: "POST", body: JSON.stringify({ target_version: targetVersion, fault }),
    }),
    fault === "none" ? `Updated ${controller.name} to ${targetVersion}.` : `Completed ${capitalize(fault.replaceAll("_", " "))} scenario.`,
  );
});

byId("rollback-button").addEventListener("click", () => {
  const controller = selectedController();
  if (!controller) return;
  performAction(
    () => request(`${API_ROOT}/controllers/${encodeURIComponent(controller.id)}/rollback`, { method: "POST" }),
    `Rolled back ${controller.name}.`,
  );
});

byId("heading-update-button").addEventListener("click", () => {
  byId("update-form").requestSubmit();
});

byId("reset-button").addEventListener("click", () => performAction(
  () => request(`${API_ROOT}/reset`, { method: "POST" }),
  "Lab reset to the deterministic baseline.",
));

byId("fault-scenario").addEventListener("change", (event) => {
  byId("fault-note").textContent = FAULT_NOTES[event.target.value];
});

byId("export-button").addEventListener("click", () => {
  if (!state.events.length) {
    showToast("There are no selected-controller events to export.", "error");
    return;
  }
  const controller = selectedController();
  const blob = new Blob([JSON.stringify(state.events, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${controller?.id || "fleet"}-diagnostic-events.json`;
  link.click();
  URL.revokeObjectURL(url);
  showToast("Diagnostic event log exported.", "success");
});

function selectedController() { return state.controllers.find((item) => item.id === state.selectedId); }
function capitalize(value) { return value.charAt(0).toUpperCase() + value.slice(1); }
function stageLabel(value) { return capitalize(value.replaceAll("_", " ")); }
function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[character]);
}
function showToast(message, type) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.className = `toast visible ${type}`;
  window.setTimeout(() => { toast.className = "toast"; }, 3500);
}

refresh().catch((error) => showToast(`Could not load the lab: ${error.message}`, "error"));
