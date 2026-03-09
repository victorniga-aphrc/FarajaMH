// app.js — FULL updated (Role override fix + no confusion)
//
// Key guarantees:
// ✅ Single source of truth for the currently-selected turn role: window.CURRENT_ROLE
// ✅ One-shot override for the next submission only: window.NEXT_CHAT_ROLE
// ✅ Voice notes submit as role=patient ONLY when Patient is selected; otherwise clinician
// ✅ Typed sends use CURRENT_ROLE (unless forced role or NEXT_CHAT_ROLE is set)
// ✅ Transcript labels ALWAYS come from SSE payload (item.role), not UI selection
// ✅ Patient accounts remain forced to simulated + patient role (no Real Actors)
// ✅ Finalize works across modes and renders into the correct transcript box
//
// NOTE: This file is designed to preserve your existing working flows.

function $(id) { return document.getElementById(id); }
function on(el, ev, fn) { el && el.addEventListener(ev, fn); }
function show(el) { if (el) el.style.display = ''; }
function hide(el) { if (el) el.style.display = 'none'; }

// Force mode/role per user (patients locked server-side too, but we keep UI consistent)
window.APP_FORCED = window.APP_FORCED || { mode: null, role: null };

// One-shot override for the NEXT chat submission only (used by voice notes)
window.NEXT_CHAT_ROLE = window.NEXT_CHAT_ROLE || null;

// Global role state for turn-based real actors (admin)
window.CURRENT_ROLE = window.CURRENT_ROLE || "clinician";

// ---------------------------------------------
// Screening wiring (kept)
// ---------------------------------------------
window.MH_TRANSCRIPT = window.MH_TRANSCRIPT || "";
window.MH_ANSWERS   = window.MH_ANSWERS   || {};
window.MH_SAFETY    = !!window.MH_SAFETY;

async function runScreening() {
  try {
    const r = await fetch('/mh/screen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transcript: MH_TRANSCRIPT,
        responses: MH_ANSWERS,
        safety_concerns: MH_SAFETY
      })
    });
    if (!r.ok) return;
    const data = await r.json();
    renderScreeningPanel(data);
  } catch (e) { console.error('mh/screen failed', e); }
}
window.runScreening = runScreening;

let _mhTimer = null;
function debounceScreening() {
  clearTimeout(_mhTimer);
  _mhTimer = setTimeout(runScreening, 1200);
}
window.debounceScreening = debounceScreening;

function renderScreeningPanel(data) {
  const mount = $('mh-screening') || (() => {
    const d = document.createElement('div');
    d.id = 'mh-screening';
    d.className = 'alert alert-secondary mt-2';
    ($('agentsConversation') || document.body).prepend(d);
    return d;
  })();

  const chips = (data.results || []).map(r => {
    const sev  = String(r.severity || '').replace('_',' ');
    const conf = Math.round((r.confidence || 0) * 100);
    let extra = '';
    if (r.name === 'depression') extra = ` • PHQ-9≈${Math.round((r.score || 0) * 27)}/27`;
    if (r.name === 'anxiety')    extra = ` • GAD-7≈${Math.round((r.score || 0) * 21)}/21`;
    return `<span class="badge bg-info text-dark me-1">${r.name}: ${sev}${extra} (${conf}%)</span>`;
  }).join(' ');

  const why  = (data.results || []).flatMap(r =>
    (r.rationale || []).slice(0,5).map(e =>
      `<li><em>${e.feature}</em>: ${e.text} <small class="text-muted">(${e.source})</small></li>`)).join('');

  const steps = (data.results || []).flatMap(r =>
    (r.next_steps || []).map(s => `<li>${s}</li>`)).join('');

  mount.innerHTML = `
    <div><strong>Screening:</strong> ${data.overall_flag || ''}</div>
    <div class="mt-1">${chips || '<span class="text-muted">No signals yet</span>'}</div>
    <details class="mt-2"><summary>Why & Next steps</summary>
      <ul>${why || '<li class="text-muted">—</li>'}</ul>
      <strong>Next steps</strong>
      <ul>${steps || '<li class="text-muted">—</li>'}</ul>
    </details>`;
}
window.renderScreeningPanel = renderScreeningPanel;

// ---------------------------------------------
// Auth & CSRF helpers (kept)
// ---------------------------------------------
let CSRF_TOKEN = null;
async function loadCsrf() {
  try {
    const r = await fetch('/csrf-token', { credentials: 'same-origin' });
    const j = await r.json();
    CSRF_TOKEN = j.csrf_token || j.csrfToken || null;
    window.CSRF_TOKEN = CSRF_TOKEN;
  } catch (_) {}
}
function authHeaders() {
  return { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN || '' };
}
async function getMe() {
  try {
    const r = await fetch('/auth/me', { credentials: 'same-origin' });
    return await r.json();
  } catch { return { authenticated: false }; }
}
async function login(email, password, remember=true) {
  const r = await fetch('/auth/login', {
    method: 'POST', headers: authHeaders(), credentials: 'same-origin',
    body: JSON.stringify({ email, password, remember })
  });
  return r.json();
}
async function signup(email, password) {
  const r = await fetch('/auth/signup', {
    method: 'POST', headers: authHeaders(), credentials: 'same-origin',
    body: JSON.stringify({ email, password })
  });
  return r.json();
}
async function logout() {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const r = await fetch('/auth/logout', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken,
      ...authHeaders()
    }
  });
  return r.json();
}

window.ACTIVE_PATIENT_ID = window.ACTIVE_PATIENT_ID || null;

async function fetchPatients() {
  const r = await fetch('/api/patients', { credentials: 'same-origin' });
  return r.json();
}

async function fetchCurrentPatient() {
  const r = await fetch('/api/current-patient', { credentials: 'same-origin' });
  return r.json();
}

async function createPatientIdentifier(identifier) {
  const r = await fetch('/api/patients', {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify({ identifier })
  });
  return r.json();
}

async function selectPatient(patientId) {
  const r = await fetch('/api/select-patient', {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify({ patient_id: Number(patientId), continue_latest: true })
  });
  return r.json();
}

