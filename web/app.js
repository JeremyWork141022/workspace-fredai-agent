const TEXT_INLINE_LIMIT = 1024 * 1024;
const DEFAULT_BINARY_INLINE_LIMIT = 6 * 1024 * 1024;
const MOCK_MODE = new URLSearchParams(window.location.search).get("mock") === "1";

const TEXT_EXTENSIONS = new Set([
  ".txt",
  ".md",
  ".csv",
  ".log",
  ".json",
  ".xml",
  ".yaml",
  ".yml",
  ".toml",
  ".ini",
  ".cfg",
]);

const SUPPORTED_EXTENSIONS = new Set([
  ...TEXT_EXTENSIONS,
  ".tsv",
  ".xlsx",
  ".docx",
  ".pptx",
  ".rtf",
  ".html",
  ".htm",
  ".sql",
]);

const state = {
  busy: false,
  abortController: null,
  sessionId: "",
  lastRequestId: "",
  messages: [],
  attachments: [],
  attachmentConfig: {
    inlineBase64: false,
    maxInlineBytes: 0,
    acceptedExtensions: Array.from(SUPPORTED_EXTENSIONS),
  },
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
  scrollToBottomButton: document.querySelector("#scrollToBottomButton"),
  chatForm: document.querySelector("#chatForm"),
  composerShell: document.querySelector("#composerShell"),
  messageInput: document.querySelector("#messageInput"),
  fileInput: document.querySelector("#fileInput"),
  attachButton: document.querySelector("#attachButton"),
  attachmentList: document.querySelector("#attachmentList"),
  sendButton: document.querySelector("#sendButton"),
  stopButton: document.querySelector("#stopButton"),
  turnMeta: document.querySelector("#turnMeta"),
  newSessionButton: document.querySelector("#newSessionButton"),
  copySessionButton: document.querySelector("#copySessionButton"),
};

