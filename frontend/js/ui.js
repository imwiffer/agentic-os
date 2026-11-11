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
    Chat: 'view-chat',
    Design: 'view-design',
    Website: 'view-website',
    Inbox: 'view-inbox',
    Calendar: 'view-calendar',
    Memory: 'view-memory',
    Agents: 'view-agents',
    Settings: 'view-settings',
    IDE: 'view-ide',
    Extensions: 'view-extensions',
    Voice: 'view-voice',
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
    if (!el) return;
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
    const activeView = VIEW_MAP[s.currentTab] || 'view-dashboard';
    for (const id of Object.values(VIEW_MAP)) {
        $(id)?.classList.toggle('hidden', id !== activeView);
    }
    $('url-bar').textContent = `${location.host || 'tailnet'}/AgenticOS/${s.currentTab}`;
}

function cardHTML(card) {
    return `
    <div class="stat-card bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl p-5 border border-white/10 hover:border-purple-500/50 cursor-pointer group" data-card="${card.id}" data-label="${esc(card.label)}" data-raw-value="${card.value}" data-format="${card.format}" title="Click to edit ${esc(card.label)}">
        <div class="flex justify-between items-start mb-4">
            <div class="w-10 h-10 rounded-full bg-[#222] flex items-center justify-center text-${card.color}-400 group-hover:scale-110 transition">
                <i class="${card.icon}"></i>
            </div>
            <span class="text-[10px] text-gray-500" data-badge>${appState.stats ? 'Live · Edit' : 'Demo'}</span>
        </div>
        <div class="text-sm text-gray-400 mb-1">${esc(card.label)}</div>
        <div class="text-2xl font-light text-white" data-value data-current="0">${formatValue(card.format, 0)}</div>
    </div>`;
}

function renderCards(s) {
    document.querySelectorAll('[data-btab]').forEach((el) => el.classList.toggle('active', el.dataset.btab === s.businessTab));
    const cards = s.stats?.tabs?.[s.businessTab] ?? SKELETON[s.businessTab] ?? [];
    const grid = $('stat-cards');
    if (!grid) return;
    const currentIds = [...grid.querySelectorAll('[data-card]')].map((el) => el.dataset.card).join();
    if (currentIds !== cards.map((c) => c.id).join()) {
        grid.innerHTML = cards.map(cardHTML).join('') ||
            '<div class="text-gray-500 text-xs col-span-3">No data for this tab yet.</div>';
    }
    cards.forEach((card) => {
        const root = grid.querySelector(`[data-card="${card.id}"]`);
        if (!root) return;
        root.dataset.rawValue = card.value;
        const el = root.querySelector('[data-value]');
        root.querySelector('[data-badge]').textContent = s.stats ? 'Live · Edit' : 'Demo';
        animateValue(el, parseFloat(el.dataset.current) || 0, convert(card, s), (v) => formatValue(card.format, v, s));
    });
}

