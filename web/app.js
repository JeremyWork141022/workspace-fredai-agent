const TEXT_INLINE_LIMIT = 1024 * 1024;
const DEFAULT_BINARY_INLINE_LIMIT = 6 * 1024 * 1024;
const QUERY = new URLSearchParams(window.location.search);
const MOCK_MODE = QUERY.get("mock") === "1";
const INITIAL_SESSION_ID = QUERY.get("session") || QUERY.get("thread") || "";
const INITIAL_SHARE_FROM = QUERY.get("from") || "";
const INITIAL_SHARE_TO = QUERY.get("to") || "";
const SHARED_WORKSPACE_ID = "shared_workspace";
const SHARED_USER_ID = "shared";
const MOCK_THREADS_KEY = "workspace-fredai-mock-threads-v1";
const NEW_THREAD_TITLE = "Ask me anything about CRT Analytics";
const EMPTY_STATE_TITLE = "What should CRT Analytic Agent help with?";
const EMPTY_STATE_HINT = "Ask me about CRT Analytics.";

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
  ".pdf",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".bmp",
  ".webp",
  ".tif",
  ".tiff",
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
  threads: [],
  loadingThreadId: "",
  renamingThreadId: "",
  renameDraftValue: "",
  renameSelectPending: false,
  draftThread: null,
  messages: [],
  attachments: [],
  selectedShareIds: new Set(),
  activeShareRange: null,
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
  currentThreadTitle: document.querySelector("#currentThreadTitle"),
  threadList: document.querySelector("#threadList"),
  workspaceId: document.querySelector("#workspaceId"),
  userId: document.querySelector("#userId"),
  sessionId: document.querySelector("#sessionId"),
  shareBar: document.querySelector("#shareBar"),
  shareCount: document.querySelector("#shareCount"),
  copyShareButton: document.querySelector("#copyShareButton"),
  clearShareButton: document.querySelector("#clearShareButton"),
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

function extensionFromMimeType(mediaType) {
  const lookup = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "application/pdf": ".pdf",
  };
  return lookup[String(mediaType || "").toLowerCase()] || "";
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

function normalizeTitle(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 120);
}

function fallbackTitle(session) {
  return normalizeTitle(session?.title) || NEW_THREAD_TITLE;
}

function draftThreadTitle() {
  return state.draftThread ? fallbackTitle(state.draftThread) : NEW_THREAD_TITLE;
}

function currentThreadTitle() {
  if (state.draftThread) return draftThreadTitle();
  if (!state.sessionId) return NEW_THREAD_TITLE;
  const thread = state.threads.find((item) => item.id === state.sessionId);
  return fallbackTitle(thread || { title: state.messages.find((message) => message.role === "user")?.text });
}

function formatThreadTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function threadGroupLabel(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "Earlier";
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const time = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  if (time >= startOfToday) return "Today";
  if (time >= startOfToday - 86400000) return "Yesterday";
  return "Earlier";
}

function getMessageShareId(message) {
  return String(message?.serverId || message?.id || "");
}

function selectedMessagesInOrder() {
  return state.messages.filter((message) => state.selectedShareIds.has(getMessageShareId(message)));
}

function isMessageInActiveShareRange(message) {
  if (!state.activeShareRange) return false;
  const ids = state.messages.map(getMessageShareId);
  const current = getMessageShareId(message);
  const fromIndex = ids.indexOf(String(state.activeShareRange.from));
  const toIndex = ids.indexOf(String(state.activeShareRange.to));
  const currentIndex = ids.indexOf(current);
  if (fromIndex < 0 || toIndex < 0 || currentIndex < 0) return false;
  const start = Math.min(fromIndex, toIndex);
  const end = Math.max(fromIndex, toIndex);
  return currentIndex >= start && currentIndex <= end;
}

function buildThreadUrl(sessionId, range = null) {
  const url = new URL(window.location.href);
  url.search = "";
  if (MOCK_MODE) url.searchParams.set("mock", "1");
  if (sessionId) url.searchParams.set("session", sessionId);
  if (range?.from) url.searchParams.set("from", String(range.from));
  if (range?.to) url.searchParams.set("to", String(range.to));
  return url;
}