function renderPatientBadge() {
  const badge = $('activePatientBadge');
  const select = $('patientSelect');
  if (!badge) return;
  const option = select?.options?.[select.selectedIndex];
  if (window.ACTIVE_PATIENT_ID && option && option.value) {
    badge.textContent = `Patient: ${option.text}`;
    badge.classList.remove('bg-secondary');
    badge.classList.add('bg-primary');
  } else {
    badge.textContent = 'No patient selected';
    badge.classList.remove('bg-primary');
    badge.classList.add('bg-secondary');
  }
}

async function initPatientContext(user) {
  const hasScopedRole = !!(user?.roles || []).some(r => r === 'admin' || r === 'clinician');
  const select = $('patientSelect');
  const addBtn = $('newPatientBtn');
  const inlinePanel = $('newPatientInlinePanel');
  const patientInput = $('newPatientIdentifierInput');
  const cancelCreatePatientBtn = $('cancelCreatePatientBtn');
  const createPatientConfirmBtn = $('createPatientConfirmBtn');
  const newPatientError = $('newPatientError');
  if (!hasScopedRole || !select) return;

  async function refreshPatientList() {
    const list = await fetchPatients();
    if (!list.ok) return [];
    const patients = list.patients || [];
    const currentSelected = select.value || (window.ACTIVE_PATIENT_ID ? String(window.ACTIVE_PATIENT_ID) : '');
    select.innerHTML = '<option value="">Select patient</option>' +
      patients.map(p => `<option value="${p.id}">${p.identifier}</option>`).join('');
    if (currentSelected && patients.some(p => String(p.id) === String(currentSelected))) {
      select.value = String(currentSelected);
    }
    return patients;
  }

  await refreshPatientList();
  const cur = await fetchCurrentPatient();
  if (cur.ok && cur.patient?.id) {
    window.ACTIVE_PATIENT_ID = cur.patient.id;
    select.value = String(cur.patient.id);
  } else {
    window.ACTIVE_PATIENT_ID = null;
  }
  renderPatientBadge();

  if (!select.dataset.boundPatientContext) {
    select.dataset.boundPatientContext = '1';
    on(select, 'change', async () => {
      const pid = select.value;
      if (!pid) {
        window.ACTIVE_PATIENT_ID = null;
        renderPatientBadge();
        return;
      }
      const out = await selectPatient(pid);
      if (!out.ok) { alert(out.error || 'Failed to select patient'); return; }
      window.ACTIVE_PATIENT_ID = Number(pid);
      renderPatientBadge();
      try { await window.resetConversationAndUI?.(); } catch (_) {}
    });
  }

  if (addBtn && !addBtn.dataset.boundPatientContext) {
    addBtn.dataset.boundPatientContext = '1';
    on(addBtn, 'click', async () => {
      if (newPatientError) {
        newPatientError.classList.add('d-none');
        newPatientError.textContent = '';
      }
      if (patientInput) patientInput.value = '';
      if (inlinePanel) inlinePanel.style.display = '';
      if (patientInput) patientInput.focus();
    });
  }

  if (cancelCreatePatientBtn && !cancelCreatePatientBtn.dataset.boundPatientContext) {
    cancelCreatePatientBtn.dataset.boundPatientContext = '1';
    on(cancelCreatePatientBtn, 'click', () => {
      if (inlinePanel) inlinePanel.style.display = 'none';
      if (newPatientError) {
        newPatientError.classList.add('d-none');
        newPatientError.textContent = '';
      }
      if (patientInput) patientInput.value = '';
    });
  }

  if (createPatientConfirmBtn && !createPatientConfirmBtn.dataset.boundPatientContext) {
    createPatientConfirmBtn.dataset.boundPatientContext = '1';
    on(createPatientConfirmBtn, 'click', async () => {
      const identifier = (patientInput?.value || '').trim().toUpperCase();
      if (!identifier) {
        if (newPatientError) {
          newPatientError.textContent = 'Patient identifier is required.';
          newPatientError.classList.remove('d-none');
        }
        return;
      }

      createPatientConfirmBtn.setAttribute('disabled', '');
      try {
        const out = await createPatientIdentifier(identifier);
        if (!out.ok) {
          if (newPatientError) {
            newPatientError.textContent = out.error || 'Failed to create patient';
            newPatientError.classList.remove('d-none');
          }
          return;
        }
        if (inlinePanel) inlinePanel.style.display = 'none';
        await refreshPatientList();
        if (out.patient?.id) {
          select.value = String(out.patient.id);
          const sel = await selectPatient(out.patient.id);
          if (sel.ok) {
            window.ACTIVE_PATIENT_ID = out.patient.id;
            renderPatientBadge();
            try { await window.resetConversationAndUI?.(); } catch (_) {}
          }
        }
      } finally {
        createPatientConfirmBtn.removeAttribute('disabled');
      }
    });
  }
}

function showAuth() {
  $('auth-gate')?.setAttribute('style','');
  const app = $('app-wrapper'); if (app) app.style.display = 'none';
}
function showApp(user) {
  const gate = $('auth-gate'); if (gate) gate.style.display = 'none';
  const app  = $('app-wrapper'); if (app) app.style.display = '';
  const who  = $('whoami');
  if (who) {
    const label = user?.display_name || user?.username || user?.name || 'User';
    who.innerHTML = `Welcome <strong>${label}</strong> - role: <strong>${(user?.roles || []).join(', ')}</strong>`;
  }
}

