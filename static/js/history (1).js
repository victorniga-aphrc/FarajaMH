(function () {
  function byId(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  let selectedConversationId = null;
  let conversations = [];

  async function api(url, options) {
    const res = await fetch(url, { credentials: "same-origin", ...(options || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `Request failed (${res.status})`);
    }
    return data;
  }

  function renderList() {
    const host = byId("history-list");
    if (!host) return;
    host.innerHTML = "";
    if (!conversations.length) {
      host.innerHTML = '<div class="list-group-item text-muted">No conversations yet.</div>';
      return;
    }
    conversations.forEach((c) => {
      const active = c.id === selectedConversationId ? "active" : "";
      const a = document.createElement("button");
      a.type = "button";
      a.className = `list-group-item list-group-item-action ${active}`;
      a.dataset.cid = c.id;
      a.innerHTML = `
        <div class="d-flex w-100 justify-content-between">
          <small>${new Date(c.created_at).toLocaleString()}</small>
          <small>${c.message_count || 0} msgs</small>
        </div>
        <div class="small text-truncate">${esc(c.preview || "No preview")}</div>
      `;
      host.appendChild(a);
    });
  }

  function renderMessages(items) {
    const empty = byId("history-empty");
    const box = byId("history-messages");
    if (!box || !empty) return;
    box.innerHTML = "";
    if (!items.length) {
      empty.style.display = "";
      box.style.display = "none";
      empty.textContent = "No messages for this conversation.";
      return;
    }
    empty.style.display = "none";
    box.style.display = "";
    items.forEach((m) => {
      const p = document.createElement("p");
      p.innerHTML = `
        <strong>${esc(m.role || "unknown")}</strong>
        <small class="text-muted ms-2">${esc(m.timestamp || "")}</small><br>
        ${esc(m.message || "")}
      `;
      box.appendChild(p);
    });
  }

  async function loadMessages(cid) {
    selectedConversationId = cid;
    renderList();
    byId("history-delete-btn")?.removeAttribute("disabled");
    byId("history-detail-title").textContent = `Conversation ${cid}`;
    const data = await api(`/api/conversations/${encodeURIComponent(cid)}/messages`);
    renderMessages(data.messages || []);
  }

  async function loadConversations() {
    const data = await api("/api/my-conversations");
    conversations = data.conversations || [];
    renderList();
    if (conversations.length && !selectedConversationId) {
      loadMessages(conversations[0].id).catch(console.error);
    }
  }

  async function deleteConversation() {
    if (!selectedConversationId) return;
    if (!confirm("Delete this conversation? This cannot be undone.")) return;
    await api(`/api/conversations/${encodeURIComponent(selectedConversationId)}`, { method: "DELETE" });
    selectedConversationId = null;
    byId("history-delete-btn")?.setAttribute("disabled", "");
    byId("history-detail-title").textContent = "Conversation Detail";
    const empty = byId("history-empty");
    const box = byId("history-messages");
    if (empty) {
      empty.style.display = "";
      empty.textContent = "Select a conversation to view its transcript.";
    }
    if (box) {
      box.style.display = "none";
      box.innerHTML = "";
    }
    await loadConversations();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const list = byId("history-list");
    list?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-cid]");
      if (!btn) return;
      loadMessages(btn.dataset.cid).catch((err) => alert(err.message));
    });
    byId("history-delete-btn")?.addEventListener("click", () => {
      deleteConversation().catch((err) => alert(err.message));
    });
    loadConversations().catch((err) => {
      const empty = byId("history-empty");
      if (empty) empty.textContent = err.message;
    });
  });
})();