function replaceThreadUrl(sessionId, range = null) {
  window.history.replaceState({}, "", buildThreadUrl(sessionId, range));
}

async function copyTextToClipboard(text, fallbackPrompt = "Copy this share link:") {
  const value = String(text || "");
  if (!value) return false;

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";
  textarea.style.width = "1px";
  textarea.style.height = "1px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);

  try {
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    if (document.execCommand("copy")) return true;
  } catch (error) {
    console.warn("Fallback clipboard copy failed.", error);
  } finally {
    document.body.removeChild(textarea);
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await Promise.race([
        navigator.clipboard.writeText(value),
        new Promise((_, reject) => {
          window.setTimeout(() => reject(new Error("Clipboard API timed out")), 750);
        }),
      ]);
      return true;
    }
  } catch (error) {
    console.warn("Clipboard API copy failed.", error);
  }

  window.prompt(fallbackPrompt, value);
  return false;
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
  el.copySessionButton.disabled = !state.sessionId;
  el.stopButton.classList.toggle("hidden", !state.busy);
  el.sendButton.classList.toggle("hidden", state.busy);
}

function render(options = {}) {
  const shouldPin = !options.preserveScroll && (options.forceScroll || isNearBottom());
  el.currentThreadTitle.textContent = currentThreadTitle();
  renderThreads();
  renderMessages();
  renderAttachments();
  renderShareBar();
  updateComposerControls();
  if (shouldPin) requestAnimationFrame(() => scrollToBottom(options.smooth ? "smooth" : "auto"));
  updateScrollButton();
}

function renderThreads() {
  el.threadList.innerHTML = "";
  const threadsForDisplay = state.draftThread
    ? [state.draftThread, ...state.threads.filter((thread) => thread.id !== state.draftThread.id)]
    : state.threads;

  if (!threadsForDisplay.length) {
    const empty = document.createElement("p");
    empty.className = "thread-empty";
    empty.textContent = MOCK_MODE ? "Mock threads will appear here." : "No shared threads yet.";
    el.threadList.appendChild(empty);
    return;
  }

  const grouped = new Map();
  for (const thread of threadsForDisplay) {
    const label = threadGroupLabel(thread.updated_at || thread.created_at);
    if (!grouped.has(label)) grouped.set(label, []);
    grouped.get(label).push(thread);
  }

  for (const [label, threads] of grouped.entries()) {
    const groupLabel = document.createElement("div");
    groupLabel.className = "thread-group-label";
    groupLabel.textContent = label;
    el.threadList.appendChild(groupLabel);

    for (const thread of threads) {
      const item = document.createElement("div");
      const isActive = thread.id === state.sessionId || thread.id === state.draftThread?.id;
      item.className = `thread-item${isActive ? " active" : ""}${thread.isDraft ? " draft" : ""}`;

      if (thread.id === state.renamingThreadId) {
        const editor = document.createElement("div");
        editor.className = "thread-rename-editor";

        const input = document.createElement("input");
        input.className = "thread-rename-input";
        input.value = thread.id === state.renamingThreadId ? state.renameDraftValue : fallbackTitle(thread);
        input.maxLength = 120;
        input.dataset.sessionId = thread.id;
        input.placeholder = "Name this thread";
        input.setAttribute("aria-label", "Rename thread");

        const save = document.createElement("button");
        save.type = "button";
        save.dataset.action = "save-rename";
        save.dataset.sessionId = thread.id;
        save.textContent = "Save";

        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.dataset.action = "cancel-rename";
        cancel.dataset.sessionId = thread.id;
        cancel.textContent = "Cancel";

        editor.append(input, save, cancel);
        item.appendChild(editor);
        el.threadList.appendChild(item);
        requestAnimationFrame(() => {
          input.focus();
          if (state.renameSelectPending) {
            input.select();
            state.renameSelectPending = false;
          } else {
            const end = input.value.length;
            input.setSelectionRange(end, end);
          }
        });
        continue;
      }

      const open = document.createElement("button");
      open.type = "button";
      open.className = "thread-open";
      open.dataset.action = "open-thread";
      open.dataset.sessionId = thread.id;
      const title = document.createElement("span");
      title.className = "thread-title";
      title.textContent = fallbackTitle(thread);
      const time = document.createElement("span");
      time.className = "thread-time";
      time.textContent = formatThreadTime(thread.updated_at || thread.created_at);
      open.append(title, time);

      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "thread-rename";
      rename.title = "Rename thread";
      rename.setAttribute("aria-label", `Rename ${fallbackTitle(thread)}`);
      rename.dataset.action = "rename-thread";
      rename.dataset.sessionId = thread.id;
      rename.textContent = "Rename";

      item.append(open, rename);
      el.threadList.appendChild(item);
    }
  }
}