// ---------------------------------------------
// App init (auth gate + toggles)
// ---------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
  const loader = $('loader');
  const footer = $('footer');
  // Initial visibility is set server-side from current_user so logged-in users don't see a login flash when navigating to Home

  try {
    await loadCsrf();
    const me = await getMe();
    if (me.authenticated) {
      showApp(me.user);
      const roles = JSON.parse(sessionStorage.getItem('userRole') || '[]');
      adjustChatOptions({ roles });
      await initPatientContext(me.user);
      if (footer) footer.style.display = '';
    } else {
      showAuth();
      if (footer) footer.style.display = '';
    }
  } catch (err) {
    console.error('Error checking auth:', err);
    showAuth();
  } finally {
    if (loader) {
      loader.style.opacity = '0';
      loader.style.transition = 'opacity 0.4s ease';
      setTimeout(() => loader.remove(), 400);
    }
  }

  const loginForm = $('login-form');
  on(loginForm, 'submit', async (e) => {
    e.preventDefault();
    const email = $('login-email')?.value.trim().toLowerCase();
    const password = $('login-password')?.value;
    try {
      const res = await login(email, password);
      if (res.ok || res.authenticated) {
        const isClinician = res.user.roles?.includes('clinician');
        const needsReset = res.user.reset_password;
        sessionStorage.setItem('userRole', JSON.stringify(res.user.roles));

        if (isClinician && needsReset) {
          window.location.href = '/new-password';
        } else {
          location.reload();
        }
      } else {
        const el = $('auth-error');
        if (el) { el.textContent = res.error || 'Login failed'; el.classList.remove('d-none'); }
        await loadCsrf();
      }
    } catch {
      const el = $('auth-error');
      if (el) { el.textContent = 'Network error'; el.classList.remove('d-none'); }
    }
  });

  const signupForm = $('signup-form');
  on(signupForm, 'submit', async (e) => {
    e.preventDefault();
    const email = $('signup-email')?.value.trim().toLowerCase();
    const password = $('signup-password')?.value;
    try {
      const res = await signup(email, password);
      if (res.ok) {
        alert("Signup successful, Check mail for OTP");
        window.location.href = `/otp-verification?email=${encodeURIComponent(email)}`;
      } else {
        const el = $('auth-error');
        if (el) { el.textContent = res.error || 'Signup failed'; el.classList.remove('d-none'); }
        await loadCsrf();
      }
    } catch {
      const el = $('auth-error');
      if (el) { el.textContent = 'Network error'; el.classList.remove('d-none'); }
    }
  });

  on(document, 'click', (e) => {
    const el = e.target.closest('[data-action="show-signup"], [data-action="show-login"]');
    if (!el) return;
    e.preventDefault();
    if (el.dataset.action === 'show-signup') {
      $('signup-card')?.classList.remove('d-none');
      $('login-card')?.classList.add('d-none');
    } else {
      $('login-card')?.classList.remove('d-none');
      $('signup-card')?.classList.add('d-none');
    }
    const err = $('auth-error'); if (err) { err.classList.add('d-none'); err.textContent = ''; }
  });

  on($('logout-btn'), 'click', async () => {
    try {
      const result = await logout();
      if (result.ok) window.location.href = "/";
    } catch (err) {
      alert("Logout error:", err);
    }
  });
});

const _origShowAuth = showAuth;
window.showAuth = function() {
  _origShowAuth();
  $('login-card')?.classList.remove('d-none');
  $('signup-card')?.classList.add('d-none');
  const err = $('auth-error');
  if (err){ err.classList.add('d-none'); err.textContent=''; }
};

// ---------------------------------------------
// Agents UI: mode & toggles (kept)
// ---------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const convWrap   = $('agentsConversation');
  const chatModeEl = $('chatMode');
  const modeBadge  = $('modeBadge');
  const modeTipTxt = $('modeTipText');
  const msgInput   = $('agentMessage');
  if (!chatModeEl || !convWrap) return;

  if (![...chatModeEl.options].some(o => o.value === 'live')) {
    chatModeEl.add(new Option('Live (Mic)', 'live'));
  }

  function ensureLiveUI() {
    let bar = $('liveBar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'liveBar';
      bar.className = 'd-flex gap-2 align-items-center mb-2';
      (convWrap || document.body).prepend(bar);
    }
    if (!$('startLiveBtn')) {
      const s = document.createElement('button');
      s.id = 'startLiveBtn'; s.type = 'button';
      s.className = 'btn btn-sm btn-primary'; s.textContent = '▶ Start Live';
      bar.appendChild(s);
    }
    if (!$('stopLiveBtn')) {
      const t = document.createElement('button');
      t.id = 'stopLiveBtn'; t.type = 'button';
      t.className = 'btn btn-sm btn-danger'; t.textContent = '■ Stop'; t.disabled = true;
      bar.appendChild(t);
    }
    if (!$('liveStatus')) {
      const sp = document.createElement('span');
      sp.id = 'liveStatus'; sp.className = 'ms-2 text-muted'; sp.textContent = '';
      bar.appendChild(sp);
    }
    if (!$('liveMeter')) {
      const sp = document.createElement('span');
      sp.id = 'liveMeter'; sp.className = 'ms-2 text-muted small'; sp.textContent = '';
      bar.appendChild(sp);
    }
    if (!$('liveSuggestMode')) {
      const sel = document.createElement('select');
      sel.id = 'liveSuggestMode';
      sel.className = 'form-select form-select-sm w-auto';
      sel.innerHTML = `<option value="stream">Suggest during conversation</option>
                       <option value="final">Suggest at the end</option>`;
      bar.appendChild(sel);
    }
    return bar;
  }

  function toggleLiveControls(showIt) {
    const bar = ensureLiveUI();
    bar.style.display = showIt ? '' : 'none';
  }

  function applyModeUI(val) {
    const turn = $('turnPane') || convWrap;
    const live = $('livePane');
    if (turn) show(turn);
    if (live) hide(live);

    convWrap.classList.remove('mode-real', 'mode-simulated', 'mode-live');

    if (val === 'simulated') {
      convWrap.classList.add('mode-simulated');
      if (modeBadge)  modeBadge.textContent = 'Simulated';
      if (modeTipTxt) modeTipTxt.textContent = 'Chat with Clinician';
      if (msgInput)   msgInput.placeholder = 'Say something to the doctor...';
      toggleLiveControls(false);
    } else if (val === 'live') {
      convWrap.classList.add('mode-live');
      if (modeBadge)  modeBadge.textContent = 'Live (Mic)';
      if (modeTipTxt) modeTipTxt.textContent = 'Speak and get continuous recommendations. Press Stop to end; use Finalize for summary/plan.';
      toggleLiveControls(true);
      if (live) { const turnP = $('turnPane'); if (turnP) hide(turnP); show(live); }
    } else {
      convWrap.classList.add('mode-real');
      if (modeBadge)  modeBadge.textContent = 'Real Actors';
      if (modeTipTxt) modeTipTxt.textContent = 'Turn-based chat: alternate between Clinician and Patient.';
      if (msgInput)   msgInput.placeholder = 'Say something to the doctor...';
      toggleLiveControls(false);
    }
  }

  applyModeUI(chatModeEl.value);
  on(chatModeEl, 'change', () => applyModeUI(chatModeEl.value));
});

