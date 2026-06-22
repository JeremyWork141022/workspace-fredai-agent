const state = {
  busy: false,
  sessionId: "",
  lastRequestId: "",
};

const el = {
  connectionStatus: document.querySelector("#connectionStatus"),
  modelName: document.querySelector("#modelName"),
  toolCount: document.querySelector("#toolCount"),
  lastRequest: document.querySelector("#lastRequest"),
  workspaceId: document.querySelector("#workspaceId"),
  userId: document.querySelector("#userId"),
  sessionId: document.querySelector("#sessionId"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  turnMeta: document.querySelector("#turnMeta"),
  newSessionButton: document.querySelector("#newSessionButton"),
  copySessionButton: document.querySelector("#copySessionButton"),
};

function setBusy(value) {
  state.busy = value;
  el.sendButton.disabled = value;
  el.messageInput.disabled = value;
  el.turnMeta.textContent = value ? "Waiting for FredAI" : "";
  el.turnMeta.className = value ? "turn-meta busy" : "turn-meta";
}

function renderEmpty() {
  if (el.messages.children.length > 0) return;
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = "Start a session by sending a workspace request.";
  el.messages.appendChild(empty);
}

function clearEmpty() {
  const empty = el.messages.querySelector(".empty");
  if (empty) empty.remove();
}

function addMessage(role, text, meta = {}) {
  clearEmpty();
  const node = document.createElement("article");
  node.className = `message ${role}${meta.error ? " error" : ""}`;
  const body = document.createElement("div");
  body.textContent = text || "";
  node.appendChild(body);

  const metaParts = [];
  if (meta.status) metaParts.push(`status: ${meta.status}`);
  if (meta.duration_ms !== undefined) metaParts.push(`${meta.duration_ms} ms`);
  if (meta.tools && meta.tools.length) metaParts.push(`tools: ${meta.tools.join(", ")}`);
  if (meta.request_id) {
    const trace = document.createElement("a");
    trace.href = `/agent/traces/${encodeURIComponent(meta.request_id)}`;
    trace.target = "_blank";
    trace.rel = "noreferrer";
    trace.className = "trace-link";
    trace.textContent = "trace";
    const metaNode = document.createElement("div");
    metaNode.className = "meta";
    if (metaParts.length) {
      const span = document.createElement("span");
      span.textContent = metaParts.join(" | ");
      metaNode.appendChild(span);
    }
    metaNode.appendChild(trace);
    node.appendChild(metaNode);
  } else if (metaParts.length) {
    const metaNode = document.createElement("div");
    metaNode.className = "meta";
    metaNode.textContent = metaParts.join(" | ");
    node.appendChild(metaNode);
  }

  el.messages.appendChild(node);
  el.messages.scrollTop = el.messages.scrollHeight;
}

async function refreshHealth() {
  try {
    const res = await fetch("/health");
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    el.connectionStatus.textContent = "Server online";
    el.connectionStatus.className = "";
    el.modelName.textContent = data.model || "-";
    el.toolCount.textContent = String((data.memory?.tools || []).length);
  } catch (err) {
    el.connectionStatus.textContent = "Server offline";
    el.connectionStatus.className = "offline";
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.busy) return;
  const message = el.messageInput.value.trim();
  if (!message) return;

  const payload = {
    workspace_id: el.workspaceId.value.trim() || "default",
    user_id: el.userId.value.trim() || "unknown",
    session_id: el.sessionId.value.trim() || null,
    message,
    attachments: [],
  };

  addMessage("user", message);
  el.messageInput.value = "";
  setBusy(true);

  try {
    const res = await fetch("/agent/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    state.sessionId = data.session_id || "";
    state.lastRequestId = data.request_id || "";
    el.sessionId.value = state.sessionId;
    el.lastRequest.textContent = state.lastRequestId || "-";
    addMessage("agent", data.answer, {
      status: data.status,
      duration_ms: data.duration_ms,
      tools: data.tool_names || [],
      request_id: data.request_id,
      error: data.status !== "success",
    });
  } catch (err) {
    addMessage("agent", `Request failed: ${err.message}`, { error: true });
  } finally {
    setBusy(false);
    el.messageInput.focus();
  }
}

function newSession() {
  state.sessionId = "";
  state.lastRequestId = "";
  el.sessionId.value = "";
  el.lastRequest.textContent = "-";
  el.messages.innerHTML = "";
  renderEmpty();
  el.messageInput.focus();
}

async function copySession() {
  const value = el.sessionId.value.trim();
  if (!value) return;
  await navigator.clipboard.writeText(value);
  el.turnMeta.textContent = "Session copied";
  setTimeout(() => {
    if (!state.busy) el.turnMeta.textContent = "";
  }, 1200);
}

el.chatForm.addEventListener("submit", sendMessage);
el.newSessionButton.addEventListener("click", newSession);
el.copySessionButton.addEventListener("click", copySession);
el.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    el.chatForm.requestSubmit();
  }
});

renderEmpty();
refreshHealth();