function renderShareBar() {
  const count = state.selectedShareIds.size;
  el.shareBar.classList.toggle("hidden", count === 0);
  el.shareCount.textContent =
    count === 1 ? "1 selected" : `${count} selected`;
  el.copyShareButton.disabled = count === 0 || !state.sessionId;
}

function renderMessages() {
  el.messages.innerHTML = "";

  if (state.messages.length === 0) {
    const empty = document.createElement("section");
    empty.className = "empty-state";

    const title = document.createElement("h2");
    title.textContent = EMPTY_STATE_TITLE;
    empty.appendChild(title);

    const hint = document.createElement("p");
    hint.textContent = EMPTY_STATE_HINT;
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
  const shareId = getMessageShareId(message);
  const classes = ["message", message.role, message.status || ""];
  if (state.selectedShareIds.has(shareId)) classes.push("share-selected");
  if (isMessageInActiveShareRange(message)) classes.push("shared-highlight");
  article.className = classes.filter(Boolean).join(" ");
  article.dataset.messageId = message.id;
  article.dataset.shareId = shareId;

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
      const label = document.createElement("span");
      label.textContent = "Working";
      indicator.appendChild(label);
      const dots = document.createElement("span");
      dots.className = "typing-dots";
      dots.setAttribute("aria-hidden", "true");
      for (let index = 0; index < 3; index += 1) {
        const dot = document.createElement("span");
        dot.textContent = ".";
        dots.appendChild(dot);
      }
      indicator.appendChild(dots);
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

  const select = document.createElement("button");
  select.type = "button";
  select.textContent = state.selectedShareIds.has(getMessageShareId(message)) ? "Selected" : "Select";
  select.dataset.action = "toggle-share-message";
  select.dataset.messageId = message.id;
  actions.appendChild(select);

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

function loadMockThreads() {
  try {
    const parsed = JSON.parse(localStorage.getItem(MOCK_THREADS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveMockThreads(threads) {
  localStorage.setItem(MOCK_THREADS_KEY, JSON.stringify(threads.slice(0, 100)));
}

function persistCurrentMockThread() {
  if (!MOCK_MODE || !state.sessionId || !state.messages.length) return;
  const threads = loadMockThreads().filter((thread) => thread.id !== state.sessionId);
  const now = new Date().toISOString();
  const firstUserMessage = state.messages.find((message) => message.role === "user");
  const existingTitle =
    normalizeTitle(state.draftThread?.title) ||
    state.threads.find((thread) => thread.id === state.sessionId)?.title ||
    normalizeTitle(firstUserMessage?.text) ||
    NEW_THREAD_TITLE;
  threads.unshift({
    id: state.sessionId,
    workspace_id: SHARED_WORKSPACE_ID,
    user_id: SHARED_USER_ID,
    title: existingTitle,
    created_at: state.messages[0]?.createdAt || now,
    updated_at: now,
    messages: state.messages.map((message) => ({
      id: message.id,
      serverId: getMessageShareId(message),
      role: message.role,
      status: message.status,
      text: message.text,
      createdAt: message.createdAt,
      meta: message.meta || {},
      attachments: message.attachments || [],
    })),
  });
  saveMockThreads(threads);
  state.threads = normalizeThreads(threads);
}

function normalizeThreads(sessions) {
  return (sessions || [])
    .map((session) => ({
      id: String(session.id || session.session_id || ""),
      workspace_id: session.workspace_id || SHARED_WORKSPACE_ID,
      user_id: session.user_id || SHARED_USER_ID,
      title: fallbackTitle(session),
      created_at: session.created_at || "",
      updated_at: session.updated_at || session.created_at || "",
      messages: session.messages || [],
    }))
    .filter((session) => session.id)
    .sort((a, b) => {
      const aTime = new Date(a.updated_at || a.created_at || 0).getTime();
      const bTime = new Date(b.updated_at || b.created_at || 0).getTime();
      return bTime - aTime;
    });
}

async function refreshThreads(options = {}) {
  if (state.renamingThreadId && !options.force) return;

  if (MOCK_MODE) {
    state.threads = normalizeThreads(loadMockThreads());
    renderThreads();
    return;
  }

  try {
    const res = await fetch("/agent/sessions?limit=100");
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    state.threads = normalizeThreads(data.sessions || []);
    renderThreads();
  } catch {
    setTurnMeta("Could not refresh thread list.", "warn");
  }
}

function mapServerMessage(message) {
  const metadata = message.metadata || {};
  const storedStatus = String(metadata.status || "complete");
  const status = storedStatus === "success" ? "complete" : storedStatus;
  return {
    id: `db_${message.id}`,
    serverId: String(message.id),
    role: message.role,
    status: ["error", "cancelled"].includes(status) ? status : "complete",
    text: message.text || "",
    createdAt: message.created_at || new Date().toISOString(),
    meta: {
      durationMs: metadata.request_duration_ms,
      toolNames: metadata.tool_names || [],
      progressMessages: metadata.progress_messages || [],
    },
  };
}

function mapMockMessage(message) {
  return {
    id: message.id || createId("msg"),
    serverId: String(message.serverId || message.id || createId("mock_msg")),
    role: message.role,
    status: message.status || "complete",
    text: message.text || "",
    createdAt: message.createdAt || new Date().toISOString(),
    meta: message.meta || {},
    attachments: message.attachments || [],
  };
}

async function loadThread(sessionId, options = {}) {
  if (!sessionId || state.busy) return;
  state.loadingThreadId = sessionId;
  state.draftThread = null;
  state.renamingThreadId = "";
  state.renameDraftValue = "";
  state.renameSelectPending = false;
  setTurnMeta("Loading thread", "busy");
  renderThreads();

  try {
    if (MOCK_MODE) {
      const thread = loadMockThreads().find((item) => item.id === sessionId);
      if (!thread) throw new Error("thread not found");
      state.sessionId = thread.id;
      state.messages = (thread.messages || []).map(mapMockMessage);
      state.threads = normalizeThreads(loadMockThreads());
    } else {
      const res = await fetch(`/agent/sessions/${encodeURIComponent(sessionId)}?limit=1000`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      state.sessionId = data.session.id;
      state.messages = (data.messages || []).map(mapServerMessage);
      await refreshThreads();
    }

    state.selectedShareIds.clear();
    state.activeShareRange = options.range || null;
    state.lastRequestId = "";
    el.sessionId.value = state.sessionId;
    el.lastRequest.textContent = "-";
    replaceThreadUrl(state.sessionId, state.activeShareRange);
    setTurnMeta("");
    render({
      forceScroll: !state.activeShareRange,
      preserveScroll: Boolean(state.activeShareRange),
    });
    if (state.activeShareRange) scrollToSharedRange();
  } catch (err) {
    setTurnMeta(`Could not load thread: ${err.message}`, "warn");
  } finally {
    state.loadingThreadId = "";
    renderThreads();
  }
}

async function renameThread(sessionId) {
  const thread = state.threads.find((item) => item.id === sessionId);
  if (!thread) return;
  state.renamingThreadId = sessionId;
  state.renameDraftValue = fallbackTitle(thread);
  state.renameSelectPending = true;
  renderThreads();
}

async function saveThreadRename(sessionId) {
  const input = el.threadList.querySelector(`.thread-rename-input[data-session-id="${CSS.escape(sessionId)}"]`);
  const thread =
    state.draftThread?.id === sessionId
      ? state.draftThread
      : state.threads.find((item) => item.id === sessionId);
  const draftValue = input ? input.value : state.renameDraftValue;
  const title = normalizeTitle(draftValue || "") || NEW_THREAD_TITLE;
  if (!thread || !title) {
    state.renamingThreadId = "";
    state.renameDraftValue = "";
    state.renameSelectPending = false;
    renderThreads();
    return;
  }

  if (thread.isDraft) {
    state.draftThread = {
      ...thread,
      title,
      updated_at: new Date().toISOString(),
    };
    state.renamingThreadId = "";
    state.renameDraftValue = "";
    state.renameSelectPending = false;
    render();
    return;
  }

  if (title === fallbackTitle(thread)) {
    state.renamingThreadId = "";
    state.renameDraftValue = "";
    state.renameSelectPending = false;
    renderThreads();
    return;
  }

  if (MOCK_MODE) {
    const threads = loadMockThreads();
    const target = threads.find((item) => item.id === sessionId);
    if (target) {
      target.title = title;
      target.updated_at = new Date().toISOString();
      saveMockThreads(threads);
      state.threads = normalizeThreads(threads);
      state.renamingThreadId = "";
      state.renameDraftValue = "";
      state.renameSelectPending = false;
      render();
    }
    return;
  }

  try {
    await updateThreadTitleOnServer(sessionId, title);
    state.renamingThreadId = "";
    state.renameDraftValue = "";
    state.renameSelectPending = false;
    await refreshThreads();
    render();
  } catch (err) {
    setTurnMeta(`Could not rename thread: ${err.message}`, "warn");
  }
}

async function updateThreadTitleOnServer(sessionId, title) {
  const res = await fetch(`/agent/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function cancelThreadRename() {
  state.renamingThreadId = "";
  state.renameDraftValue = "";
  state.renameSelectPending = false;
  renderThreads();
}

function scrollToSharedRange() {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const first = state.messages.find((message) => isMessageInActiveShareRange(message));
      if (!first) return;
      const target = el.messages.querySelector(`[data-message-id="${CSS.escape(first.id)}"]`);
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
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
  state.selectedShareIds.clear();
  state.activeShareRange = null;
  el.messageInput.value = "";
  autoResizeInput();

  const userMessage = {
    id: createId("msg"),
    serverId: "",
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
  const draftTitleForNewSession = state.draftThread && !state.sessionId ? draftThreadTitle() : "";
  state.busy = true;
  state.abortController = new AbortController();
  setTurnMeta("Waiting for CRT Analytics", "busy");

  const assistantMessage = {
    id: createId("msg"),
    serverId: "",
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
      if (data.user_message_id) userMessage.serverId = String(data.user_message_id);
      if (data.assistant_message_id) assistantMessage.serverId = String(data.assistant_message_id);

      assistantMessage.status = data.status === "success" ? "complete" : "error";
      assistantMessage.text = data.answer || "";
      assistantMessage.meta = {
        requestId: data.request_id,
        durationMs: data.duration_ms,
        toolNames: data.tool_names || [],
        progressMessages: data.progress_messages || [],
      };

      if (draftTitleForNewSession && state.sessionId) {
        try {
          await updateThreadTitleOnServer(state.sessionId, draftTitleForNewSession);
        } catch (renameErr) {
          console.warn("Could not apply draft thread title", renameErr);
        }
      }
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
    if (MOCK_MODE) persistCurrentMockThread();
    if (draftTitleForNewSession && state.sessionId) {
      state.draftThread = null;
      state.renamingThreadId = "";
    }
    await refreshThreads();
    setTurnMeta("");
    if (state.sessionId) replaceThreadUrl(state.sessionId);
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
  userMessage.serverId = userMessage.serverId || userMessage.id;
  assistantMessage.serverId = assistantMessage.serverId || assistantMessage.id;

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
    const extension = getExtension(file.name) || extensionFromMimeType(file.type);
    const allowed =
      configuredExtensions.has(extension) ||
      file.type.startsWith("image/") ||
      file.type.startsWith("text/") ||
      file.type === "application/pdf";
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

function pastedImageName(mediaType, index) {
  const extension = extensionFromMimeType(mediaType) || ".png";
  const timestamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "");
  const suffix = index > 1 ? `-${index}` : "";
  return `screenshot-${timestamp}${suffix}${extension}`;
}

async function handleComposerPaste(event) {
  if (state.busy) return;

  const items = Array.from(event.clipboardData?.items || []);
  const imageFiles = [];
  for (const item of items) {
    if (item.kind !== "file" || !item.type.startsWith("image/")) continue;
    const blob = item.getAsFile();
    if (!blob) continue;
    const name = blob.name || pastedImageName(blob.type, imageFiles.length + 1);
    imageFiles.push(new File([blob], name, { type: blob.type || "image/png", lastModified: Date.now() }));
  }

  if (!imageFiles.length) return;
  event.preventDefault();
  await addFiles(imageFiles);
  setTurnMeta(imageFiles.length === 1 ? "Screenshot attached" : `${imageFiles.length} screenshots attached`);
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
  const copied = await copyTextToClipboard(message.text || "", "Copy this message:");
  setTurnMeta(copied ? "Message copied" : "Copy box opened");
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

function newSession() {
  state.sessionId = "";
  state.lastRequestId = "";
  state.draftThread = {
    id: createId("draft"),
    workspace_id: SHARED_WORKSPACE_ID,
    user_id: SHARED_USER_ID,
    title: NEW_THREAD_TITLE,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    isDraft: true,
  };
  state.renamingThreadId = state.draftThread.id;
  state.renameDraftValue = "";
  state.renameSelectPending = false;
  state.messages = [];
  state.selectedShareIds.clear();
  state.activeShareRange = null;
  for (const attachment of state.attachments) {
    if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
  }
  state.attachments = [];
  el.sessionId.value = "";
  el.lastRequest.textContent = "-";
  replaceThreadUrl("");
  setTurnMeta("");
  render({ forceScroll: true });
}

async function copySession() {
  const value = el.sessionId.value.trim();
  if (!value) return;
  const url = buildThreadUrl(value).toString();
  const copied = await copyTextToClipboard(url);
  setTurnMeta(copied ? "Thread share link ready to paste" : "Copy box opened");
  setTimeout(() => {
    if (!state.busy) setTurnMeta("");
  }, 1200);
}

function toggleShareMessage(id) {
  const message = state.messages.find((item) => item.id === id);
  if (!message) return;
  const shareId = getMessageShareId(message);
  if (state.selectedShareIds.has(shareId)) {
    state.selectedShareIds.delete(shareId);
  } else {
    state.selectedShareIds.add(shareId);
  }
  render({ forceScroll: false });
}

async function copyShareLink() {
  const selected = selectedMessagesInOrder();
  if (!selected.length || !state.sessionId) return;
  const range = {
    from: getMessageShareId(selected[0]),
    to: getMessageShareId(selected[selected.length - 1]),
  };
  const url = buildThreadUrl(state.sessionId, range).toString();
  const title = currentThreadTitle();
  state.activeShareRange = range;
  replaceThreadUrl(state.sessionId, range);
  render({ preserveScroll: true });
  scrollToSharedRange();
  const copied = await copyTextToClipboard(
    [
      "Please look at this CRT Analytics thread excerpt:",
      url,
      "",
      `Thread: ${title}`,
    ].join("\n"),
    "Copy this selected-chat share link:",
  );
  setTurnMeta(copied ? "Selected chat share link ready to paste" : "Copy box opened");
  setTimeout(() => {
    if (!state.busy) setTurnMeta("");
  }, 1400);
}

function clearShareSelection() {
  state.selectedShareIds.clear();
  state.activeShareRange = null;
  if (state.sessionId) replaceThreadUrl(state.sessionId);
  render({ forceScroll: false });
}

function handleMessageClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  if (action === "toggle-share-message") toggleShareMessage(button.dataset.messageId);
  if (action === "copy-message") copyMessage(button.dataset.messageId);
  if (action === "edit-message") editMessage(button.dataset.messageId);
}

function handleThreadListClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const sessionId = button.dataset.sessionId || "";
  if (action === "open-thread") loadThread(sessionId);
  if (action === "rename-thread") renameThread(sessionId);
  if (action === "save-rename") saveThreadRename(sessionId);
  if (action === "cancel-rename") cancelThreadRename();
}

function handleThreadListKeydown(event) {
  const input = event.target.closest(".thread-rename-input");
  if (!input) return;
  if (event.key === "Enter") {
    event.preventDefault();
    saveThreadRename(input.dataset.sessionId || "");
  }
  if (event.key === "Escape") {
    event.preventDefault();
    cancelThreadRename();
  }
}

function handleThreadListInput(event) {
  const input = event.target.closest(".thread-rename-input");
  if (!input) return;
  if (state.renamingThreadId === input.dataset.sessionId) {
    state.renameDraftValue = input.value;
  }
}

function handleThreadListFocusout(event) {
  const input = event.target.closest(".thread-rename-input");
  if (!input) return;
  window.setTimeout(() => {
    const activeEditor = el.threadList.querySelector(".thread-rename-editor");
    if (activeEditor?.contains(document.activeElement)) return;
    if (state.renamingThreadId === input.dataset.sessionId) {
      saveThreadRename(input.dataset.sessionId || "");
    }
  }, 0);
}

function handleAttachmentClick(event) {
  const button = event.target.closest("[data-action='remove-attachment']");
  if (!button) return;
  removeAttachment(button.dataset.attachmentId);
}

function handleComposerClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button || !el.chatForm.contains(button)) return;
  const action = button.dataset.action;
  if (action === "share-thread") {
    event.preventDefault();
    copySession();
  }
  if (action === "share-selected") {
    event.preventDefault();
    copyShareLink();
  }
  if (action === "clear-share-selection") {
    event.preventDefault();
    clearShareSelection();
  }
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
el.chatForm.addEventListener("click", handleComposerClick);
el.stopButton.addEventListener("click", stopRequest);
el.newSessionButton.addEventListener("click", newSession);
el.threadList.addEventListener("click", handleThreadListClick);
el.threadList.addEventListener("input", handleThreadListInput);
el.threadList.addEventListener("keydown", handleThreadListKeydown);
el.threadList.addEventListener("focusout", handleThreadListFocusout);
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
el.messageInput.addEventListener("paste", handleComposerPaste);
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

window.crtShareThread = copySession;
window.crtShareSelected = copyShareLink;
window.crtClearShareSelection = clearShareSelection;

async function initializeApp() {
  el.workspaceId.value = SHARED_WORKSPACE_ID;
  el.userId.value = SHARED_USER_ID;
  autoResizeInput();
  render({ forceScroll: true });
  await refreshHealth();
  await refreshThreads();

  if (INITIAL_SESSION_ID) {
    const range =
      INITIAL_SHARE_FROM || INITIAL_SHARE_TO
        ? { from: INITIAL_SHARE_FROM || INITIAL_SHARE_TO, to: INITIAL_SHARE_TO || INITIAL_SHARE_FROM }
        : null;
    await loadThread(INITIAL_SESSION_ID, { range });
  } else {
    render({ forceScroll: true });
  }

  window.setInterval(() => {
    if (!state.busy) refreshThreads();
  }, 30000);
}

initializeApp();