// ---------------------------------------------
// Role switcher (Real Actors) — HARDENED (no ambiguity)
// ---------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  const clinicianBtn = $("roleClinicianBtn");
  const patientBtn   = $("rolePatientBtn");
  const roleDisplay  = $("currentRoleDisplay");

  function paintButtons(role) {
    // Keep bootstrap intent consistent: selected=primary, other=secondary
    if (clinicianBtn) {
      clinicianBtn.classList.toggle("btn-primary", role === "clinician");
      clinicianBtn.classList.toggle("btn-secondary", role !== "clinician");
      clinicianBtn.setAttribute("aria-pressed", role === "clinician" ? "true" : "false");
    }
    if (patientBtn) {
      patientBtn.classList.toggle("btn-primary", role === "patient");
      patientBtn.classList.toggle("btn-secondary", role !== "patient");
      patientBtn.setAttribute("aria-pressed", role === "patient" ? "true" : "false");
    }
  }

  function setRole(r) {
    const role = (r || "clinician").toLowerCase() === "patient" ? "patient" : "clinician";
    window.CURRENT_ROLE = role;
    paintButtons(role);
    if (roleDisplay) roleDisplay.textContent = `(Current: ${role === "patient" ? "Patient" : "Clinician"})`;
  }

  // Initialize once from global
  setRole(window.CURRENT_ROLE || "clinician");

  on(clinicianBtn, "click", () => setRole("clinician"));
  on(patientBtn, "click", () => setRole("patient"));

  // Helper for any sender (typed or voice): resolves the role to send RIGHT NOW
  window.__getRoleToSend = function __getRoleToSend() {
    const forcedRole = window.APP_FORCED?.role;
    if (forcedRole) return forcedRole;

    // one-shot override (voice note) wins for the *next* submit only
    if (window.NEXT_CHAT_ROLE) return window.NEXT_CHAT_ROLE;

    // fallback to CURRENT_ROLE (admin real actors)
    return window.CURRENT_ROLE || "clinician";
  };

  // Helper: clear one-shot override after use
  window.__consumeOneShotRole = function __consumeOneShotRole() {
    window.NEXT_CHAT_ROLE = null;
  };
});

// ---------------------------------------------
// Finalize + Reset + Turn-based send (SSE)
// ---------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  // Finalize — mode agnostic on backend; front chooses correct transcript host
  on($("finalizeBtn"), "click", () => {
    const language = $("languageMode")?.value || "bilingual";
    const mode = $("chatMode")?.value || (window.APP_FORCED?.mode || "real");

    const transcriptDiv =
      (mode === "live")
        ? $("liveTranscript")
        : $("agentChatTranscript");

    const es = new EventSource(
      `/agent_chat_stream?message=${encodeURIComponent('[Finalize]')}&lang=${encodeURIComponent(language)}&role=finalize&mode=${encodeURIComponent(mode)}`
    );

    es.onmessage = (event) => {
      const item = JSON.parse(event.data);
      if (item.type === 'question_recommender') return;

      const p = document.createElement('p');
      // IMPORTANT: label always from SSE payload
      p.innerHTML = `<strong>${item.role}:</strong><br>${(item.message || '').replaceAll('\n','<br>')}<br>
                     <small class="text-muted">${item.timestamp || ''}</small>`;
      transcriptDiv?.appendChild(p);
      transcriptDiv?.scrollTo({ top: transcriptDiv.scrollHeight, behavior: 'smooth' });
    };

    es.onerror = () => es.close();
  });

  // Shared: reset conversation server-side and clear transcript/suggested UI (used by Reset button and mode change)
  window.resetConversationAndUI = async function () {
    try {
      const res1 = await fetch('/reset_conv', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
        credentials: 'same-origin'
      });
      const data1 = await res1.json();
      if (data1.ok) {
        await fetch('/live/reset_plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
          credentials: 'same-origin'
        });
        $("agentChatTranscript")?.replaceChildren();
        $("chatSuggestedQuestions")?.replaceChildren();
        $("liveTranscript")?.replaceChildren();
        $("liveSuggestedQuestions")?.replaceChildren();
        const badge = $("unasked-badge"); if (badge) badge.textContent = '0';
        window.MH_TRANSCRIPT = ""; window.MH_SAFETY = false;
        $("mh-screening")?.replaceChildren();
      }
    } catch (err) { console.error('Reset error:', err); }
  };

  on($("resetBtn"), "click", () => { window.resetConversationAndUI(); });

  // Turn-based send (typed + voice-submitted transcription)
  on($("agentChatForm"), "submit", (e) => {
    e.preventDefault();

    const messageInput = $("agentMessage");
    const transcriptDiv = $("agentChatTranscript");
    const typingIndicator = $("typingIndicator");

    const message = (messageInput?.value || "").trim();
    const language = $("languageMode")?.value || "bilingual";

    if (!message) { alert("Please enter a message!"); return; }
    if ($('patientSelect') && !window.ACTIVE_PATIENT_ID) { alert("Select a patient before starting conversation."); return; }

    typingIndicator && (typingIndicator.style.display = "block");

    const forcedMode = window.APP_FORCED?.mode;
    const mode = forcedMode || $("chatMode")?.value || "real";

    // Resolve role deterministically
    const roleToSend = (window.__getRoleToSend ? window.__getRoleToSend() : (window.CURRENT_ROLE || "clinician"));

    // Consume one-shot override immediately so next messages don't inherit it
    if (window.__consumeOneShotRole) window.__consumeOneShotRole();

    const es = new EventSource(
      `/agent_chat_stream?message=${encodeURIComponent(message)}&lang=${encodeURIComponent(language)}` +
      `&role=${encodeURIComponent(roleToSend)}&mode=${encodeURIComponent(mode)}`
    );

    es.onmessage = (event) => {
      const item = JSON.parse(event.data);

      if (item.type === "question_recommender") {
        const qContainer = $("chatSuggestedQuestions");
        const li = document.createElement("li");
        li.innerHTML = `<strong>English:</strong> ${item.question?.english || ""}<br>
                        <strong>Swahili:</strong> ${item.question?.swahili || ""}`;
        qContainer?.appendChild(li);
        return;
      }

      const p = document.createElement("p");
      // IMPORTANT: label always from SSE payload, not UI role
      p.innerHTML = `<strong>${item.role}:</strong><br>${(item.message || "").replaceAll("\n", "<br>")}<br>
                    <small class="text-muted">${item.timestamp || ""}</small>`;
      transcriptDiv?.appendChild(p);
      transcriptDiv?.scrollTo({ top: transcriptDiv.scrollHeight, behavior: "smooth" });

      // Screening accumulates only patient text
      const role = (item.role || "").toLowerCase().trim();
      if (role === "patient") {
        const msg = (item.message || "").trim();
        if (msg) {
          window.MH_TRANSCRIPT += (window.MH_TRANSCRIPT ? "\n" : "") + msg;
          const t = msg.toLowerCase();
          if (t.includes("suicid") || t.includes("kujiua") || t.includes("kill myself")) window.MH_SAFETY = true;
          debounceScreening();
        }
      }
      runScreening();
    };

    es.onerror = () => {
      typingIndicator && (typingIndicator.style.display = "none");
      es.close();
    };

    es.onopen = () => typingIndicator && (typingIndicator.style.display = "block");
    es.addEventListener("message", () => setTimeout(() => typingIndicator && (typingIndicator.style.display = "none"), 500));

    if (messageInput) messageInput.value = "";
  });
});

