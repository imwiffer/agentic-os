// Renderer: maps state changes onto the DOM (finds elements by ID and updates them).
import { appState, subscribe } from './store.js';

const $ = (id) => document.getElementById(id);

const STATUS_TEXT = {
    idle: 'Standing by',
    listening: 'Listening…',
    transcribing: 'Transcribing…',
    thinking: 'Analyzing…',
    speaking: 'Responding…',
    offline: 'Brain offline',
};
const PROCESSING = new Set(['transcribing', 'thinking', 'speaking']);

const VIEW_MAP = {
    Dashboard: 'view-dashboard',
    Inbox: 'view-inbox',
    Calendar: 'view-calendar',
    Memory: 'view-memory',
    Agents: 'view-agents',
    Settings: 'view-settings',
};

const SKELETON = {
    Overview: [
        { id: 'cash_on_hand', label: 'Cash on hand', value: 0, format: 'currency', icon: 'fas fa-wallet', color: 'green' },
        { id: 'monthly_income', label: 'Monthly income', value: 0, format: 'currency', icon: 'fas fa-chart-line', color: 'blue' },
        { id: 'revenue_target', label: 'Revenue target', value: 0, format: 'currency', icon: 'fas fa-bullseye', color: 'purple' },
    ],
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---------- formatting ----------
export function convert(card, s = appState) {
    return card.format === 'currency' ? card.value * (s.rates[s.currency] ?? 1) : card.value;
}

export function formatValue(format, value, s = appState) {
    if (format === 'currency') {
        const digits = s.currency === 'JPY' ? 0 : 2;
        return new Intl.NumberFormat('en-US', {
            style: 'currency', currency: s.currency, minimumFractionDigits: digits, maximumFractionDigits: digits,
        }).format(value);
    }
    if (format === 'percent') return `${value.toFixed(1)}%`;
    return new Intl.NumberFormat('en-US').format(Math.round(value));
}

export function animateValue(el, from, to, render, duration = 1400) {
    cancelAnimationFrame(el._raf);
    const start = performance.now();
    const tick = (now) => {
        const t = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - t, 3);
        const v = from + (to - from) * eased;
        el.textContent = render(v);
        el.dataset.current = v;
        if (t < 1) el._raf = requestAnimationFrame(tick);
    };
    el._raf = requestAnimationFrame(tick);
}

// ---------- renderers ----------
function renderSidebar(s) {
    document.querySelectorAll('[data-tab]').forEach((el) => el.classList.toggle('active', el.dataset.tab === s.currentTab));
    const activeView = VIEW_MAP[s.currentTab] || 'view-generic';
    for (const id of [...Object.values(VIEW_MAP), 'view-generic']) {
        $(id)?.classList.toggle('hidden', id !== activeView);
    }
    $('url-bar').textContent = `${location.host || 'tailnet'}/AgenticOS/${s.currentTab}`;
    if (activeView === 'view-generic') {
        const link = document.querySelector(`[data-tab="${s.currentTab}"] i`);
        $('generic-title').textContent = s.currentTab;
        $('generic-icon').className = `${link?.className.replace('w-4 text-center', '') || 'fas fa-cube'} text-4xl text-purple-400`;
    }
}

function cardHTML(card) {
    return `
    <div class="stat-card bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl p-5 border border-white/10 hover:border-purple-500/50 cursor-pointer group" data-card="${card.id}">
        <div class="flex justify-between items-start mb-4">
            <div class="w-10 h-10 rounded-full bg-[#222] flex items-center justify-center text-${card.color}-400 group-hover:scale-110 transition">
                <i class="${card.icon}"></i>
            </div>
            <span class="text-[10px] text-gray-500" data-badge>${appState.stats ? 'Live' : 'Demo'}</span>
        </div>
        <div class="text-sm text-gray-400 mb-1">${card.label}</div>
        <div class="text-2xl font-light text-white" data-value data-current="0">${formatValue(card.format, 0)}</div>
    </div>`;
}

