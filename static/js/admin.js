// static/js/admin.js

// ---- Helpers ----
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}

async function getJSON(url) {
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`HTTP ${r.status}: ${text}`);
  }
  return r.json();
}

async function deleteJSON(url) {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
  const r = await fetch(url, {
    method: "DELETE",
    credentials: "same-origin",
    headers: csrf ? { "X-CSRFToken": csrf } : {}
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${r.status}`);
  }
  return data;
}

function fmtDateTime(iso) {
  try { return new Date(iso).toLocaleString(); } catch { return iso || ''; }
}

// ---- Renderers: existing KPIs/Charts/Tables ----
function renderKPIs(sum) {
  users_kpi = document.getElementById('kpi-users')
  if (users_kpi){
  users_kpi.textContent =
    `Total: ${sum.users.total} | Clinicians: ${sum.users.clinicians} | Admins: ${sum.users.admins}`;
  }

  document.getElementById('kpi-convos').textContent = sum.conversations.total;
  document.getElementById('kpi-messages').textContent = sum.messages.total;
  document.getElementById('kpi-reco').textContent = sum.messages.recommended;
}

let _convChart;
function renderConversationsPerDayChart(sum) {
  const labels = (sum.series?.conversations_per_day || []).map(([d]) => d);
  const data = (sum.series?.conversations_per_day || []).map(([, c]) => c);
  const ctx = document.getElementById('chart-convos');
  if (!ctx) return;
  if (_convChart) _convChart.destroy();
  _convChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [{ label: 'Conversations', data }] },
    options: { responsive: true, scales: { y: { beginAtZero: true } } }
  });
}

function renderTopCliniciansTable(sum) {
  const tbodyClin = document.querySelector('#tbl-clinicians tbody');
  if (!tbodyClin) return;
  tbodyClin.innerHTML = '';
  (sum.series?.top_clinicians || []).forEach(row => {
    const tr = document.createElement('tr');
    const label = row.display_name ? `${row.display_name} (${row.email})` : row.email;
    tr.innerHTML = `<td>${escapeHtml(label)}</td><td>${row.count}</td>`;
    tbodyClin.appendChild(tr);
  });
}

// ---- Conversations list (paginated) ----
function renderConversationRows(conversations) {
  const tbody = document.querySelector('#tbl-convos tbody');
  if (!tbody) return;
  conversations.forEach(c => {
    const ownerTxt = c.owner_email
      ? (c.owner_display_name ? `${c.owner_display_name} (${c.owner_email})` : c.owner_email)
      : (c.owner_user_id != null ? String(c.owner_user_id)
      : (c.owner ?? '-'));

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="text-truncate" style="max-width:260px">${escapeHtml(c.id)}</td>
      <td>${escapeHtml(String(ownerTxt))}</td>
      <td>${new Date(c.created_at).toLocaleString()}</td>
      <td class="d-flex gap-1">
        <button class="btn btn-sm btn-outline-primary" data-cid="${escapeHtml(c.id)}">View</button>
        <button class="btn btn-sm btn-outline-danger" data-del-cid="${escapeHtml(c.id)}">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}


async function showConversation(cid) {
  const detail = document.getElementById('conv-detail');
  const err = document.getElementById('admin-error');
  if (!detail) {
    console.error('#conv-detail not found in DOM');
    if (err) { err.style.display = ''; err.textContent = 'Template missing #conv-detail element.'; }
    return;
  }

  const j = await getJSON(`/admin/api/conversation/${encodeURIComponent(cid)}`);
  if (j.ok === false) throw new Error(j.error || 'Failed to load conversation');

  const msgs = (j.messages || []).map(m => ({
    role: m.role,
    timestamp: m.timestamp,
    text: m.text ?? m.raw_text ?? m.message ?? ''
  }));

  const recs = (j.recommended_questions || []).map(q => ({
    question: q.question ?? q.text ?? '',
    symptom: q.symptom || null
  }));

function formatBoldNewLines(text) {
  return text
    // 1️⃣ Convert **text** → new line + bold
    .replace(/\*\*(.*?)\*\*/g, '<br><strong>$1</strong>')

    // 2️⃣ Convert "- Step X:" into bold step headers on new lines
    .replace(/-\s*(Step\s*\d+):/gi, '<br><strong>$1:</strong>')

    // 3️⃣ Ensure each step description starts on its own line
    .replace(/(<\/strong>)([^<])/g, '$1<br>$2')

    // 4️⃣ Clean up leading breaks
    .replace(/^<br>/, '');
  }



  const msgList = msgs.map(m => `
    <li class="mb-2">
      <strong>${escapeHtml(m.role)}</strong>
      <small class="text-muted"> ${escapeHtml(m.timestamp || '')}</small><br/>
      <span>${formatBoldNewLines(escapeHtml(m.text))}</span>
    </li>
  `).join('');


  const recoList = recs.length
    ? recs.map(q => `
        <li class="mb-2">
          ${escapeHtml(q.question)}
        </li>`).join('')
    : '<em>No recommended questions.</em>';

  detail.innerHTML = `
    <div class="card p-3">
      <h5 class="mb-3">Conversation ${escapeHtml(cid)}</h5>
      <div class="row">
        <!-- Left column -->
        <div class="col-md-6">
          <h6>Transcript</h6>
          <ul class="list-unstyled mb-0">
            ${msgList}
          </ul>
        </div>

        <!-- Right column -->
        <div class="col-md-6">
          <h6>Recommended Questions</h6>
          <ul class="list-unstyled mb-0">
            ${recoList}
          </ul>

          <div class="mt-4">
            <h6>Disease Likelihoods</h6>
            <div class="card-body" id="conv-like-box">
              <p class="text-muted mb-0">
                Select a conversation to view estimated likelihoods.
              </p>
            </div>
          </div>
        </div>
      </div>

    </div>`;

        // Show loading state
    const box = document.getElementById('conv-like-box');
    if (box) {
      box.innerHTML = `<p class="text-muted mb-0">Loading likelihoods…</p>`;
    }

    // Fetch and render likelihoods
    fetchAndShowLikelihoods(cid).catch(err => {
      console.error(err);
      if (box) {
        box.innerHTML = `<p class="text-danger mb-0">
          Failed to load disease likelihoods.
        </p>`;
      }});
  detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
}


function resizeCanvas(canvas) {
  // Get the container width
  const rect = canvas.parentElement.getBoundingClientRect();
  const dpi = window.devicePixelRatio || 1;

  // Set canvas width/height in pixels (actual pixels, not CSS)
  canvas.width = rect.width * dpi;
  canvas.height = 300 * dpi; // desired height in pixels

  // Set CSS to match container size
  canvas.style.width = rect.width + "px";
  canvas.style.height = "300px";

  return dpi;
}


function renderGlobalSummary(symData) {
  const container = document.getElementById('symptom-summary');
  if (!container) return;

  const totalConvos = symData.total_convos || 0;
  const counts = symData.global_counts || {};

  const entries = Object.entries(counts)
    .sort((a, b) => b[1] - a[1]);

  let html = `
    <div style="margin-bottom: 10px;">
      <strong>Total Conversations:</strong> ${totalConvos}
    </div>
    <div>
      <strong>Symptom Frequency:</strong>
      <ul style="columns: 2; margin-top: 6px;">
  `;

  for (const [word, count] of entries) {
    html += `<li><strong>${word}</strong>: ${count}</li>`;
  }

  html += `
      </ul>
    </div>
  `;

  container.innerHTML = html;
}



function renderGlobalSymptoms(symData) {
  const canvas = document.getElementById('chart-symptoms');
  if (!canvas) return;

  // Resize canvas for high-DPI / crispness
  const rect = canvas.parentElement.getBoundingClientRect();
  const dpi = window.devicePixelRatio || 1;

  canvas.width = rect.width * dpi;
  canvas.height = 400 * dpi; // taller canvas
  canvas.style.width = rect.width + "px";
  canvas.style.height = "400px";

  const entries = Object.entries(symData.global || {});
  if (!entries.length) return;

  const list = entries
    .sort((a, b) => b[1] - a[1])
    .slice(0, 50)
    .map(([word, count]) => [word, count]);

  WordCloud(canvas, {
    list,
    gridSize: 10, // slightly bigger grid
    weightFactor: function(size) {
      return Math.max(20, size * 8); // increase size multiplier
    },
    fontFamily: 'Segoe UI, Arial, sans-serif',
    color: 'random-dark',
    backgroundColor: '#ffffff',
    rotateRatio: 0.2, // more rotated words
    rotationSteps: 2,
    drawOutOfBound: false
  });
}


// function renderPerConversationSymptoms(symData) {
//   const tbody = document.getElementById('tbl-conv-symptoms-body');
//   if (!tbody) return;
//   tbody.innerHTML = '';
//   (symData.by_conversation || []).forEach(row => {
//     const ownerTxt = row.owner_email
//       ? row.owner_email
//       : (row.owner_user_id != null ? String(row.owner_user_id)
//       : (row.owner ?? '—'));

//     const symList = Object.entries(row.symptoms || {}).slice(0, 5)
//       .map(([s, c]) => `${escapeHtml(s)} (${c})`).join(', ') || '—';

//     const tr = document.createElement('tr');
//     tr.innerHTML = `
//       <td>${escapeHtml(String(ownerTxt))}</td>
//       <td class="text-truncate" style="max-width:260px"><code>${escapeHtml(row.conversation_id)}</code></td>
//       <td>${symList}</td>
//       <td><button class="btn btn-sm btn-outline-primary" data-like="${escapeHtml(row.conversation_id)}">View</button></td>
//     `;
//     tbody.appendChild(tr);
//   });
// }

function renderLikelihoodPanel(cid, data) {
  const box = document.getElementById('conv-like-box');
  if (!box) return;

  const symptomList = Object.entries(data.symptoms || {})
    .map(([s, c]) => `${escapeHtml(s)}`).join(', ')|| '—';

  const rows = (data.top_diseases || []).map(d => `
    <tr><td>${escapeHtml(d.disease)}</td><td>${d.likelihood_pct}%</td></tr>
  `).join('') || '<tr><td colspan="2" class="text-muted">No signal</td></tr>';

  box.innerHTML = `
    <p><strong>Extracted symptoms:</strong> ${symptomList}</p>
    <table class="table table-sm mb-0">
      <thead><tr><th>Disease</th><th>Estimated likelihood</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function fetchAndShowLikelihoods(cid) {
  const j = await getJSON(`/admin/api/conversation/${encodeURIComponent(cid)}/disease_likelihoods`);
  if (j.ok === false) throw new Error(j.error || 'Failed to load likelihoods');
  renderLikelihoodPanel(cid, j);
}

// ---- Paging state ----
const convoPager = { page: 1, size: 20, loading: false, done: false };

async function loadMoreConversations() {
  if (convoPager.loading || convoPager.done) return;
  convoPager.loading = true;
  try {
    const j = await getJSON(`/admin/api/conversations?page=${convoPager.page}&size=${convoPager.size}`);
    if (j.ok === false) throw new Error(j.error || 'Failed to load conversations');
    renderConversationRows(j.conversations || []);
    convoPager.page += 1;

    const loaded = (convoPager.page - 1) * convoPager.size;
    if (loaded >= (j.total || 0)) {
      convoPager.done = true;
      const btn = document.getElementById('load-more');
      if (btn) btn.disabled = true;
    }
  } catch (e) {
    const el = document.getElementById('admin-error');
    if (el) { el.style.display = ''; el.textContent = e.message; }
  } finally {
    convoPager.loading = false;
  }
}

async function deleteConversation(cid) {
  if (!cid) return;
  if (!confirm(`Delete conversation ${cid}? This cannot be undone.`)) return;
  await deleteJSON(`/admin/api/conversation/${encodeURIComponent(cid)}`);
  const detail = document.getElementById('conv-detail');
  if (detail) detail.innerHTML = '';
  const tbody = document.querySelector('#tbl-convos tbody');
  if (tbody) tbody.innerHTML = '';
  convoPager.page = 1;
  convoPager.done = false;
  const btn = document.getElementById('load-more');
  if (btn) btn.disabled = false;
  await loadMoreConversations();
}

// ---- Main init ----
async function adminInit() {
  const err = document.getElementById('admin-error');
  try {
    // Summary/KPIs
    const sum = await getJSON('/admin/api/summary');
    if (sum.ok === false) throw new Error(sum.error || 'Summary failed');

    renderKPIs(sum);
    renderConversationsPerDayChart(sum);
    renderTopCliniciansTable(sum);

    // Conversations list
    await loadMoreConversations();

    // Symptoms data -> chart + per-conv table
    const sym = await getJSON('/admin/api/symptoms');
    if (sym.ok === false) throw new Error(sym.error || 'Symptoms failed');
    renderGlobalSummary(sym)
    renderGlobalSymptoms(sym);

    // renderPerConversationSymptoms(sym);

    // Bind once
    document.getElementById('load-more')?.addEventListener('click', loadMoreConversations);

    // Clicks in conversations table (View transcript / Likelihoods)
    document.querySelector('#tbl-convos')?.addEventListener('click', async (e) => {
      const btnView = e.target.closest('button[data-cid]');
      const btnDelete = e.target.closest('button[data-del-cid]');
      const btnLike = e.target.closest('button[data-like]');
      try {
        if (btnView) await showConversation(btnView.getAttribute('data-cid'));
        if (btnDelete) await deleteConversation(btnDelete.getAttribute('data-del-cid'));
        if (btnLike) await fetchAndShowLikelihoods(btnLike.getAttribute('data-like'));
      } catch (ex) {
        if (err) { err.style.display = ''; err.textContent = ex.message; }
      }
    });

    // Clicks in per-conversation symptoms table (Likelihoods)
    document.getElementById('tbl-conv-symptoms-body')?.addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-like]');
      if (!btn) return;
      try {
        await fetchAndShowLikelihoods(btn.getAttribute('data-like'));
      } catch (ex) {
        if (err) { err.style.display = ''; err.textContent = ex.message; }
      }
    });

  } catch (ex) {
    if (err) { err.style.display = ''; err.textContent = ex.message; }
  }
}

// Run on /admin or /admin/ (tolerate trailing slash)
if (location.pathname === '/admin' || location.pathname === '/admin/' || 
  location.pathname === '/clinician_dashboard' || location.pathname === '/clinician_dashboard/') {
  window.addEventListener('DOMContentLoaded', () => {
    if (window.__ADMIN_INIT_ATTACHED__) return; // prevent double-binding
    window.__ADMIN_INIT_ATTACHED__ = true;
    adminInit();
  });
}



const form = document.getElementById("addClinicianForm");

if (form) {
  form.addEventListener("submit", async function(e) {
      e.preventDefault();

    const formData = new FormData(form);
    const data = Object.fromEntries(formData.entries());

    const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

    const resp = await fetch(form.dataset.url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken

        },
        body: JSON.stringify(data)
    });

    const result = await resp.json();
    if (result.ok) {
        alert("Clinician Added, Invitation sent");
        window.location.reload(); 

    } else {
      const el = $('auth-error');
      if (el) {
        el.textContent = result.error || 'Adding Clinician failed';
        el.classList.remove('d-none');
      }
      // Optionally reload CSRF token if needed
      await loadCsrf();
    }
  });
}


const toggleBtn = document.getElementById("toggleFormBtn");
const formContainer = document.getElementById("clinicianFormContainer");
const toggleIcon = document.getElementById("toggleIcon");

if (toggleBtn && formContainer && toggleIcon) {
  toggleBtn.addEventListener("click", () => {
      if (formContainer.style.display === "none" || formContainer.style.display === "") {
          formContainer.style.display = "block";
          toggleIcon.classList.remove("fa-chevron-down");
          toggleIcon.classList.add("fa-chevron-up");
      } else {
          formContainer.style.display = "none";
          toggleIcon.classList.remove("fa-chevron-up");
          toggleIcon.classList.add("fa-chevron-down");
      }
  });
}

const select = document.getElementById("institutionSelect");
const input = document.getElementById("newInstitution");

if (select && input) {
  select.addEventListener("change", () => {
    if (select.value) {
      input.value = "";
    }
  });

  input.addEventListener("input", () => {
    if (input.value.trim() !== "") {
      select.value = "";
    }
  });
}