// ---------------------------------------------
// Voice note (batch transcription) — FIXED role routing (one-shot override)
// ---------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  let mediaRecorder;
  let audioChunks = [];
  const recordBtn = $("recordAudioBtn");
  const audioElement = $("recordedAudio");

  on(recordBtn, "click", async () => {
    if (!mediaRecorder || mediaRecorder.state === "inactive") {
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          alert('Your browser does not support audio recording. Please use a modern browser or enable HTTPS.');
          return;
        }
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, sampleRate: 48000, noiseSuppression: true, echoCancellation: true, autoGainControl: true }
        });
        const options = { mimeType: "audio/webm;codecs=opus", audioBitsPerSecond: 128000 };
        mediaRecorder = new MediaRecorder(stream, options);
        mediaRecorder.start(1000);
        audioChunks = [];
        mediaRecorder.ondataavailable = e => { if (e.data?.size) audioChunks.push(e.data); };

        mediaRecorder.onstop = async () => {
          try { stream.getTracks().forEach(t => t.stop()); } catch(_) {}
          const audioBlob = new Blob(audioChunks, { type: "audio/webm" });

          if (audioElement) {
            audioElement.src = URL.createObjectURL(audioBlob);
            audioElement.style.display = "block";
          }

          const lang = $("languageMode")?.value || "bilingual";

          // Determine the intended role at STOP TIME (when we submit)
          // This ensures "click patient / click clinician" is respected.
          const intendedRole =
            (window.APP_FORCED?.role) ? window.APP_FORCED.role :
            (window.CURRENT_ROLE || "clinician");

          const formData = new FormData();
          formData.append("audio", audioBlob);
          formData.append("lang", lang);

          // This 'role' is only for /transcribe_audio endpoint (if backend uses it)
          formData.append("role", intendedRole);

          recordBtn.textContent = "⌛ Transcribing...";

          try {
            const response = await fetch("/transcribe_audio", { method: "POST", body: formData });
            const data = await response.json();

            if (data.text) {
              const input = $("agentMessage");
              if (input) input.value = data.text;

              // ✅ One-shot override for the next submission ONLY
              window.NEXT_CHAT_ROLE = intendedRole;

              // Submit the form once, deterministically
              $("agentChatForm")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            } else {
              alert("Failed to transcribe audio.");
            }
          } catch (err) {
            alert("Transcription error.");
            console.error(err);
          }

          recordBtn.textContent = "🎤 Voice Note";
        };

        recordBtn.textContent = "⏹ Stop Recording";
      } catch (error) {
        console.error(error);
        alert(error?.name === "NotAllowedError" ? "Microphone access denied in the browser." : "Audio capture failed.");
      }
    } else if (mediaRecorder.state === "recording") {
      mediaRecorder.stop();
    }
  });
});