function renderCards(s) {
    document.querySelectorAll('[data-btab]').forEach((el) => el.classList.toggle('active', el.dataset.btab === s.businessTab));
    const cards = s.stats?.tabs?.[s.businessTab] ?? SKELETON[s.businessTab] ?? [];
    const grid = $('stat-cards');
    const currentIds = [...grid.querySelectorAll('[data-card]')].map((el) => el.dataset.card).join();
    if (currentIds !== cards.map((c) => c.id).join()) {
        grid.innerHTML = cards.map(cardHTML).join('') ||
            '<div class="text-gray-500 text-xs col-span-3">No data for this tab yet.</div>';
    }
    cards.forEach((card) => {
        const root = grid.querySelector(`[data-card="${card.id}"]`);
        const el = root.querySelector('[data-value]');
        root.querySelector('[data-badge]').textContent = s.stats ? 'Live' : 'Demo';
        animateValue(el, parseFloat(el.dataset.current) || 0, convert(card, s), (v) => formatValue(card.format, v, s));
    });
}

function renderUsage(s) {
    const usage = s.stats?.usage ?? [];
    $('usage-cards').innerHTML = usage.map((u) => `
        <div class="glass-panel rounded-xl p-4 flex items-center justify-between hover:border-gray-600 transition cursor-pointer group">
            <div class="flex items-center gap-4">
                <div class="w-16 h-16 rounded-full bg-[#333] relative overflow-hidden shadow-lg">
                    <img src="${u.image}" alt="" class="w-full h-full object-cover opacity-80 group-hover:scale-110 transition duration-500">
                </div>
                <div>
                    <div class="text-2xl font-light text-white" data-usage="${u.id}" data-current="0">0%</div>
                    <div class="text-xs text-gray-400">${u.label}</div>
                </div>
            </div>
        </div>`).join('');
    usage.forEach((u) => {
        const el = document.querySelector(`[data-usage="${u.id}"]`);
        animateValue(el, 0, u.percent, (v) => `${Math.round(v)}%`);
    });
    if (s.stats) {
        $('data-source').textContent = 'Live from SQLite';
        $('data-dot').className = 'w-2 h-2 rounded-full bg-green-500';
    }
}

function renderUser(s) {
    if (!s.user) return;
    $('user-name').textContent = s.user.name;
    $('user-title').textContent = s.user.title || 'Personal Profile';
}

export function currentModel(s = appState) {
    return s.models.find((m) => m.id === s.model) || { id: s.model, name: s.model || '—', location: 'local', status: '' };
}

function renderModels(s) {
    const m = currentModel(s);
    const loc = m.location === 'cloud' ? 'Cloud' : 'Local';
    $('model-label').textContent = `${loc}: ${m.name}`;
    $('header-location').textContent = loc;
    $('panel-model-location').textContent = loc;
    $('panel-model-name').textContent = m.name;
    $('panel-model-host').textContent = m.location === 'cloud' ? 'Via OpenAI-compatible API' : 'On VPS (Ollama)';
    $('answer-model').textContent = m.name;
    document.querySelectorAll('[data-mode]').forEach((el) => el.classList.toggle('active', el.dataset.mode === m.location));

    const groups = [['local', 'Local Models'], ['cloud', 'Cloud Models']];
    $('model-list').innerHTML = groups.map(([key, title]) => {
        const items = s.models.filter((x) => x.location === key);
        if (!items.length) return '';
        return `<div class="p-2 text-[10px] font-bold text-gray-500 uppercase">${title}</div>` + items.map((x) => `
            <div class="hover:bg-white/10 cursor-pointer text-xs flex items-center gap-2 p-2 border-l-2 ${x.id === s.model ? 'border-white bg-white/5' : 'border-transparent'}"
                 data-model-id="${x.id}" title="${x.id}">
                <div class="w-2 h-2 rounded-full ${x.available ? 'bg-green-500' : 'bg-gray-600'}"></div>
                <span class="flex-1 truncate">${x.name}</span>
                <span class="text-[9px] text-gray-500">${x.available ? x.size || '' : x.status || ''}</span>
            </div>`).join('');
    }).join('');
}