function createId(prefix) {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return `${prefix}_${window.crypto.randomUUID().replaceAll("-", "")}`;
  }
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function getExtension(name) {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function normalizeExtension(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return "";
  return text.startsWith(".") ? text : `.${text}`;
}

function classifyFile(file) {
  const extension = getExtension(file.name);
  if (file.type.startsWith("image/")) return "image";
  if (extension === ".pdf") return "pdf";
  if (extension === ".doc" || extension === ".docx" || extension === ".rtf") return "document";
  if (extension === ".ppt" || extension === ".pptx") return "presentation";
  if (extension === ".xls" || extension === ".xlsx" || extension === ".csv" || extension === ".tsv") {
    return "spreadsheet";
  }
  if (TEXT_EXTENSIONS.has(extension) || file.type.startsWith("text/")) return "text";
  return "file";
}

function isTextLike(file) {
  return TEXT_EXTENSIONS.has(getExtension(file.name)) || file.type.startsWith("text/");
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function setTurnMeta(text, tone = "") {
  el.turnMeta.textContent = text || "";
  el.turnMeta.className = tone ? `turn-meta ${tone}` : "turn-meta";
}

function isNearBottom() {
  const distance = el.messages.scrollHeight - el.messages.scrollTop - el.messages.clientHeight;
  return distance <= 80 || el.messages.scrollHeight <= el.messages.clientHeight;
}

function scrollToBottom(behavior = "auto") {
  el.messages.scrollTo({ top: el.messages.scrollHeight, behavior });
  updateScrollButton();
}

function updateScrollButton() {
  el.scrollToBottomButton.classList.toggle("hidden", isNearBottom());
}

function autoResizeInput() {
  el.messageInput.style.height = "auto";
  el.messageInput.style.height = `${Math.min(el.messageInput.scrollHeight, 220)}px`;
}

function updateComposerControls() {
  const hasText = el.messageInput.value.trim().length > 0;
  const hasAttachments = state.attachments.length > 0;
  const canSend = !state.busy && (hasText || hasAttachments);
  el.sendButton.disabled = !canSend;
  el.attachButton.disabled = state.busy;
  el.messageInput.disabled = state.busy;
  el.stopButton.classList.toggle("hidden", !state.busy);
  el.sendButton.classList.toggle("hidden", state.busy);
}

function render(options = {}) {
  const shouldPin = options.forceScroll || isNearBottom();
  renderMessages();
  renderAttachments();
  updateComposerControls();
  if (shouldPin) requestAnimationFrame(() => scrollToBottom(options.smooth ? "smooth" : "auto"));
  updateScrollButton();
}

function renderMessages() {
  el.messages.innerHTML = "";

  if (state.messages.length === 0) {
    const empty = document.createElement("section");
    empty.className = "empty-state";

    const title = document.createElement("h2");
    title.textContent = "Where should we begin?";
    empty.appendChild(title);

    const hint = document.createElement("p");
    hint.textContent = "Ask FredAI about your workspace, or attach a file for analysis.";
    empty.appendChild(hint);

    el.messages.appendChild(empty);
    return;
  }

  for (const message of state.messages) {
    el.messages.appendChild(renderMessage(message));
  }
}

function renderMessage(message) {
  const article = document.createElement("article");
  article.className = `message ${message.role} ${message.status || ""}`.trim();
  article.dataset.messageId = message.id;

  if (message.role === "user" && message.attachments?.length) {
    article.appendChild(renderMessageAttachments(message.attachments));
  }

  const content = document.createElement("div");
  content.className = "message-content";
  if (message.role === "assistant") {
    renderMarkdown(content, message.text || "");
    if (message.status === "running") {
      const indicator = document.createElement("span");
      indicator.className = "typing-indicator";
      indicator.textContent = "Working";
      content.appendChild(indicator);
    }
  } else {
    content.textContent = message.text || "";
  }
  article.appendChild(content);

  const actions = renderMessageActions(message);
  if (actions) article.appendChild(actions);

  const meta = renderMessageMeta(message);
  if (meta) article.appendChild(meta);

  return article;
}

function renderMessageAttachments(attachments) {
  const list = document.createElement("div");
  list.className = "message-attachments";
  for (const attachment of attachments) {
    const chip = document.createElement("div");
    chip.className = "message-attachment";
    const name = document.createElement("span");
    name.textContent = attachment.name;
    const size = document.createElement("small");
    size.textContent = `${attachment.kind || "file"} | ${formatBytes(attachment.size)}`;
    chip.append(name, size);
    list.appendChild(chip);
  }
  return list;
}

function renderMessageActions(message) {
  if (message.status === "running") return null;

  const actions = document.createElement("div");
  actions.className = "message-actions";

  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy";
  copy.dataset.action = "copy-message";
  copy.dataset.messageId = message.id;
  actions.appendChild(copy);

  if (message.role === "user") {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.dataset.action = "edit-message";
    edit.dataset.messageId = message.id;
    actions.appendChild(edit);
  }

  if (message.role === "assistant" && message.status !== "error") {
    const regenerate = document.createElement("button");
    regenerate.type = "button";
    regenerate.textContent = "Regenerate";
    regenerate.dataset.action = "regenerate-message";
    regenerate.dataset.messageId = message.id;
    actions.appendChild(regenerate);
  }

  return actions;
}

function renderMessageMeta(message) {
  const hasMeta =
    message.status === "error" ||
    message.meta?.requestId ||
    message.meta?.durationMs !== undefined ||
    message.meta?.toolNames?.length ||
    message.meta?.progressMessages?.length;

  if (!hasMeta) return null;

  const meta = document.createElement("div");
  meta.className = "meta";

  const parts = [];
  if (message.meta?.durationMs !== undefined) parts.push(`${message.meta.durationMs} ms`);
  if (message.status && !["complete", "running"].includes(message.status)) parts.push(message.status);
  if (message.meta?.toolNames?.length) parts.push(`tools: ${message.meta.toolNames.join(", ")}`);
  if (parts.length) {
    const summary = document.createElement("span");
    summary.textContent = parts.join(" | ");
    meta.appendChild(summary);
  }

  if (message.meta?.requestId) {
    const trace = document.createElement("a");
    trace.href = `/agent/traces/${encodeURIComponent(message.meta.requestId)}`;
    trace.target = "_blank";
    trace.rel = "noreferrer";
    trace.className = "trace-link";
    trace.textContent = "trace";
    meta.appendChild(trace);
  }

  if (message.meta?.progressMessages?.length) {
    const details = document.createElement("details");
    details.className = "progress-details";
    const summary = document.createElement("summary");
    summary.textContent = "progress";
    const list = document.createElement("ul");
    for (const item of message.meta.progressMessages) {
      const li = document.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    }
    details.append(summary, list);
    meta.appendChild(details);
  }

  return meta;
}

function renderMarkdown(container, text) {
  const lines = String(text || "").split(/\r?\n/);
  let inCode = false;
  let codeLines = [];
  let list = null;

  const closeList = () => {
    if (list) {
      container.appendChild(list);
      list = null;
    }
  };

  const closeCode = () => {
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = codeLines.join("\n");
    pre.appendChild(code);
    container.appendChild(pre);
    codeLines = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");

    if (line.trim().startsWith("```")) {
      if (inCode) {
        inCode = false;
        closeCode();
      } else {
        closeList();
        inCode = true;
        codeLines = [];
      }
      continue;
    }

    if (inCode) {
      codeLines.push(rawLine);
      continue;
    }

    if (!line.trim()) {
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = String(Math.min(heading[1].length + 2, 5));
      const node = document.createElement(`h${level}`);
      node.textContent = heading[2];
      container.appendChild(node);
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      if (!list || list.tagName !== "UL") {
        closeList();
        list = document.createElement("ul");
      }
      const li = document.createElement("li");
      li.textContent = bullet[1];
      list.appendChild(li);
      continue;
    }

    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (ordered) {
      if (!list || list.tagName !== "OL") {
        closeList();
        list = document.createElement("ol");
      }
      const li = document.createElement("li");
      li.textContent = ordered[1];
      list.appendChild(li);
      continue;
    }

    closeList();
    const paragraph = document.createElement("p");
    paragraph.textContent = line;
    container.appendChild(paragraph);
  }

  closeList();
  if (inCode) closeCode();
}

function renderAttachments() {
  el.attachmentList.innerHTML = "";
  el.attachmentList.classList.toggle("empty", state.attachments.length === 0);

  for (const attachment of state.attachments) {
    const chip = document.createElement("div");
    chip.className = `attachment-chip ${attachment.status || "ready"}`;

    if (attachment.previewUrl) {
      const img = document.createElement("img");
      img.src = attachment.previewUrl;
      img.alt = "";
      chip.appendChild(img);
    }

    const label = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = attachment.name;
    const meta = document.createElement("span");
    meta.textContent = `${attachment.kind} | ${formatBytes(attachment.size)} | ${attachment.transferLabel}`;
    label.append(name, meta);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.dataset.action = "remove-attachment";
    remove.dataset.attachmentId = attachment.id;

    chip.append(label, remove);
    el.attachmentList.appendChild(chip);
  }
}

async function refreshHealth() {
  if (MOCK_MODE) {
    el.connectionStatus.textContent = "UI mock mode";
    el.connectionStatus.className = "";
    el.modelName.textContent = "mock-ui";
    el.toolCount.textContent = "4";
    state.attachmentConfig.inlineBase64 = true;
    state.attachmentConfig.maxInlineBytes = DEFAULT_BINARY_INLINE_LIMIT;
    state.attachmentConfig.acceptedExtensions = Array.from(SUPPORTED_EXTENSIONS);
    return;
  }

  try {
    const res = await fetch("/health");
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();

    el.connectionStatus.textContent = "Server online";
    el.connectionStatus.className = "";
    el.modelName.textContent = data.model || "-";
    el.toolCount.textContent = String((data.memory?.tools || data.tools || []).length || "-");

    const attachmentCaps = data.attachment_capabilities || data.capabilities?.attachments || {};
    state.attachmentConfig.inlineBase64 =
      attachmentCaps.inline_base64 === true || attachmentCaps.inlineBinary === true;
    state.attachmentConfig.maxInlineBytes =
      Number(attachmentCaps.max_inline_bytes || attachmentCaps.maxInlineBytes || 0) ||
      (state.attachmentConfig.inlineBase64 ? DEFAULT_BINARY_INLINE_LIMIT : 0);
    if (Array.isArray(attachmentCaps.accepted_extensions)) {
      state.attachmentConfig.acceptedExtensions = attachmentCaps.accepted_extensions
        .map(normalizeExtension)
        .filter(Boolean);
    }
  } catch (err) {
    el.connectionStatus.textContent = "Server offline";
    el.connectionStatus.className = "offline";
  }
}

async function sendMessage(event) {
  event.preventDefault();
  await sendCurrentComposer();
}

async function sendCurrentComposer() {
  if (state.busy) return;

  let message = el.messageInput.value.trim();
  if (!message && state.attachments.length > 0) {
    message = "Please analyze the attached file(s).";
  }
  if (!message) return;

  const displayAttachments = state.attachments.map(toDisplayAttachment);
  const payloadAttachments = state.attachments.map(toPayloadAttachment);
  state.attachments = [];
  el.messageInput.value = "";
  autoResizeInput();

  const userMessage = {
    id: createId("msg"),
    role: "user",
    status: "complete",
    text: message,
    attachments: displayAttachments,
    payloadAttachments,
    createdAt: new Date().toISOString(),
  };

  state.messages.push(userMessage);
  await runAssistantRequest(userMessage, { forceScroll: true });
}

async function runAssistantRequest(userMessage, options = {}) {
  state.busy = true;
  state.abortController = new AbortController();
  setTurnMeta("Waiting for FredAI", "busy");

  const assistantMessage = {
    id: createId("msg"),
    role: "assistant",
    status: "running",
    text: "",
    meta: {},
    createdAt: new Date().toISOString(),
  };

  state.messages.push(assistantMessage);
  render({ forceScroll: options.forceScroll ?? true, smooth: true });

  const payload = {
    workspace_id: el.workspaceId.value.trim() || "default",
    user_id: el.userId.value.trim() || "unknown",
    session_id: el.sessionId.value.trim() || null,
    message: userMessage.text,
    attachments: userMessage.payloadAttachments || [],
  };

  try {
    if (MOCK_MODE) {
      await completeMockAssistantResponse(userMessage, assistantMessage);
    } else {
      const res = await fetch("/agent/respond", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: state.abortController.signal,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);

      state.sessionId = data.session_id || "";
      state.lastRequestId = data.request_id || "";
      el.sessionId.value = state.sessionId;
      el.lastRequest.textContent = state.lastRequestId || "-";

      assistantMessage.status = data.status === "success" ? "complete" : "error";
      assistantMessage.text = data.answer || "";
      assistantMessage.meta = {
        requestId: data.request_id,
        durationMs: data.duration_ms,
        toolNames: data.tool_names || [],
        progressMessages: data.progress_messages || [],
      };
    }
  } catch (err) {
    if (err.name === "AbortError") {
      assistantMessage.status = "cancelled";
      assistantMessage.text = "Request stopped in the browser.";
    } else {
      assistantMessage.status = "error";
      assistantMessage.text = `Request failed: ${err.message}`;
    }
  } finally {
    state.busy = false;
    state.abortController = null;
    setTurnMeta("");
    render({ forceScroll: true });
    el.messageInput.focus();
  }
}

async function completeMockAssistantResponse(userMessage, assistantMessage) {
  const started = performance.now();
  await waitForMockDelay(700, state.abortController.signal);

  const attachments = userMessage.payloadAttachments || [];
  const attachmentLines = attachments.length
    ? attachments.map((attachment) => {
        const transfer = attachment.transfer || "metadata_only";
        return `- ${attachment.name} (${attachment.type}, ${attachment.extension || "no extension"}, ${transfer})`;
      })
    : ["- No attachments were included."];

  const inlineCount = attachments.filter((attachment) =>
    ["inline_text", "inline_base64"].includes(attachment.transfer),
  ).length;
  const metadataOnlyCount = attachments.filter((attachment) => attachment.transfer === "metadata_only").length;

  state.sessionId = state.sessionId || el.sessionId.value.trim() || `mock_sess_${Date.now()}`;
  state.lastRequestId = `mock_req_${Date.now()}`;
  el.sessionId.value = state.sessionId;
  el.lastRequest.textContent = state.lastRequestId;

  assistantMessage.status = "complete";
  assistantMessage.text = [
    "### UI mock response",
    "",
    "This is a local browser-only response. It proves the ChatGPT-style UI is working without calling FredAI.",
    "",
    "I received:",
    "",
    `> ${userMessage.text}`,
    "",
    "Attachments seen by the UI:",
    "",
    ...attachmentLines,
    "",
    "Backend compatibility notes:",
    "",
    `- Inline attachments ready for parsing: ${inlineCount}`,
    `- Metadata-only attachments needing backend upload/path handling: ${metadataOnlyCount}`,
    "- Real memory, orchestration, tool calls, and file parsing still happen behind /agent/respond on your work computer.",
    "",
    "```json",
    JSON.stringify(
      {
        workspace_id: el.workspaceId.value.trim() || "default",
        user_id: el.userId.value.trim() || "unknown",
        session_id: state.sessionId,
        attachment_count: attachments.length,
      },
      null,
      2,
    ),
    "```",
  ].join("\n");
  assistantMessage.meta = {
    requestId: state.lastRequestId,
    durationMs: Math.round(performance.now() - started),
    toolNames: ["mock_memory_router", "mock_file_router", "mock_trace_writer"],
    progressMessages: [
      "Mock mode enabled by ?mock=1.",
      "Skipped /agent/respond so FredAI credentials are not needed.",
      "Rendered the same message, attachment, metadata, and action UI used in real mode.",
    ],
  };
}

function waitForMockDelay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      rejectAbort();
      return;
    }
    const timeout = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timeout);
        rejectAbort();
      },
      { once: true },
    );

    function rejectAbort() {
      const err = new Error("Aborted");
      err.name = "AbortError";
      reject(err);
    }
  });
}