// ---------------------------------------------
// Live (Mic) via WebSocket — kept (role here is 'live')
// ---------------------------------------------
(() => {
  function wsURL(path) {
    const fullPath = path.startsWith('/') ? path : '/' + path;
    const url = new URL(fullPath, window.location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.href;
  }

  const qs = (id) => $(id);

  let liveMediaStream = null;
  let liveRecorder = null;
  let liveWS = null;
  let liveActive = false;

  async function startLive() {
    if (liveActive) return;
    if ($('patientSelect') && !window.ACTIVE_PATIENT_ID) {
      alert('Select a patient before starting live conversation.');
      return;
    }
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert('Your browser does not support audio recording. Please use a modern browser or enable HTTPS.');
        return;
      }
      liveMediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 48000,
          noiseSuppression: true,
          echoCancellation: true,
          autoGainControl: true
        }
      });

      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');

      liveRecorder = new MediaRecorder(liveMediaStream, { mimeType: mime, audioBitsPerSecond: 128000 });

      const lang = $('languageMode')?.value || 'bilingual';
      liveWS = new WebSocket(wsURL(`/ws/stt?lang=${encodeURIComponent(lang)}`));
      liveWS.binaryType = 'arraybuffer';

      liveWS.onopen = () => {
        qs('liveStatus') && (qs('liveStatus').textContent = 'Connected');
        qs('startLiveBtn')?.setAttribute('disabled','');
        qs('stopLiveBtn')?.removeAttribute('disabled');
        liveActive = true;
        liveRecorder.start(300);
      };

      liveRecorder.ondataavailable = async (e) => {
        if (!e.data || !e.data.size) return;
        if (!liveWS || liveWS.readyState !== WebSocket.OPEN) return;
        try {
          const buf = await e.data.arrayBuffer();
          liveWS.send(buf);
        } catch (err) { console.warn('WS send failed', err); }
      };

      liveRecorder.onerror = (e) => { console.error('Recorder error:', e); stopLive(); };

      liveWS.onmessage = (ev) => {
        let j; try { j = JSON.parse(ev.data); } catch { return; }

        if (j.type === 'meter') {
          const m = qs('liveMeter');
          if (m && (j.bytes_in != null || j.bytes_pcm != null)) {
            m.textContent = `in=${j.bytes_in ?? 0}B pcm=${j.bytes_pcm ?? 0}B`;
          }
          return;
        }

        if (j.type === 'partial') {
          const lm = qs('liveMessage'); if (lm) lm.textContent = j.text || '';
          return;
        }

        if (j.type === 'final') {
          const host = qs('liveTranscript');
          if (host) {
            const p = document.createElement('p');
            p.textContent = j.text || '';
            host.appendChild(p);
            host.scrollTo({ top: host.scrollHeight, behavior: 'smooth' });
          }
          const lm = qs('liveMessage'); if (lm) lm.textContent = '';

          // Trigger recommender via SSE (role=live, mode=live)
          try {
            const language = $('languageMode')?.value || 'bilingual';
            const modeSel = $('liveSuggestMode');
            const suggest = (modeSel ? modeSel.value : 'stream');
            const msg = encodeURIComponent(j.text || '');
            const es = new EventSource(`/agent_chat_stream?message=${msg}&lang=${encodeURIComponent(language)}&role=live&mode=live&suggest=${encodeURIComponent(suggest)}`);

            window.__LIVE_QSET = window.__LIVE_QSET || new Set();
            es.onmessage = (event) => {
              try {
                const item = JSON.parse(event.data);
                if (item.type === 'question_recommender') {
                  const qContainer = $('liveSuggestedQuestions') || $('chatSuggestedQuestions');
                  if (!qContainer) return;

                  const en = (item.question?.english || '').trim();
                  const sw = (item.question?.swahili || '').trim();
                  const key = (en + '|' + sw).toLowerCase();

                  if (!en || window.__LIVE_QSET.has(key)) return;
                  window.__LIVE_QSET.add(key);

                  const li = document.createElement('li');
                  li.innerHTML = `<strong>English:</strong> ${en}${sw ? `<br><strong>Swahili:</strong> ${sw}` : ''}`;
                  qContainer.appendChild(li);
                } else {
                  // Non-recommender events for live are ignored here
                }
              } catch(_) {}
            };
            es.onerror = () => es.close();
          } catch (e) { console.warn('live SSE trigger failed', e); }

          if (j.text) {
            window.MH_TRANSCRIPT = (window.MH_TRANSCRIPT ? window.MH_TRANSCRIPT + ' ' : '') + j.text;
            window.debounceScreening && window.debounceScreening();
          }

          // Deduplicate repeated finals
          (function dedupeLive() {
            const host2 = $('liveTranscript');
            if (!host2) return;
            const nodes = host2.querySelectorAll('p');
            if (nodes.length < 2) return;
            const a = nodes[nodes.length-1];
            const b = nodes[nodes.length-2];
            if (a.textContent && b.textContent && a.textContent.trim() === b.textContent.trim()) {
              b.remove();
            }
          })();

          return;
        }

        if (j.type === 'error') {
          const s = qs('liveStatus'); if (s) s.textContent = `Error: ${j.message || 'unknown'}`;
          return;
        }
      };

      liveWS.onerror = () => { const s = qs('liveStatus'); if (s) s.textContent = 'WS error'; stopLive(); };
      liveWS.onclose  = () => { const s = qs('liveStatus'); if (s) s.textContent = 'WS closed'; stopLive(); };

      document.addEventListener('visibilitychange', () => { if (document.hidden && liveActive) stopLive(); }, { once: true });
    } catch (err) {
      console.error('Live start error', err);
      const s = qs('liveStatus');
      if (s) s.textContent = (err?.name === 'NotAllowedError') ? 'Mic denied' : 'Mic/WS failed';
      stopLive();
    }
  }

  function stopLive() {
    try {
      clearTimeout(window._liveStopTimeout);
      if (liveRecorder && liveRecorder.state === 'recording') liveRecorder.stop();
      if (liveMediaStream) liveMediaStream.getTracks().forEach(t => t.stop());
      // Send stop so server flushes and sends final transcript before we close (then wait for finals or 6s)
      if (liveWS && liveWS.readyState === WebSocket.OPEN) {
        const s = qs('liveStatus'); if (s) s.textContent = 'Stopping…';
        try { liveWS.send(JSON.stringify({ type: 'stop' })); } catch (e) {}
        window._liveStopTimeout = setTimeout(() => {
          if (liveWS && liveWS.readyState === WebSocket.OPEN) liveWS.close(1000, 'stop');
          stopLive();
        }, 6000);
        return;
      }
      if (liveWS) liveWS.close(1000, 'stop');

      // If user picked "final", auto trigger final suggestions at stop
      try {
        const sel = $('liveSuggestMode');
        if (sel && sel.value === 'final') {
          const language = $('languageMode')?.value || 'bilingual';
          const es = new EventSource(`/agent_chat_stream?message=${encodeURIComponent('[Finalize]')}&lang=${encodeURIComponent(language)}&role=finalize&mode=live&suggest=final`);

          window.__LIVE_QSET = window.__LIVE_QSET || new Set();
          es.onmessage = (event) => {
            try {
              const item = JSON.parse(event.data);
              if (item.type === 'question_recommender') {
                const qContainer = $('liveSuggestedQuestions') || $('chatSuggestedQuestions');
                if (!qContainer) return;

                const en = (item.question?.english || '').trim();
                const sw = (item.question?.swahili || '').trim();
                const key = (en + '|' + sw).toLowerCase();

                if (!en || window.__LIVE_QSET.has(key)) return;
                window.__LIVE_QSET.add(key);

                const li = document.createElement('li');
                li.innerHTML = `<strong>English:</strong> ${en}${sw ? `<br><strong>Swahili:</strong> ${sw}` : ''}`;
                qContainer.appendChild(li);
              }
            } catch {}
          };
          es.onerror = () => es.close();
        }
      } catch {}
    } catch (e) {
      console.warn('stopLive error', e);
    } finally {
      liveRecorder = null; liveMediaStream = null; liveWS = null; liveActive = false;
      const s = qs('liveStatus'); if (s) s.textContent = 'Stopped';
      qs('startLiveBtn')?.removeAttribute('disabled');
      qs('stopLiveBtn')?.setAttribute('disabled','');
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    on($('startLiveBtn'), 'click', (e) => { e.preventDefault(); startLive(); });
    on($('stopLiveBtn'),  'click', (e) => { e.preventDefault(); stopLive();  });
  });

  on($('chatMode'), 'change', (e) => {
    const turn = $('turnPane');
    const live = $('livePane');
    if (turn && live) {
      if (e.target.value === 'live') { hide(turn); show(live); }
      else { show(turn); hide(live); if (liveActive) stopLive(); }
    }
    // New conversation per mode so Real Actors / Simulated / Live don't mix in one thread
    if (window.resetConversationAndUI) window.resetConversationAndUI().catch(function () {});
  });
})();