function renderInbox(s) {
    const badge = $('inbox-badge');
    badge.textContent = s.unreadInbox;
    badge.classList.toggle('hidden', !s.unreadInbox);
    const box = $('inbox-list');
    if (!s.inbox.length) {
        box.innerHTML = '<div class="glass-panel rounded-xl p-6 text-gray-500 text-xs">Inbox is empty. Scheduled task reports and agent notifications appear here.</div>';
        return;
    }
    box.innerHTML = s.inbox.map((item) => `
        <div class="glass-panel rounded-xl p-4 border ${item.read ? 'border-white/5 opacity-75' : 'border-purple-500/40'}">
            <div class="flex items-center justify-between gap-2 mb-1">
                <span class="font-medium text-white text-xs">${esc(item.title)}</span>
                <span class="text-[10px] text-gray-500 font-mono">${esc(item.source)} · ${esc(item.created_at)}</span>
            </div>
            <div class="text-xs text-gray-300 whitespace-pre-wrap">${esc(item.body)}</div>
        </div>`).join('');
}

function renderJobs(s) {
    const box = $('job-list');
    if (!s.jobs.length) {
        box.innerHTML = '<div class="glass-panel rounded-xl p-6 text-gray-500 text-xs">No scheduled tasks yet. Add one above or tell Jarvis "every day at 08:00 check server health".</div>';
        return;
    }
    box.innerHTML = s.jobs.map((j) => `
        <div class="glass-panel rounded-xl p-4 flex items-center justify-between gap-4">
            <div class="min-w-0">
                <div class="text-xs font-medium text-white">${esc(j.description || j.name)}</div>
                <div class="text-[11px] text-gray-400 truncate">${esc(j.prompt)}</div>
                <div class="text-[10px] text-gray-500 font-mono mt-1">last: ${esc(j.last_run_local || 'never')} (${esc(j.last_status || 'pending')})</div>
            </div>
            <div class="flex items-center gap-2 shrink-0">
                <button data-run-job="${j.id}" class="glass-button px-2.5 py-1 rounded text-[11px] text-emerald-300"><i class="fas fa-play mr-1"></i>Run</button>
                <button data-del-job="${j.id}" class="glass-button px-2.5 py-1 rounded text-[11px] text-red-300"><i class="fas fa-trash"></i></button>
            </div>
        </div>`).join('');
}

function renderMemories(s) {
    const box = $('memory-list');
    if (!s.memories.length) {
        box.innerHTML = '<div class="glass-panel rounded-xl p-6 text-gray-500 text-xs">No memories stored yet. Say "remember that…" or add one above.</div>';
        return;
    }
    box.innerHTML = s.memories.map((m) => `
        <div class="glass-panel rounded-xl p-3 flex items-center justify-between gap-3">
            <div class="min-w-0">
                <div class="text-xs text-gray-200">${esc(m.text)}</div>
                <div class="text-[10px] text-gray-500 font-mono mt-0.5">#${m.id} · ${esc(m.tags || 'untagged')} · ${esc(m.created_at)}</div>
            </div>
            <button data-del-memory="${m.id}" class="glass-button px-2.5 py-1 rounded text-[11px] text-red-300 shrink-0"><i class="fas fa-trash"></i></button>
        </div>`).join('');
}

function renderAgents(s) {
    const active = s.roles.find((r) => r.id === s.role);
    $('role-pill').textContent = s.role;
    $('header-role').textContent = active?.title || s.role.toUpperCase();
    $('role-cards').innerHTML = s.roles.map((r) => `
        <div data-select-role="${r.id}" class="glass-panel rounded-xl p-4 cursor-pointer border transition ${r.id === s.role ? 'border-purple-500 bg-purple-950/20' : 'border-white/10 hover:border-purple-500/40'}">
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2 text-white font-semibold text-xs">
                    <i class="${esc(r.icon)} text-purple-400"></i> ${esc(r.title)}
                </div>
                <span class="text-[10px] font-mono ${r.id === s.role ? 'text-emerald-400' : 'text-gray-500'}">${r.id === s.role ? 'ACTIVE' : 'Select'}</span>
            </div>
            <p class="text-[11px] text-gray-400 mb-3">You are ${esc(r.description)}</p>
            <div class="flex flex-wrap gap-1">
                ${(r.tools || []).map((t) => `<span class="px-1.5 py-0.5 rounded bg-white/5 text-[10px] font-mono text-gray-400">${esc(t)}</span>`).join('')}
            </div>
        </div>`).join('');
}

