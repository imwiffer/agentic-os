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

// Cards shown before the API answers, so the numbers visibly count up from $0.
const SKELETON = {
    Overview: [
        { id: 'cash_on_hand', label: 'Cash on hand', value: 0, format: 'currency', icon: 'fas fa-wallet', color: 'green' },
        { id: 'monthly_income', label: 'Monthly income', value: 0, format: 'currency', icon: 'fas fa-chart-line', color: 'blue' },
        { id: 'revenue_target', label: 'Revenue target', value: 0, format: 'currency', icon: 'fas fa-bullseye', color: 'purple' },
    ],
};

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
    const onDashboard = s.currentTab === 'Dashboard';
    $('view-dashboard').classList.toggle('hidden', !onDashboard);
    $('view-generic').classList.toggle('hidden', onDashboard);
    $('url-bar').textContent = `localhost:8000/AgenticOS/${s.currentTab}`;
    if (!onDashboard) {
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
    $('panel-model-host').textContent = m.location === 'cloud' ? 'Via OpenAI-compatible API' : 'On this machine';
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
    render(Object.fromEntries(Object.keys(appState).map((k) => [k, appState[k]])), appState); // first paint
}

// ---------- effects ----------
export function highlightCard(id) {
    const el = document.querySelector(`[data-card="${id}"]`);
    if (!el) return false;
    el.classList.remove('card-highlight');
    void el.offsetWidth; // restart animation
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