// ---------------------------------------------
// Question Bank wiring — NO auto-load on page open (kept)
// ---------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const elCat = $('qbCategory');
  const elQ   = $('qbQuery');
  const btnS  = $('qbSearchBtn');
  const btnP  = $('qbPrintBtn');
  const btnE  = $('qbExportBtn');
  const host  = $('qbResults');

  if (!host || (!btnS && !btnP && !btnE)) return;

  function setActionsEnabled(enabled) {
    if (btnP) btnP.disabled = !enabled;
    if (btnE) btnE.disabled = !enabled;
  }
  function hasResults() {
    return !!host.querySelector('.border-bottom');
  }
  function renderItems(items) {
    if (!items || !items.length) {
      host.innerHTML = '<div class="text-muted">No questions found.</div>';
      setActionsEnabled(false);
      return;
    }
    const html = items.map(it => `
      <div class="border-bottom py-2">
        <div class="small text-muted">${it.id} · ${it.category || ''}</div>
        <div><strong>English:</strong> ${it.english || ''}</div>
        <div><strong>Swahili:</strong> ${it.swahili || ''}</div>
      </div>`).join('');
    host.innerHTML = html;
    setActionsEnabled(true);
  }

  async function doSearch() {
    host.innerHTML = '<div class="text-muted">Searching…</div>';
    setActionsEnabled(false);
    const cat = elCat?.value || '';
    const q   = (elQ?.value || '').trim();
    const body = { query: q || (cat ? cat : ''), category: cat || null, k: 50 };

    try {
      const r = await fetch('/questions/search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const j = await r.json();
      if (j.error) {
        host.innerHTML = `<div class="text-danger">Search failed: ${j.error}</div>`;
        return;
      }
      renderItems(j.items || []);
    } catch (e) {
      host.innerHTML = `<div class="text-danger">Search failed.</div>`;
    }
  }

  on(btnS, 'click', (e) => { e.preventDefault(); doSearch(); });

  on(btnP, 'click', (e) => {
    e.preventDefault();
    if (!hasResults()) return;
    const cat = elCat?.value || '';
    const url = `/questions/print${cat ? ('?category=' + encodeURIComponent(cat)) : ''}`;
    window.open(url, '_blank');
  });

  on(btnE, 'click', (e) => {
    e.preventDefault();
    if (!hasResults()) return;
    const cat = elCat?.value || '';
    const q   = (elQ?.value || '').trim();
    const params = new URLSearchParams();
    if (cat) params.set('category', cat);
    if (q) params.set('q', q);
    window.location.href = `/questions/export?${params.toString()}`;
  });

  host.innerHTML = '<div class="text-muted">Search to see questions.</div>';
  setActionsEnabled(false);
});