function renderSteps(s) {
    const box = $('step-trace');
    if (!s.steps.length) {
        box.innerHTML = '<div class="text-gray-500">No tool calls recorded in this session yet.</div>';
        return;
    }
    box.innerHTML = s.steps.slice(-20).reverse().map((st) => `
        <div class="p-2 rounded bg-black/40 border border-white/5">
            <div class="flex items-center justify-between">
                <span class="text-purple-300">${esc(st.role || 'jarvis')} → ${esc(st.tool)}(${esc(JSON.stringify(st.args || {}))})</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded ${st.status === 'done' ? 'bg-emerald-900/40 text-emerald-300' : st.status === 'running' ? 'bg-sky-900/40 text-sky-300' : 'bg-red-900/40 text-red-300'}">${esc(st.status)}</span>
            </div>
            ${st.result ? `<div class="text-[11px] text-gray-400 mt-1 whitespace-pre-wrap">${esc(st.result)}</div>` : ''}
        </div>`).join('');
}

function renderSettings(s) {
    $('tool-mode-badge').textContent = `mode: ${s.toolMode}`;
    const sys = s.system;
    if (sys) {
        const upH = sys.uptime_s ? `${Math.floor(sys.uptime_s / 86400)}d ${Math.floor((sys.uptime_s % 86400) / 3600)}h` : '—';
        $('system-cards').innerHTML = [
            ['Host / OS', `${sys.hostname} (${sys.cpu_count} vCPU)`, sys.os],
            ['CPU & Load', `${sys.cpu_percent ?? 0}%`, sys.load_avg ? `load ${sys.load_avg.join(', ')}` : `uptime ${upH}`],
            ['Memory', `${sys.mem_percent ?? 0}%`, `swap ${sys.swap_percent ?? 0}%`],
            ['Disk & Ollama', `${sys.disk_percent}% used`, sys.ollama_loaded == null ? 'Ollama offline' : `loaded: ${sys.ollama_loaded.join(', ') || 'idle'}`],
        ].map(([k, v, sub]) => `
            <div class="glass-panel rounded-xl p-4">
                <div class="text-[10px] text-gray-500 uppercase">${esc(k)}</div>
                <div class="text-lg text-white font-medium mt-1">${esc(v)}</div>
                <div class="text-[11px] text-gray-400 font-mono mt-0.5">${esc(sub)}</div>
            </div>`).join('');
    }
    const auditBox = $('audit-list');
    if (!s.audit.length) {
        auditBox.innerHTML = '<div class="text-gray-500">No tool calls audited yet.</div>';
        return;
    }
    auditBox.innerHTML = s.audit.map((a) => `
        <div class="p-2 rounded bg-black/40 border border-white/5 flex items-start justify-between gap-3">
            <div class="min-w-0">
                <div><span class="text-gray-500">${esc(a.ts)}</span> · <span class="text-purple-300">${esc(a.actor)}/${esc(a.role)}</span> · <span class="text-white">${esc(a.tool)}</span> <span class="text-gray-400">${esc(a.args)}</span></div>
                <div class="text-gray-400 truncate">${esc(a.result)}</div>
            </div>
            <span class="px-1.5 py-0.5 rounded text-[10px] shrink-0 ${a.ok ? 'bg-emerald-900/40 text-emerald-300' : 'bg-red-900/40 text-red-300'}">${esc(a.decision)}</span>
        </div>`).join('');
}

function renderModals(s) {
    const req = s.approvalRequest;
    $('approval-modal').classList.toggle('hidden', !req);
    if (req) {
        $('approval-reason').textContent = req.reason || 'Approval requested';
        $('approval-tool').textContent = req.tool;
        $('approval-args').textContent = JSON.stringify(req.args, null, 2);
    }
    $('login-modal').classList.toggle('hidden', !(s.authRequired && !s.authenticated));
}

function renderStatus(s) {
    document.body.dataset.status = s.status;
    $('agent-status').textContent = STATUS_TEXT[s.status] ?? s.status;
    $('hud-status').textContent = `AGENT_LOGIC_${s.status.toUpperCase()}`;
    $('thinking-overlay').classList.toggle('show', s.status === 'thinking' || s.status === 'transcribing');
    $('thinking-text').textContent = STATUS_TEXT[s.status] ?? '';
    $('ai-models-section').classList.toggle('processing', PROCESSING.has(s.status));
}