function renderUsage(s) {
    const usage = s.stats?.usage ?? [];
    const box = $('usage-cards');
    if (!box) return;
    const vpsConn = (s.connectors || []).find((c) => c.id === 'ollama');
    const gpuConn = (s.connectors || []).find((c) => c.id === 'main_pc_gpu');
    box.innerHTML = usage.map((u, idx) => {
        const isCpu = idx === 0;
        const icon = isCpu ? 'fas fa-server text-sky-400' : 'fas fa-microchip text-purple-400';
        const sub = isCpu
            ? (vpsConn?.detail || `Node ${s.ollamaUrl ? s.ollamaUrl.replace(/^https?:\/\//, '') : '147.189.168.95:11434'}`)
            : (gpuConn?.detail || 'RTX 5070 12GB · Local Cache');
        const barColor = isCpu ? 'bg-sky-500' : 'bg-purple-500';
        return `
        <div class="glass-panel rounded-xl p-4 flex flex-col justify-between hover:border-purple-500/40 transition cursor-pointer group h-32" data-usage-card="${esc(u.id)}" data-usage-label="${esc(u.label)}" data-usage-percent="${u.percent}" data-usage-image="${esc(u.image || '')}" title="Click to edit or sync ${esc(u.label)}">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-3 min-w-0">
                    <div class="w-10 h-10 rounded-xl bg-[#1a1b23] border border-white/10 flex items-center justify-center shrink-0">
                        <i class="${icon}"></i>
                    </div>
                    <div class="min-w-0">
                        <div class="text-xs font-medium text-gray-200 truncate">${esc(u.label)}</div>
                        <div class="text-[10px] text-gray-400 font-mono truncate">${esc(sub)}</div>
                    </div>
                </div>
                <div class="text-2xl font-light text-white font-mono shrink-0" data-usage="${esc(u.id)}" data-current="0">0%</div>
            </div>
            <div class="w-full bg-white/5 h-1.5 rounded-full overflow-hidden mt-3">
                <div class="${barColor} h-full rounded-full transition-all duration-700" style="width: ${Math.max(4, Math.min(100, u.percent))}%"></div>
            </div>
        </div>`;
    }).join('');
    usage.forEach((u) => {
        const el = document.querySelector(`[data-usage="${u.id}"]`);
        animateValue(el, 0, u.percent, (v) => `${Math.round(v)}%`);
    });
    if (s.stats) {
        $('data-source').textContent = 'Live from SQLite + VPS Telemetry';
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
    const hostText = m.location === 'cloud'
        ? 'Via OpenAI-compatible API'
        : `On Node (${s.ollamaUrl ? s.ollamaUrl.replace(/^https?:\/\//, '') : '147.189.168.95:11434'})${s.failsafeEnabled ? ' + AirLLM Failsafe' : ''}`;
    $('panel-model-host').textContent = hostText;
    $('answer-model').textContent = m.name;
    document.querySelectorAll('[data-mode]').forEach((el) => el.classList.toggle('active', el.dataset.mode === m.location));

    const groups = [['local', 'Local / VPS Models'], ['cloud', 'Cloud Models']];
    $('model-list').innerHTML = groups.map(([key, title]) => {
        const items = s.models.filter((x) => x.location === key);
        if (!items.length) return '';
        return `<div class="p-2 text-[10px] font-bold text-gray-500 uppercase">${title}</div>` + items.map((x) => `
            <div class="hover:bg-white/10 cursor-pointer text-xs flex items-center gap-2 p-2 border-l-2 ${x.id === s.model ? 'border-white bg-white/5' : 'border-transparent'}"
                 data-model-id="${esc(x.id)}" title="${esc(x.id)}">
                <div class="w-2 h-2 rounded-full ${x.available ? 'bg-green-500' : 'bg-amber-500'}"></div>
                <span class="flex-1 truncate">${esc(x.name)}</span>
                <span class="text-[9px] text-gray-500">${esc(x.available ? x.size || 'ready' : x.status || '')}</span>
            </div>`).join('');
    }).join('');
}

function renderToolModeAndFailsafe(s) {
    const mode = s.toolMode || 'auto';
    if ($('tool-mode-badge')) $('tool-mode-badge').textContent = `mode: ${mode}`;
    const lockMap = {
        auto: { label: 'Auto (Full Admin)', icon: 'fas fa-unlock text-emerald-400', float: 'Auto' },
        confirm: { label: 'Confirm Writes', icon: 'fas fa-shield-alt text-amber-400', float: 'Confirm' },
        readonly: { label: 'Read-Only Locked', icon: 'fas fa-lock text-red-400', float: 'Locked' },
    };
    const info = lockMap[mode] || lockMap.auto;
    if ($('lock-mode-btn')) {
        $('lock-mode-btn').innerHTML = `<i class="${info.icon.split(' ').slice(0, 2).join(' ')}"></i> ${info.label}`;
    }
    if ($('floating-lock-label')) $('floating-lock-label').textContent = info.float;
    if ($('floating-lock-icon')) $('floating-lock-icon').className = info.icon;
    if ($('failsafe-pill')) {
        $('failsafe-pill').textContent = s.failsafeEnabled
            ? 'VPS Primary · Local AirLLM Failsafe Armed'
            : 'VPS Primary · Failsafe Disabled';
        $('failsafe-pill').className = `px-2 py-0.5 rounded text-[10px] font-mono border cursor-pointer ${
            s.failsafeEnabled
                ? 'bg-emerald-950/60 text-emerald-300 border-emerald-500/30'
                : 'bg-amber-950/60 text-amber-300 border-amber-500/30'
        }`;
    }
    const urlInput = $('details-ollama-url') || $('ollama-url-input');
    if (urlInput && s.ollamaUrl && !urlInput.matches(':focus')) {
        urlInput.value = s.ollamaUrl;
    }
    if ($('live-endpoint-pill') && s.ollamaUrl) {
        $('live-endpoint-pill').textContent = `VPS ${s.ollamaUrl.replace(/^https?:\/\//, '')}`;
    }
}

function renderConnectors(s) {
    const list = s.connectors || [];
    const onlineCount = list.filter((c) => c.online).length;
    if ($('connector-status')) {
        $('connector-status').innerHTML = onlineCount > 0
            ? `<div class="w-1.5 h-1.5 rounded-full bg-green-500"></div> <span class="text-green-400">${onlineCount}/${list.length || 1} Online</span>`
            : `<div class="w-1.5 h-1.5 rounded-full bg-emerald-500"></div> <span class="text-emerald-400">On</span>`;
    }

    const vpsConn = list.find((c) => c.id === 'ollama');
    const gpuConn = list.find((c) => c.id === 'main_pc_gpu');
    if (vpsConn) {
        const msMatch = (vpsConn.detail || '').match(/(\d+ms)/);
        if ($('live-latency-pill')) $('live-latency-pill').textContent = msMatch ? `ping: ${msMatch[1]}` : (vpsConn.online ? 'ping: online' : 'ping: standby');
        if ($('matrix-vps-status')) {
            $('matrix-vps-status').textContent = vpsConn.online ? 'ONLINE' : 'STANDBY';
            $('matrix-vps-status').className = `px-1.5 py-0.5 rounded text-[9px] ${vpsConn.online ? 'bg-emerald-900/50 text-emerald-300' : 'bg-amber-900/50 text-amber-300'}`;
        }
        if ($('matrix-vps-model')) $('matrix-vps-model').textContent = `Active: ${s.model} (${vpsConn.detail || 'CPU Auto'})`;
        if ($('matrix-vps-host')) $('matrix-vps-host').textContent = `Endpoint: ${s.ollamaUrl || 'http://147.189.168.95:11434'}`;
        if ($('welcome-vps-pill')) {
            $('welcome-vps-pill').textContent = vpsConn.online ? `VPS ONLINE · ${msMatch ? msMatch[1] : 'READY'}` : 'VPS STANDBY · GPU FAILSAFE';
        }
    }
    if (gpuConn) {
        if ($('matrix-gpu-status')) $('matrix-gpu-status').textContent = gpuConn.online ? 'RTX 5070 READY' : 'CPU MODE';
        if ($('matrix-gpu-spec')) $('matrix-gpu-spec').textContent = gpuConn.detail || '11.9GB VRAM · Local Cache Ready';
    }
    if (s.welcomeGreeting && $('jarvis-welcome-text')) {
        $('jarvis-welcome-text').textContent = s.welcomeGreeting;
    }

    if ($('connector-row') && list.length) {
        $('connector-row').innerHTML = list.map((c) => `
            <div class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-[#181920]/90 border ${c.online ? 'border-emerald-500/40 text-emerald-300' : 'border-white/10 text-gray-500'} cursor-pointer hover:border-purple-400 transition shrink-0" title="${esc(c.name)} (${esc(c.detail || '')})">
                <div class="w-1.5 h-1.5 rounded-full ${c.online ? 'bg-emerald-400' : 'bg-gray-600'}"></div>
                <i class="${esc(c.icon || 'fas fa-plug')} text-[11px]"></i>
                <div class="text-[10px] font-mono">
                    <span class="text-gray-200">${esc(c.name.split(' ')[0])}</span>
                    <span class="text-gray-400 ml-1">${esc((c.detail || '').split('·')[0].trim())}</span>
                </div>
            </div>`).join('');
    }
    if ($('details-connector-grid') && list.length) {
        $('details-connector-grid').innerHTML = list.map((c) => `
            <div class="p-2.5 rounded-lg bg-black/40 border ${c.online ? 'border-emerald-500/30' : 'border-white/10'} flex items-center justify-between gap-2">
                <div class="min-w-0">
                    <div class="text-white font-medium truncate"><i class="${esc(c.icon || 'fas fa-plug')} mr-1.5 text-purple-400"></i>${esc(c.name)}</div>
                    <div class="text-[10px] text-gray-400 font-mono truncate">${esc(c.detail || c.target || '')}</div>
                </div>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-mono shrink-0 ${c.online ? 'bg-emerald-900/50 text-emerald-300' : 'bg-gray-800 text-gray-400'}">${c.online ? 'ONLINE' : 'STANDBY'}</span>
            </div>`).join('');
    }
}

function renderActivityFeed(s) {
    const feed = $('vps-activity-feed');
    if (!feed) return;
    const recentSteps = (s.steps || []).slice(-3).reverse().map((st) => ({
        ts: 'live',
        actor: st.role || 'jarvis',
        tool: st.tool,
        result: st.result || st.status,
        ok: st.status !== 'error' && st.status !== 'denied',
    }));
    const recentAudits = (s.audit || []).slice(0, 4);
    const combined = [...recentSteps, ...recentAudits].slice(0, 4);
    if (!combined.length) {
        feed.innerHTML = `<div class="text-emerald-400/90">● Node ${s.ollamaUrl ? s.ollamaUrl.replace(/^https?:\/\//, '') : '147.189.168.95:11434'} (${esc(s.model)}) & Local RTX 5070 standing by — zero errors.</div>`;
        return;
    }
    feed.innerHTML = combined.map((a) => `
        <div class="flex items-center justify-between gap-2 truncate">
            <span class="truncate">
                <span class="${a.ok ? 'text-emerald-400' : 'text-red-400'}">●</span>
                <span class="text-gray-500">${esc(String(a.ts || '').slice(-8))}</span>
                <span class="text-purple-300 ml-1">${esc(a.actor || 'jarvis')}</span>
                <span class="text-white font-semibold ml-1">${esc(a.tool)}</span>
                <span class="text-gray-400 ml-1">${esc(String(a.result || '').slice(0, 70))}</span>
            </span>
            <span class="text-[9px] px-1.5 rounded ${a.ok ? 'bg-emerald-950 text-emerald-300' : 'bg-red-950 text-red-300'} shrink-0">${a.ok ? 'OK' : 'ERR'}</span>
        </div>`).join('');
}

function renderChatMessages(s) {
    const box = $('chat-view-log');
    if (!box) return;
    const msgs = s.chatMessages || [];
    if (!msgs.length) {
        box.innerHTML = '<div class="text-gray-500 text-xs">No conversation messages yet. Send a prompt below to talk to VPS Ollama (or automatic local AirLLM failsafe).</div>';
        return;
    }
    box.innerHTML = msgs.map((m) => {
        const isUser = m.role === 'user';
        return `
        <div class="p-3 rounded-xl border ${isUser ? 'bg-purple-950/20 border-purple-500/30 ml-6' : 'bg-black/40 border-white/10 mr-6'}">
            <div class="flex items-center justify-between text-[10px] text-gray-400 font-mono mb-1">
                <span class="${isUser ? 'text-purple-300' : 'text-emerald-300'} uppercase font-semibold">${esc(m.role)}</span>
                <span>${esc(m.created_at || '')}</span>
            </div>
            <div class="text-xs text-gray-200 whitespace-pre-wrap leading-relaxed">${esc(m.content)}</div>
        </div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
}

function renderWorkspaceFiles(s) {
    const box = $('workspace-file-list');
    if (!box) return;
    const files = s.workspaceFiles || [];
    if (!files.length) {
        box.innerHTML = '<div class="text-gray-500 p-2">Click List Files to inspect workspace.</div>';
        return;
    }
    box.innerHTML = files.map((f) => `
        <div data-file-path="${esc(f.path)}" data-file-dir="${f.is_dir ? '1' : '0'}" class="px-2 py-1 rounded hover:bg-white/10 cursor-pointer flex items-center justify-between gap-2">
            <span class="truncate ${f.is_dir ? 'text-sky-300' : 'text-gray-300'}">
                <i class="fas ${f.is_dir ? 'fa-folder text-sky-400' : 'fa-file-code text-gray-500'} mr-1.5"></i>${esc(f.name)}
            </span>
            <span class="text-[10px] text-gray-500 shrink-0">${f.is_dir ? 'DIR' : `${Math.ceil((f.size || 0) / 1024)}KB`}</span>
        </div>`).join('');
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
    renderToolModeAndFailsafe(s);
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
    if (!s.connectors?.length) {
        $('connector-status').innerHTML = s.connected
            ? '<div class="w-1.5 h-1.5 rounded-full bg-green-500"></div> <span class="text-green-400">On</span>'
            : '<div class="w-1.5 h-1.5 rounded-full bg-red-500"></div> <span class="text-red-400">Off</span>';
    }
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
    if (has('stats', 'connectors')) renderUsage(s);
    if (has('user')) renderUser(s);
    if (has('models', 'model', 'ollamaUrl', 'failsafeEnabled')) renderModels(s);
    if (has('toolMode', 'failsafeEnabled', 'ollamaUrl')) renderToolModeAndFailsafe(s);
    if (has('connectors', 'welcomeGreeting', 'model')) renderConnectors(s);
    if (has('audit', 'steps', 'model')) renderActivityFeed(s);
    if (has('chatMessages')) renderChatMessages(s);
    if (has('workspaceFiles')) renderWorkspaceFiles(s);
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
    if (!box) return;
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
    if (!box) return;
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