// ---------------------------------------------
// Controlling user role views (kept + consistent role defaults)
// ---------------------------------------------
function adjustChatOptions(user) {
  const chatModeSelect = $('chatMode');
  const modeBadge = $('modeBadge');
  const clinicianBtn = $('roleClinicianBtn');
  const patientBtn = $('rolePatientBtn');
  const currentRoleDisplay = $('currentRoleDisplay');
  const modeTipText = $('modeTipText');
  const questionBankSection = $('questionBankSection');
  const suggestedquestionSection = $('suggestedQuestionSection');
  const questionRecommenderBrand = $('questionRecommender');
  const agentsConversationHeader = $('agentsConversationHeader');
  const finalizeBtn = $('finalizeBtn');
  const liveMic = $('livePane');
  const turnPane = $('turnPane');

  if (!chatModeSelect) return;

  const roles = user?.roles || [];
  const isAdmin = roles.includes('admin');
  const isClinician = roles.includes('clinician');
  const isPatient = roles.includes('patient');

  if (isPatient && !isAdmin && !isClinician) {
    window.APP_FORCED.mode = 'simulated';
    window.APP_FORCED.role = 'patient';

    chatModeSelect.value = 'simulated';
    chatModeSelect.style.display = 'none';

    if (modeBadge) modeBadge.textContent = 'Simulated';
    if (modeTipText) modeTipText.textContent = 'Chat with Clinician';

    // Hide role selector (patients can't switch)
    if (clinicianBtn) clinicianBtn.style.display = 'none';
    if (patientBtn) patientBtn.style.display = 'none';

    window.CURRENT_ROLE = "patient";
    if (currentRoleDisplay) currentRoleDisplay.textContent = '(Current: Patient)';

    if (questionBankSection) questionBankSection.style.display = 'none';
    if (suggestedquestionSection) suggestedquestionSection.style.display = 'none';

    // Patients should NOT see Summarize/Finalize
    if (finalizeBtn) finalizeBtn.style.display = 'none';

    if (questionRecommenderBrand) questionRecommenderBrand.style.display = 'none';
    if (agentsConversationHeader) agentsConversationHeader.textContent = 'Conversation';

    try { chatModeSelect.dispatchEvent(new Event('change', { bubbles: true })); } catch (_) {}
  }
  else if (isClinician && !isAdmin) {
    // Clinician: default to live mic
    chatModeSelect.style.display = 'none';
    if (modeBadge) modeBadge.textContent = 'Live Mic';

    if (turnPane) turnPane.style.display = 'none';
    if (liveMic) liveMic.style.display = 'block';

    if (modeTipText) modeTipText.textContent = 'Chat with Patient';
    if (agentsConversationHeader) agentsConversationHeader.textContent = 'Conversation';

    // Clinicians can finalize
    if (finalizeBtn) finalizeBtn.style.display = '';

    // Clinician is clinician
    window.CURRENT_ROLE = "clinician";
    if (currentRoleDisplay) currentRoleDisplay.textContent = '(Current: Clinician)';

    // Hide role toggle in clinician live view
    if (clinicianBtn) clinicianBtn.style.display = 'none';
    if (patientBtn) patientBtn.style.display = 'none';
  }
  else {
    // Admin/other: allow switching freely
    window.APP_FORCED.mode = null;
    window.APP_FORCED.role = null;

    if (modeBadge) modeBadge.textContent = chatModeSelect.options[chatModeSelect.selectedIndex].text;
    chatModeSelect.addEventListener('change', () => {
      if (modeBadge) modeBadge.textContent = chatModeSelect.options[chatModeSelect.selectedIndex].text;
    });

    if (questionBankSection) questionBankSection.style.display = '';
    if (questionRecommenderBrand) questionRecommenderBrand.style.display = '';
    if (finalizeBtn) finalizeBtn.style.display = '';

    // Ensure role toggle is visible in real actors
    if (clinicianBtn) clinicianBtn.style.display = '';
    if (patientBtn) patientBtn.style.display = '';

    if (currentRoleDisplay) currentRoleDisplay.textContent =
      `(Current: ${window.CURRENT_ROLE === "patient" ? "Patient" : "Clinician"})`;
  }
}

// ---------------------------------------------
// Set-password page flow (kept)
// ---------------------------------------------
const setPasswordForm = $('setPasswordForm');
on(setPasswordForm, 'submit', async (e) => {
  e.preventDefault();

  const tempPassword = $('tempPassword')?.value.trim();
  const newPassword = $('newPassword')?.value.trim();
  const confirmPassword = $('confirmPassword')?.value.trim();
  const url = setPasswordForm?.dataset?.url;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', "X-CSRFToken": csrfToken },
      body: JSON.stringify({ temp_password: tempPassword, new_password: newPassword, confirm_password: confirmPassword })
    }).then(r => r.json());

    if (res.ok) {
      alert(res.message);
      location.href = '/';
    } else {
      const el = $('auth-error');
      if (el) { el.textContent = res.error || 'Password reset failed'; el.classList.remove('d-none'); }
      await loadCsrf();
    }
  } catch {
    const el = $('auth-error');
    if (el) { el.textContent = 'Network error'; el.classList.remove('d-none'); }
  }
});

// ---------------------------------------------
// OTP verification flow (kept)
// ---------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  const params = new URLSearchParams(window.location.search);
  const email = params.get("email");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  const otpInputs = document.querySelectorAll(".otp-input");
  otpInputs.forEach((input, i) => {
    input.addEventListener("input", () => {
      if (input.value.length === 1 && i < otpInputs.length - 1) otpInputs[i + 1].focus();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !input.value && i > 0) otpInputs[i - 1].focus();
    });
  });

  const otpForm = document.getElementById("otpForm");
  if (otpForm) {
    otpForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const otp_code = Array.from(otpInputs).map(inp => inp.value).join("");

      try {
        const res = await fetch(otpForm.dataset.url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
          body: JSON.stringify({ email, otp_code })
        });
        const data = await res.json();

        const el = document.getElementById("auth-error");
        if (data.ok) {
          alert(data.message);
          window.location.href = "/";
        } else if (el) {
          el.textContent = data.error || "Verification failed";
          el.classList.remove("d-none");
        }
      } catch {
        const el = document.getElementById("auth-error");
        if (el) { el.textContent = "Network error"; el.classList.remove("d-none"); }
      }
    });
  }
});

// ---------------------------------------------
// Reset password email flow (kept)
// ---------------------------------------------
const resetPassEmailForm = $('resetEmailForm');
on(resetPassEmailForm, 'submit', async (e) => {
  e.preventDefault();

  const email = $('reset-email')?.value.trim();
  const url = resetPassEmailForm?.dataset?.url;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', "X-CSRFToken": csrfToken },
      body: JSON.stringify({ email: email})
    }).then(r => r.json());

    if (res.ok) {
      alert(res.message);
    } else {
      const el = $('auth-error');
      if (el) { el.textContent = res.error || 'Password reset failed'; el.classList.remove('d-none'); }
      await loadCsrf();
    }
  } catch {
    const el = $('auth-error');
    if (el) { el.textContent = 'Network error'; el.classList.remove('d-none'); }
  }
});

// ---------------------------------------------
// Reset password with token flow (kept)
// ---------------------------------------------
const resetPasswordForm = $('resetPasswordForm');
on(resetPasswordForm, 'submit', async (e) => {
  e.preventDefault();
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  const newPassword = $('newPassword')?.value.trim();
  const confirmPassword = $('confirmPassword')?.value.trim();
  const url = resetPasswordForm?.dataset?.url;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', "X-CSRFToken": csrfToken },
      body: JSON.stringify({ token: token, new_password: newPassword, confirm_password: confirmPassword })
    }).then(r => r.json());

    if (res.ok) {
      alert(res.message);
      location.href = '/';
    } else {
      const el = $('auth-error');
      if (el) { el.textContent = res.error || 'Password reset failed'; el.classList.remove('d-none'); }
      await loadCsrf();
    }
  } catch {
    const el = $('auth-error');
    if (el) { el.textContent = 'Network error'; el.classList.remove('d-none'); }
  }
});