function stopRequest() {
  if (state.abortController) state.abortController.abort();
}

function toDisplayAttachment(attachment) {
  return {
    id: attachment.id,
    name: attachment.name,
    size: attachment.size,
    kind: attachment.kind,
    extension: attachment.extension,
    media_type: attachment.media_type,
    transfer: attachment.transfer,
    previewUrl: attachment.previewUrl,
  };
}

function toPayloadAttachment(attachment) {
  const payload = {
    id: attachment.id,
    name: attachment.name,
    size: attachment.size,
    type: attachment.kind,
    media_type: attachment.media_type,
    content_type: attachment.media_type,
    extension: attachment.extension,
    transfer: attachment.transfer,
    last_modified: attachment.lastModified,
  };

  if (attachment.text) payload.text = attachment.text;
  if (attachment.data_base64) {
    payload.encoding = "base64";
    payload.data_base64 = attachment.data_base64;
  }
  if (attachment.transfer === "metadata_only") {
    payload.note =
      "The browser did not send file bytes. Backend should ask for a workspace path, upload endpoint, or enable inline_base64 capability.";
  }

  return payload;
}

async function addFiles(files) {
  const accepted = [];
  const configuredExtensions = new Set([
    ...SUPPORTED_EXTENSIONS,
    ...state.attachmentConfig.acceptedExtensions,
  ]);
  for (const file of files) {
    const extension = getExtension(file.name);
    const allowed = configuredExtensions.has(extension) || file.type.startsWith("text/");
    if (!allowed) {
      setTurnMeta(`Skipped unsupported file: ${file.name}`, "warn");
      continue;
    }
    accepted.push(file);
  }

  for (const file of accepted) {
    const attachment = await prepareAttachment(file);
    state.attachments.push(attachment);
  }

  if (accepted.length) setTurnMeta("");
  render({ forceScroll: false });
  el.messageInput.focus();
}