function renderMic(s) {
    $('voice-btn').classList.toggle('voice-active', s.voiceActive);
    $('voice-label').textContent = s.voiceActive ? 'Listening' : 'Voice';
    $('mic-icon').classList.toggle('listening', s.voiceActive && s.listening);
}

function renderConnection(s) {
    $('conn-dot').className = `w-1.5 h-1.5 rounded-full ${s.connected ? 'bg-green-500' : 'bg-red-500'}`;
    $('conn-label').textContent = s.connected ? 'brain online' : 'offline';
    $('connector-status').innerHTML = s.connected
        ? '<div class="w-1.5 h-1.5 rounded-full bg-green-500"></div> <span class="text-green-400">On</span>'
        : '<div class="w-1.5 h-1.5 rounded-full bg-red-500"></div> <span class="text-red-400">Off</span>';
}

function renderTokens(s) {
    const el = $('token-counter');
    animateValue(el, parseFloat(el.dataset.current) || 0, s.tokens, (v) => Math.round(v).toLocaleString('en-US'), 900);
}

function renderCurrencies(s) {
    const sel = $('currency-select');
    sel.innerHTML = s.currencies.map((c) => `<option value="${c}">${c}</option>`).join('');
    sel.value = s.currency;
}

function renderToolCounts(s) {
    document.querySelectorAll('[data-tool-count]').forEach((el) => { el.textContent = s.toolCounts[el.dataset.toolCount] ?? 0; });
}

function render(changed, s) {
    const has = (...keys) => keys.some((k) => k in changed);
    if (has('currentTab')) renderSidebar(s);
    if (has('businessTab', 'stats', 'currency', 'rates')) renderCards(s);
    if (has('stats')) renderUsage(s);
    if (has('user')) renderUser(s);
    if (has('models', 'model')) renderModels(s);
    if (has('inbox', 'unreadInbox')) renderInbox(s);
    if (has('jobs')) renderJobs(s);
    if (has('memories')) renderMemories(s);
    if (has('roles', 'role')) renderAgents(s);
    if (has('steps')) renderSteps(s);
    if (has('system', 'audit', 'toolMode')) renderSettings(s);
    if (has('approvalRequest', 'authRequired', 'authenticated')) renderModals(s);
    if (has('status')) renderStatus(s);
    if (has('voiceActive', 'listening')) renderMic(s);
    if (has('connected')) renderConnection(s);
    if (has('tokens')) renderTokens(s);
    if (has('currencies')) renderCurrencies(s);
    if (has('currency')) $('currency-select').value = s.currency;
    if (has('engine')) $('engine-label').textContent = `engine: ${s.engine}`;
    if (has('sttEngine')) $('stt-label').textContent = `STT: ${s.sttEngine}`;
    if (has('toolCounts')) renderToolCounts(s);
}

export function initRenderer() {
    subscribe(render);
    render(Object.fromEntries(Object.keys(appState).map((k) => [k, appState[k]])), appState);
}

// ---------- effects ----------
export function highlightCard(id) {
    const el = document.querySelector(`[data-card="${id}"]`);
    if (!el) return false;
    el.classList.remove('card-highlight');
    void el.offsetWidth;
    el.classList.add('card-highlight');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return true;
}

export function flash(el) {
    if (!el) return;
    el.classList.remove('ai-flash');
    void el.offsetWidth;
    el.classList.add('ai-flash');
}

// ---------- HUD log ----------
let interimEl = null;

export function log(role, text, meta = '') {
    const box = $('hud-log');
    clearInterim();
    const line = document.createElement('div');
    line.className = `hud-line ${role}`;
    line.textContent = text;
    if (meta) {
        const m = document.createElement('span');
        m.className = 'hud-meta';
        m.textContent = meta;
        line.appendChild(m);
    }
    box.appendChild(line);
    while (box.children.length > 80) box.firstChild.remove();
    box.scrollTop = box.scrollHeight;
}

export function logInterim(text) {
    const box = $('hud-log');
    if (!interimEl) {
        interimEl = document.createElement('div');
        interimEl.className = 'hud-line interim';
        box.appendChild(interimEl);
    }
    interimEl.textContent = text;
    box.scrollTop = box.scrollHeight;
}

export function clearInterim() {
    interimEl?.remove();
    interimEl = null;
}