async function prepareAttachment(file) {
  const extension = getExtension(file.name);
  const kind = classifyFile(file);
  const mediaType = file.type || inferMediaType(extension);
  const attachment = {
    id: createId("att"),
    file,
    name: file.name,
    size: file.size,
    kind,
    extension,
    media_type: mediaType,
    lastModified: file.lastModified,
    transfer: "metadata_only",
    transferLabel: "metadata",
    status: "ready",
    previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
  };

  if (isTextLike(file) && file.size <= TEXT_INLINE_LIMIT) {
    attachment.text = await file.text();
    attachment.transfer = "inline_text";
    attachment.transferLabel = "text";
    return attachment;
  }

  if (
    state.attachmentConfig.inlineBase64 &&
    file.size <= state.attachmentConfig.maxInlineBytes
  ) {
    attachment.data_base64 = await readFileAsBase64(file);
    attachment.transfer = "inline_base64";
    attachment.transferLabel = "file bytes";
    return attachment;
  }

  if (isTextLike(file) && file.size > TEXT_INLINE_LIMIT) {
    attachment.transferLabel = "text too large";
  } else if (!state.attachmentConfig.inlineBase64) {
    attachment.transferLabel = "needs backend upload";
  } else {
    attachment.transferLabel = "too large";
  }
  return attachment;
}

function inferMediaType(extension) {
  const lookup = {
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  };
  return lookup[extension] || "application/octet-stream";
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.onerror = () => reject(reader.error || new Error("Could not read file."));
    reader.readAsDataURL(file);
  });
}

function removeAttachment(id) {
  const attachment = state.attachments.find((item) => item.id === id);
  if (attachment?.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
  state.attachments = state.attachments.filter((item) => item.id !== id);
  render();
}

async function copyMessage(id) {
  const message = state.messages.find((item) => item.id === id);
  if (!message) return;
  await navigator.clipboard.writeText(message.text || "");
  setTurnMeta("Message copied");
  setTimeout(() => {
    if (!state.busy) setTurnMeta("");
  }, 1200);
}

function editMessage(id) {
  if (state.busy) return;
  const index = state.messages.findIndex((item) => item.id === id);
  const message = state.messages[index];
  if (!message || message.role !== "user") return;
  state.messages = state.messages.slice(0, index);
  el.messageInput.value = message.text || "";
  autoResizeInput();
  setTurnMeta(message.attachments?.length ? "Reattach files before sending the edited message." : "");
  render({ forceScroll: true });
  el.messageInput.focus();
}

async function regenerateMessage(id) {
  if (state.busy) return;
  const index = state.messages.findIndex((item) => item.id === id);
  const assistantMessage = state.messages[index];
  if (!assistantMessage || assistantMessage.role !== "assistant") return;

  const userMessage = [...state.messages.slice(0, index)].reverse().find((item) => item.role === "user");
  if (!userMessage) return;

  state.messages = state.messages.slice(0, index);
  await runAssistantRequest(userMessage, { forceScroll: true });
}

function newSession() {
  state.sessionId = "";
  state.lastRequestId = "";
  state.messages = [];
  for (const attachment of state.attachments) {
    if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
  }
  state.attachments = [];
  el.sessionId.value = "";
  el.lastRequest.textContent = "-";
  setTurnMeta("");
  render({ forceScroll: true });
  el.messageInput.focus();
}

async function copySession() {
  const value = el.sessionId.value.trim();
  if (!value) return;
  await navigator.clipboard.writeText(value);
  setTurnMeta("Session copied");
  setTimeout(() => {
    if (!state.busy) setTurnMeta("");
  }, 1200);
}

function handleMessageClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  if (action === "copy-message") copyMessage(button.dataset.messageId);
  if (action === "edit-message") editMessage(button.dataset.messageId);
  if (action === "regenerate-message") regenerateMessage(button.dataset.messageId);
}

function handleAttachmentClick(event) {
  const button = event.target.closest("[data-action='remove-attachment']");
  if (!button) return;
  removeAttachment(button.dataset.attachmentId);
}

function handleComposerDrag(event) {
  event.preventDefault();
  if (state.busy) return;
  el.composerShell.classList.add("dragging");
}

function clearComposerDrag() {
  el.composerShell.classList.remove("dragging");
}

el.chatForm.addEventListener("submit", sendMessage);
el.stopButton.addEventListener("click", stopRequest);
el.newSessionButton.addEventListener("click", newSession);
el.copySessionButton.addEventListener("click", copySession);
el.scrollToBottomButton.addEventListener("click", () => scrollToBottom("smooth"));
el.messages.addEventListener("scroll", updateScrollButton);
el.messages.addEventListener("click", handleMessageClick);
el.attachmentList.addEventListener("click", handleAttachmentClick);
el.attachButton.addEventListener("click", () => el.fileInput.click());
el.fileInput.addEventListener("change", () => {
  addFiles(Array.from(el.fileInput.files || []));
  el.fileInput.value = "";
});
el.messageInput.addEventListener("input", () => {
  autoResizeInput();
  updateComposerControls();
});
el.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    el.chatForm.requestSubmit();
  }
});
el.messageInput.addEventListener("compositionstart", () => {
  el.messageInput.dataset.composing = "true";
});
el.messageInput.addEventListener("compositionend", () => {
  delete el.messageInput.dataset.composing;
});
el.composerShell.addEventListener("dragenter", handleComposerDrag);
el.composerShell.addEventListener("dragover", handleComposerDrag);
el.composerShell.addEventListener("dragleave", clearComposerDrag);
el.composerShell.addEventListener("drop", (event) => {
  event.preventDefault();
  clearComposerDrag();
  if (state.busy) return;
  addFiles(Array.from(event.dataTransfer?.files || []));
});

autoResizeInput();
render({ forceScroll: true });
refreshHealth();
