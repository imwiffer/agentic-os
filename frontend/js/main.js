// Agentic OS bootstrap: wires DOM events, WebSocket loop, voice, auth, approval gates, console, files, design, web, and telemetry.
import { appState, subscribe, updateUI } from './store.js';
import { api, WS_URL } from './api.js';
import { JarvisSocket } from './socket.js';
import { currentModel, initRenderer, log, logInterim, clearInterim } from './ui.js';
import { executeAction } from './tools.js';
import { VoiceEngine, speaker } from './voice.js';

const $ = (id) => document.getElementById(id);

let voice = null;
let socket = null;
let pendingReply = '';
let audioChunks = [];
let audioMime = 'audio/mpeg';
let replyTimer = null;

const publicState = () => ({
    currentTab: appState.currentTab,
    businessTab: appState.businessTab,
    currency: appState.currency,
    model: appState.model,
    voiceActive: appState.voiceActive,
    role: appState.role,
});

// ---------------- Brain connection ----------------
function connectSocket() {
    if (socket) return;
    socket = new JarvisSocket(WS_URL, {
        onOpen() {
            updateUI({ connected: true, status: 'idle' });
            socket.send({ type: 'hello', state: publicState() });
            log('system', 'Neural link established (VPS Ollama + Local AirLLM Failsafe).');
        },
        onClose() {
            if (appState.connected) log('system', 'Connection lost — retrying…');
            updateUI({ connected: false });
        },
        onMessage: handleMessage,
        onBinary(data) { audioChunks.push(data); },
    });
}

async function handleMessage(msg) {
    switch (msg.type) {
        case 'ready':
            if (msg.tool_mode) updateUI({ toolMode: msg.tool_mode });
            break;
        case 'status':
            updateUI({ status: msg.status });
            break;
        case 'transcript':
            clearInterim();
            log('user', msg.text);
            break;
        case 'step': {
            const steps = [...appState.steps, msg].slice(-40);
            updateUI({ steps });
            log('action', `🔧 ${msg.role || 'jarvis'}.${msg.tool} [${msg.status}]`);
            break;
        }
        case 'approval_request':
            updateUI({ approvalRequest: msg });
            log('system', `⚠️ Approval needed: ${msg.tool}`);
            break;
        case 'inbox': {
            const inbox = [msg.item, ...appState.inbox.filter((x) => x.id !== msg.item.id)];
            updateUI({ inbox, unreadInbox: inbox.filter((x) => !x.read).length });
            log('system', `📬 ${msg.item.title}`);
            break;
        }
        case 'action':
            log('action', `⚡ ${msg.action}${msg.target ? ' → ' + msg.target : ''}`);
            await executeAction(msg);
            break;
        case 'reply':
            handleReply(msg);
            break;
        case 'audio_start':
            audioChunks = [];
            audioMime = msg.mime || 'audio/mpeg';
            break;
        case 'audio_end':
            clearTimeout(replyTimer);
            if (msg.error || !audioChunks.length) {
                speakReply(pendingReply);
            } else {
                speaker.playBlob(new Blob(audioChunks, { type: audioMime }), {
                    onstart: () => updateUI({ status: 'speaking' }),
                    onend: finishTurn,
                });
            }
            break;
        case 'error':
            log('system', msg.message);
            finishTurn();
            break;
    }
}

function handleReply(msg) {
    const meta = [msg.role, msg.engine, msg.latency_ms != null ? `${msg.latency_ms}ms` : ''].filter(Boolean).join(' · ');
    log('jarvis', msg.text, meta);
    if (msg.note) log('system', msg.note);
    const nowStr = new Date().toLocaleTimeString();
    updateUI({
        engine: msg.engine === 'llm' ? `llm (${msg.model})` : 'rules',
        tokens: msg.tokens_total ?? appState.tokens + (msg.tokens || 0),
        chatMessages: [...(appState.chatMessages || []), { role: 'assistant', content: msg.text, created_at: nowStr }].slice(-120),
    });
    if (msg.audio) {
        pendingReply = msg.text;
        replyTimer = setTimeout(() => speakReply(pendingReply), 8000);
    } else {
        speakReply(msg.text);
    }
}

function speakReply(text) {
    if (speaker.muted) return;
    speaker.speak(text, { onstart: () => updateUI({ status: 'speaking' }), onend: finishTurn });
}

function updateMuteUI() {
    const isMuted = speaker.muted;
    const muteBtn = $('mute-voice-btn');
    const muteIcon = $('mute-icon');
    const muteLabel = $('mute-label');
    if (muteBtn) {
        muteBtn.className = `flex items-center gap-1.5 glass-button px-2.5 py-1.5 rounded-full text-xs transition border ${
            isMuted ? 'border-red-500/50 text-red-400 bg-red-950/40 shadow-sm shadow-red-900/50' : 'border-emerald-500/30 text-emerald-400 hover:text-white'
        }`;
    }
    if (muteIcon) muteIcon.className = isMuted ? 'fas fa-volume-xmark' : 'fas fa-volume-high';
    if (muteLabel) muteLabel.textContent = isMuted ? 'Muted' : 'Mute';

    const hudIcon = $('hud-mute-icon');
    const hudBtn = $('hud-mute-btn');
    if (hudBtn) hudBtn.className = isMuted ? 'text-red-400 text-xs px-1.5' : 'text-emerald-400 hover:text-white text-xs px-1.5';
    if (hudIcon) hudIcon.className = isMuted ? 'fas fa-volume-xmark' : 'fas fa-volume-high';

    const studioBtn = $('studio-mute-btn');
    const studioIcon = $('studio-mute-icon');
    const studioLabel = $('studio-mute-label');
    if (studioBtn) {
        studioBtn.className = `glass-button px-2.5 py-1 rounded text-xs border flex items-center gap-1.5 transition ${
            isMuted ? 'border-red-500/50 text-red-400 bg-red-950/40' : 'border-emerald-500/30 text-emerald-400'
        }`;
    }
    if (studioIcon) studioIcon.className = isMuted ? 'fas fa-volume-xmark' : 'fas fa-volume-high';
    if (studioLabel) studioLabel.textContent = isMuted ? 'Assistant Sound: MUTED' : 'Assistant Sound: Active';
}

function toggleAssistantMute() {
    const muted = speaker.toggleMute();
    updateMuteUI();
    log('system', muted ? 'Assistant voice MUTED (instant silence).' : 'Assistant voice UNMUTED (active).');
}

function finishTurn() {
    updateUI({ status: appState.connected ? 'idle' : 'offline' });
    voice?.resume();
    refreshActiveTab();
}

// ---------------- Sending commands ----------------
async function sendCommand(text) {
    text = text.trim();
    if (!text) return;
    voice?.pause();
    clearInterim();
    log('user', text);
    const nowStr = new Date().toLocaleTimeString();
    updateUI({
        status: 'thinking',
        chatMessages: [...(appState.chatMessages || []), { role: 'user', content: text, created_at: nowStr }].slice(-120),
    });

    if (socket?.send({ type: 'user_text', text })) return;

    try {
        const res = await api.chat(text, publicState(), appState.role);
        for (const cmd of res.actions || []) {
            log('action', `⚡ ${cmd.action}${cmd.target ? ' → ' + cmd.target : ''}`);
            await executeAction(cmd);
        }
        handleReply({ ...res, text: res.response, audio: false });
    } catch (err) {
        log('system', `Brain error: ${err.message}`);
        updateUI({ status: 'offline' });
    }
}

function sendApproval(approved) {
    const req = appState.approvalRequest;
    if (!req) return;
    socket?.send({ type: 'approval', id: req.id, approved });
    updateUI({ approvalRequest: null });
}

// ---------------- Tab data loaders ----------------
async function refreshActiveTab(tab = appState.currentTab) {
    if (!appState.authenticated) return;
    try {
        if (tab === 'Dashboard') {
            const [stats, conn] = await Promise.all([api.stats(), api.connectors()]);
            updateUI({ stats, tokens: stats.tokens_processed, user: stats.user, connectors: conn.connectors });
        } else if (tab === 'Chat') {
            const [hist, filesRes] = await Promise.all([api.chatHistory(80), api.files(appState.consoleCwd || '')]);
            updateUI({
                chatMessages: hist.messages || [],
                workspaceFiles: filesRes.files || [],
                consoleCwd: filesRes.cwd || appState.consoleCwd,
            });
            if ($('console-cwd-label') && filesRes.cwd) $('console-cwd-label').textContent = filesRes.cwd;
        } else if (tab === 'Inbox') {
            const d = await api.inbox();
            updateUI({ inbox: d.items, unreadInbox: d.unread });
        } else if (tab === 'Calendar') {
            const d = await api.jobs();
            updateUI({ jobs: d.jobs });
        } else if (tab === 'Memory') {
            const q = $('memory-search')?.value || '';
            const d = await api.memories(q);
            updateUI({ memories: d.memories });
        } else if (tab === 'Agents') {
            const d = await api.roles();
            updateUI({ roles: d.roles });
        } else if (tab === 'Settings') {
            const [sys, aud, conn] = await Promise.all([api.system(), api.audit(80), api.connectors()]);
            updateUI({ system: sys, audit: aud.audit, connectors: conn.connectors });
        }
    } catch (err) {
        console.warn('Tab refresh failed:', err);
    }
}

// ---------------- Voice ----------------
async function toggleVoice(force) {
    const on = force ?? !appState.voiceActive;
    if (!on) {
        voice?.stop();
        voice = null;
        speechSynthesis?.cancel();
        updateUI({ voiceActive: false, listening: false, status: appState.connected ? 'idle' : 'offline' });
        return;
    }
    const mode = appState.sttEngine === 'browser' ? 'browser' : 'server';
    voice = new VoiceEngine({
        mode,
        onTranscript: (text) => sendCommand(text),
        onInterim: (text) => logInterim(text),
        onLevel: (on) => updateUI({ listening: on }),
        onUtteranceStart: () => socket?.send({ type: 'audio_start', sample_rate: 16000 }),
        onUtteranceEnd: () => socket?.send({ type: 'audio_end' }),
        sendAudio: (buf) => socket?.sendBinary(buf),
        onError: (message) => {
            log('system', message);
            if (/denied|not supported/i.test(message)) toggleVoice(false);
        },
    });
    try {
        if (mode === 'server' && !socket?.ready) throw new Error('Server STT needs the brain connection.');
        await voice.start();
        updateUI({ voiceActive: true });
        log('system', `Voice online (${mode === 'browser' ? 'Web Speech API' : 'server ' + appState.sttEngine}).`);
    } catch (err) {
        voice = null;
        log('system', err.message || String(err));
        updateUI({ voiceActive: false });
    }
}

// ---------------- Tool Mode / Failsafe / Connectors ----------------
async function cycleToolMode() {
    const order = ['auto', 'confirm', 'readonly'];
    const idx = order.indexOf(appState.toolMode || 'auto');
    const next = order[(idx + 1) % order.length];
    try {
        const res = await api.setToolMode({ tool_mode: next });
        updateUI({ toolMode: res.tool_mode });
        log('system', `Security / Tool mode switched to ${res.tool_mode.toUpperCase()}.`);
    } catch (err) {
        log('system', `Mode switch failed: ${err.message}`);
    }
}

async function toggleFailsafe() {
    const next = !appState.failsafeEnabled;
    try {
        const res = await api.setToolMode({ failsafe: next });
        const armed = res.failsafe ?? res.failsafe_enabled ?? next;
        updateUI({ failsafeEnabled: armed });
        log('system', `Local AirLLM failsafe ${armed ? 'ARMED' : 'DISABLED'}.`);
    } catch (err) {
        log('system', `Failsafe toggle failed: ${err.message}`);
    }
}

async function openFinanceModal() {
    const overview = appState.stats?.tabs?.Overview || [];
    const expenses = appState.stats?.tabs?.Expenses || [];
    const revenue = appState.stats?.tabs?.Revenue || [];
    const getVal = (arr, id, def = 0) => arr.find((c) => c.id === id)?.value ?? def;
    $('fin-cash').value = getVal(overview, 'cash_on_hand', 120.5);
    $('fin-income').value = getVal(overview, 'monthly_income', 142.33);
    $('fin-expenses').value = getVal(expenses, 'monthly_expenses', 3180);
    $('fin-revenue').value = getVal(revenue, 'revenue_mtd', 18420);
    $('fin-target').value = getVal(overview, 'revenue_target', 50000);
    $('edit-finance-modal').classList.remove('hidden');
}

// ---------------- DOM wiring ----------------
function selectMode(mode) {
    const models = appState.models.filter((m) => m.location === mode);
    if (!models.length || currentModel().location === mode) return;
    updateUI({ model: (models.find((m) => m.available) || models[0]).id });
}

function appendConsoleLine(text, cls = 'text-gray-200') {
    const out = $('console-output');
    if (!out) return;
    const div = document.createElement('div');
    div.className = `whitespace-pre-wrap ${cls}`;
    div.textContent = text;
    out.appendChild(div);
    out.scrollTop = out.scrollHeight;
}

function bindDOM() {
    updateMuteUI();
    // Window controls (native pywebview or browser fullscreen)
    $('win-close')?.addEventListener('click', () => {
        if (window.pywebview?.api?.close) window.pywebview.api.close();
        else window.close();
    });
    $('win-min')?.addEventListener('click', () => {
        if (window.pywebview?.api?.minimize) window.pywebview.api.minimize();
    });
    $('win-max')?.addEventListener('click', () => {
        if (window.pywebview?.api?.toggle_fullscreen) window.pywebview.api.toggle_fullscreen();
        else if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
        else document.exitFullscreen?.();
    });

    document.querySelectorAll('[data-tab]').forEach((el) => el.addEventListener('click', (e) => {
        e.preventDefault();
        updateUI({ currentTab: el.dataset.tab });
    }));
    document.querySelectorAll('[data-btab]').forEach((el) => el.addEventListener('click', () => {
        updateUI({ currentTab: 'Dashboard', businessTab: el.dataset.btab });
    }));
    document.querySelectorAll('[data-mode]').forEach((el) => el.addEventListener('click', () => selectMode(el.dataset.mode)));

    $('voice-btn').addEventListener('click', () => toggleVoice());
    $('mute-voice-btn')?.addEventListener('click', toggleAssistantMute);
    $('hud-mute-btn')?.addEventListener('click', toggleAssistantMute);
    $('studio-mute-btn')?.addEventListener('click', toggleAssistantMute);
    $('chat-btn').addEventListener('click', () => { $('hud').classList.remove('collapsed'); $('hud-input').focus(); });
    $('hud-toggle').addEventListener('click', () => $('hud').classList.toggle('collapsed'));
    $('currency-select').addEventListener('change', (e) => updateUI({ currency: e.target.value }));

    // Profile edit on click
    $('profile-box')?.addEventListener('click', async () => {
        const name = prompt('Operator Display Name:', appState.user?.name || 'Admin');
        if (!name) return;
        const title = prompt('Operator Role / Title:', appState.user?.title || 'System Operator') || 'System Operator';
        const res = await api.saveUser(name.trim(), title.trim());
        const user = res.user || res;
        updateUI({ user });
        log('system', `Operator profile updated: ${user.name} (${user.title}).`);
    });

    // Model selector & Add/Pull Model modal
    $('model-button').addEventListener('click', (e) => { e.stopPropagation(); $('modelSelector').classList.toggle('hidden'); });
    $('model-list').addEventListener('click', (e) => {
        const item = e.target.closest('[data-model-id]');
        if (!item) return;
        updateUI({ model: item.dataset.modelId });
        $('modelSelector').classList.add('hidden');
        log('system', `Active model set to ${item.dataset.modelId}`);
    });
    document.addEventListener('click', (e) => {
        if ($('modelSelector') && !$('modelSelector').contains(e.target)) $('modelSelector').classList.add('hidden');
    });

    const openAddModel = () => $('add-model-modal')?.classList.remove('hidden');
    $('add-model-btn')?.addEventListener('click', openAddModel);
    $('add-model-small-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        $('modelSelector').classList.add('hidden');
        openAddModel();
    });
    $('add-model-cancel')?.addEventListener('click', () => $('add-model-modal').classList.add('hidden'));
    $('add-model-close')?.addEventListener('click', () => $('add-model-modal').classList.add('hidden'));
    $('add-model-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = $('new-model-id').value.trim();
        const name = $('new-model-name').value.trim() || id;
        const location = ($('new-model-location') || $('new-model-loc'))?.value || 'local';
        const pull = $('new-model-pull').checked;
        if (!id) return;
        $('add-model-modal').classList.add('hidden');
        log('system', `Registering model ${id}${pull ? ' and triggering VPS Ollama pull' : ''}…`);
        try {
            const res = await api.addModel({ id, name, location, pull });
            if (res.pull_status) log('system', `VPS Ollama pull: ${res.pull_status}`);
            const models = await api.models();
            updateUI({ models: models.models, model: id });
            $('new-model-id').value = '';
            $('new-model-name').value = '';
        } catch (err) {
            log('system', `Add model error: ${err.message}`);
        }
    });

    // Engine Details drawer & Connectors
    const toggleDetailsDrawer = async (forceOpen) => {
        const drawer = $('engine-details-drawer');
        if (!drawer) return;
        const show = forceOpen ?? drawer.classList.contains('hidden');
        drawer.classList.toggle('hidden', !show);
        if (show) {
            try {
                const conn = await api.connectors();
                updateUI({ connectors: conn.connectors, ollamaUrl: conn.ollama_url, failsafeEnabled: conn.failsafe_enabled });
            } catch (err) {
                log('system', `Connector probe error: ${err.message}`);
            }
        }
    };
    $('details-btn')?.addEventListener('click', () => toggleDetailsDrawer());
    $('close-details-btn')?.addEventListener('click', () => toggleDetailsDrawer(false));
    $('connector-row')?.addEventListener('click', () => toggleDetailsDrawer(true));
    $('connector-status')?.addEventListener('click', () => toggleDetailsDrawer());
    $('save-ollama-url-btn')?.addEventListener('click', async () => {
        const urlInput = $('details-ollama-url') || $('ollama-url-input');
        const url = urlInput?.value.trim();
        if (!url) return;
        const res = await api.setToolMode({ ollama_url: url });
        const [models, conn] = await Promise.all([api.models(), api.connectors()]);
        updateUI({
            ollamaUrl: res.ollama_url,
            models: models.models,
            ollamaOnline: models.ollama_online,
            connectors: conn.connectors,
        });
        log('system', `Primary Ollama endpoint updated to ${res.ollama_url}`);
    });

    // Quick tool commands in AI Models sidebar
    $('tool-list')?.addEventListener('click', (e) => {
        const row = e.target.closest('[data-quick-cmd]');
        if (row) sendCommand(row.dataset.quickCmd);
    });

    // Jarvis Welcome Banner controls
    $('welcome-speak-btn')?.addEventListener('click', () => {
        const text = appState.welcomeGreeting || $('jarvis-welcome-text')?.textContent || 'All systems standing by.';
        speakReply(text);
    });
    $('voice-persona-select')?.addEventListener('change', async (e) => {
        const vid = e.target.value;
        try {
            await api.setVoicePersona(vid);
            const label = e.target.options[e.target.selectedIndex].text;
            log('system', `Voice persona switched to ${label}.`);
        } catch (err) {
            log('system', `Voice persona update failed: ${err.message}`);
        }
    });
    $('welcome-detect-btn')?.addEventListener('click', async () => {
        log('system', 'Probing local RTX 5070 GPU & remote Ubuntu server specs…');
        try {
            const [hwRes, welcomeRes, modelsRes] = await Promise.all([
                api.detectHardware(),
                api.welcome(),
                api.models(),
            ]);
            updateUI({
                hardware: hwRes,
                model: hwRes.selected_default_model || modelsRes.default,
                models: modelsRes.models,
                connectors: welcomeRes.connectors || appState.connectors,
                welcomeGreeting: welcomeRes.greeting,
                audit: welcomeRes.recent_audit || appState.audit,
            });
            log('jarvis', welcomeRes.greeting, `auto-detect · ${hwRes.selected_default_model}`);
            speakReply(welcomeRes.greeting);
        } catch (err) {
            log('system', `Spec detect error: ${err.message}`);
        }
    });

    // Lock / Tool Mode & Failsafe buttons
    $('lock-mode-btn')?.addEventListener('click', cycleToolMode);
    $('floating-lock-btn')?.addEventListener('click', cycleToolMode);
    $('tool-mode-badge')?.addEventListener('click', cycleToolMode);
    $('failsafe-pill')?.addEventListener('click', toggleFailsafe);

    // Sync Live Telemetry & Edit Finance / Metrics
    $('sync-live-btn')?.addEventListener('click', async () => {
        log('system', 'Syncing live CPU, RAM, token, and database telemetry into dashboard metrics…');
        const res = await api.syncLive();
        const stats = res.stats || res;
        updateUI({ stats, tokens: stats.tokens_processed });
        log('system', 'Live telemetry synced.');
    });
    $('edit-finance-btn')?.addEventListener('click', openFinanceModal);
    $('edit-finance-cancel')?.addEventListener('click', () => $('edit-finance-modal').classList.add('hidden'));
    $('edit-finance-close')?.addEventListener('click', () => $('edit-finance-modal').classList.add('hidden'));
    $('edit-finance-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            cash_on_hand: parseFloat($('fin-cash').value) || 0,
            monthly_income: parseFloat($('fin-income').value) || 0,
            monthly_expenses: parseFloat($('fin-expenses')?.value) || 0,
            revenue: parseFloat($('fin-revenue')?.value) || 0,
            revenue_target: parseFloat($('fin-target').value) || 0,
        };
        $('edit-finance-modal').classList.add('hidden');
        const res = await api.saveFinance(payload);
        updateUI({ stats: res.stats || res });
        log('system', 'Financial overview updated in SQLite.');
    });

    $('stat-cards')?.addEventListener('click', async (e) => {
        const card = e.target.closest('[data-card]');
        if (!card) return;
        const id = card.dataset.card;
        if (['cash_on_hand', 'monthly_income', 'revenue_target', 'monthly_expenses', 'revenue'].includes(id)) {
            openFinanceModal();
            return;
        }
        const label = card.dataset.label || id;
        const current = card.dataset.rawValue || '0';
        const nextStr = prompt(`Update ${label} (${appState.businessTab}):`, current);
        if (nextStr === null) return;
        const val = parseFloat(nextStr);
        if (Number.isNaN(val)) return;
        const res = await api.saveMetric({
            tab: appState.businessTab,
            key: id,
            label,
            value: val,
            format: card.dataset.format || 'number',
        });
        updateUI({ stats: res.stats || res });
        log('system', `Updated ${label} to ${val}.`);
    });

    $('usage-cards')?.addEventListener('click', async (e) => {
        const card = e.target.closest('[data-usage-card]');
        if (!card) return;
        const id = card.dataset.usageCard;
        const label = card.dataset.usageLabel || id;
        const current = card.dataset.usagePercent || '50';
        const nextStr = prompt(`Update ${label} percentage (0-100):`, current);
        if (nextStr === null) return;
        const pct = Math.max(0, Math.min(100, parseFloat(nextStr) || 0));
        const res = await api.saveUsage({ id, label, percent: pct, image: card.dataset.usageImage || '' });
        updateUI({ stats: res.stats || res });
    });

    // Monaco IDE handlers
    $('ide-reload-frame')?.addEventListener('click', () => {
        const frame = $('ide-frame');
        if (frame) frame.src = frame.src;
    });
    $('ide-start-bridge')?.addEventListener('click', async () => {
        log('system', 'Starting Vanta Monaco IDE & GPU Bridge (:7777)…');
        await api.consoleExec('python desktop_app.py --bridge-only', '');
        setTimeout(() => {
            const frame = $('ide-frame');
            if (frame) frame.src = 'http://127.0.0.1:7777';
            $('ide-fallback-card')?.classList.add('hidden');
        }, 1500);
    });

    // Extensions & Token Optimizer handlers
    async function loadTokenStats() {
        try {
            const res = await api.tokenStats();
            if (res.ok && res.stats) {
                const st = res.stats;
                if ($('stat-tokens-saved')) $('stat-tokens-saved').textContent = st.tokens_saved.toLocaleString();
                if ($('stat-zero-hits')) $('stat-zero-hits').textContent = st.zero_token_hits.toLocaleString();
                if ($('stat-cache-hits')) $('stat-cache-hits').textContent = st.cache_hits.toLocaleString();
                if ($('stat-tools-pruned')) $('stat-tools-pruned').textContent = st.tools_pruned.toLocaleString();
                if ($('token-saved-badge')) $('token-saved-badge').innerHTML = `<i class="fas fa-bolt mr-1"></i> ${st.tokens_saved.toLocaleString()} tokens saved`;
            }
        } catch {}
    }

    async function searchExtensionsUI() {
        const query = $('ext-search-input')?.value.trim() || 'theme';
        const grid = $('ext-results-grid');
        if (!grid) return;
        grid.innerHTML = '<div class="text-xs text-purple-300 py-4 col-span-3 text-center"><i class="fas fa-spinner fa-spin mr-1"></i> Searching OpenVSX...</div>';
        try {
            const res = await api.searchExtensions(query);
            const items = res.extensions || [];
            if (!items.length) {
                grid.innerHTML = '<div class="text-xs text-gray-500 py-4 col-span-3 text-center">No extensions found.</div>';
                return;
            }
            grid.innerHTML = items.map(ext => `
                <div class="p-3 rounded-lg bg-black/40 border border-white/10 hover:border-purple-500/40 transition flex flex-col justify-between h-28">
                    <div class="min-w-0">
                        <div class="flex items-center justify-between gap-1 mb-1">
                            <span class="text-xs font-semibold text-white truncate">${esc(ext.display_name || ext.name)}</span>
                            <span class="text-[9px] text-gray-500 font-mono">v${esc(ext.version)}</span>
                        </div>
                        <p class="text-[10px] text-gray-400 line-clamp-2">${esc(ext.description || 'OpenVSX extension package.')}</p>
                    </div>
                    <div class="flex items-center justify-between pt-1">
                        <span class="text-[9px] text-purple-400 font-mono">${esc(ext.namespace)}</span>
                        <button class="ext-install-btn px-2.5 py-1 rounded text-[10px] font-medium bg-purple-600 hover:bg-purple-500 text-white transition"
                                data-ns="${esc(ext.namespace)}" data-name="${esc(ext.name)}" data-ver="${esc(ext.version)}">
                            <i class="fas fa-download mr-1"></i> Install
                        </button>
                    </div>
                </div>
            `).join('');

            grid.querySelectorAll('.ext-install-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const ns = btn.dataset.ns;
                    const name = btn.dataset.name;
                    const ver = btn.dataset.ver;
                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
                    try {
                        const r = await api.installExtension(ns, name, ver);
                        btn.className = 'px-2.5 py-1 rounded text-[10px] font-medium bg-emerald-600 text-white';
                        btn.innerHTML = '<i class="fas fa-check mr-1"></i> Installed';
                        log('system', `Extension ${name} installed (${r.type || 'vsix'}).`);
                    } catch (err) {
                        btn.disabled = false;
                        btn.innerHTML = 'Retry';
                        log('system', `Install failed: ${err.message}`);
                    }
                });
            });
        } catch (err) {
            grid.innerHTML = `<div class="text-xs text-red-400 py-4 col-span-3 text-center">OpenVSX search error: ${esc(err.message)}</div>`;
        }
    }

    $('ext-search-btn')?.addEventListener('click', searchExtensionsUI);
    $('ext-search-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            searchExtensionsUI();
        }
    });

    setInterval(loadTokenStats, 8000);
    loadTokenStats();

    // Voice Studio Tab handlers
    function syncVoicePersonaUI(vid, name) {
        if ($('voice-persona-disp')) $('voice-persona-disp').textContent = name || (vid ? `Voice (${vid.slice(0, 8)}…)` : 'Fish Audio Natural');
        if ($('voice-persona-id-disp')) $('voice-persona-id-disp').textContent = vid || 'base-model (default)';
        if ($('voice-persona-select')) $('voice-persona-select').value = vid;
        if ($('custom-voice-id')) $('custom-voice-id').value = vid;

        document.querySelectorAll('.persona-card').forEach(card => {
            const cardId = card.dataset.voiceId ?? '';
            const isActive = cardId === vid;
            card.classList.toggle('border-purple-500/60', isActive);
            card.classList.toggle('border-white/10', !isActive);
            const pill = card.querySelector('.persona-active-pill');
            if (pill) {
                pill.textContent = isActive ? 'ACTIVE' : 'SELECTABLE';
                pill.className = `px-2 py-0.5 rounded text-[9px] font-mono ${isActive ? 'bg-purple-900/50 text-purple-300' : 'bg-gray-800 text-gray-400'}`;
            }
            const actBtn = card.querySelector('.btn-activate');
            if (actBtn) {
                actBtn.className = `btn-activate px-2.5 py-1 rounded text-[10px] font-semibold ${isActive ? 'bg-pink-600 text-white' : 'bg-gray-700 hover:bg-pink-600 text-white'}`;
                actBtn.textContent = isActive ? 'Active' : 'Set Active';
            }
        });
    }

    $('voice-cards-grid')?.addEventListener('click', async (e) => {
        const audBtn = e.target.closest('.btn-audition');
        if (audBtn) {
            const vid = audBtn.dataset.id ?? '';
            const phrase = $('audition-phrase')?.value.trim() || 'Systems online, dj.';
            audBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            try {
                const res = await fetch('/api/fish_audio/speak', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: phrase, voice_id: vid }),
                });
                if (res.ok) {
                    const blob = await res.blob();
                    speaker.playBlob(blob, {
                        onend: () => { audBtn.innerHTML = '<i class="fas fa-play mr-1"></i> Audition'; }
                    });
                } else {
                    audBtn.innerHTML = '<i class="fas fa-play mr-1"></i> Audition';
                }
            } catch {
                audBtn.innerHTML = '<i class="fas fa-play mr-1"></i> Audition';
            }
            return;
        }

        const actBtn = e.target.closest('.btn-activate');
        if (actBtn) {
            const vid = actBtn.dataset.id ?? '';
            const name = actBtn.dataset.name || 'Custom Persona';
            await api.setVoicePersona(vid);
            syncVoicePersonaUI(vid, name);
            log('system', `Voice persona set to ${name}.`);
        }
    });

    $('apply-custom-voice-btn')?.addEventListener('click', async () => {
        const vid = $('custom-voice-id')?.value.trim() || '';
        await api.setVoicePersona(vid);
        syncVoicePersonaUI(vid, `Custom (${vid.slice(0, 8)}…)`);
        log('system', `Custom voice persona ID activated: ${vid || 'default'}.`);
    });

    $('play-audition-btn')?.addEventListener('click', async () => {
        const phrase = $('audition-phrase')?.value.trim() || 'Systems online, dj. What are we building today?';
        const vid = $('custom-voice-id')?.value.trim() || '';
        const btn = $('play-audition-btn');
        if (btn) btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i> Speaking…';
        try {
            const res = await fetch('/api/fish_audio/speak', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: phrase, voice_id: vid }),
            });
            if (res.ok) {
                const blob = await res.blob();
                speaker.playBlob(blob, {
                    onend: () => { if (btn) btn.innerHTML = '<i class="fas fa-volume-high mr-1.5"></i> Speak Test'; }
                });
            } else {
                if (btn) btn.innerHTML = '<i class="fas fa-volume-high mr-1.5"></i> Speak Test';
            }
        } catch {
            if (btn) btn.innerHTML = '<i class="fas fa-volume-high mr-1.5"></i> Speak Test';
        }
    });

    // HUD form
    $('hud-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const input = $('hud-input');
        sendCommand(input.value);
        input.value = '';
    });

    // Chat & Console View
    $('chat-view-form')?.addEventListener('submit', (e) => {
        e.preventDefault();
        const input = $('chat-view-input');
        if (!input.value.trim()) return;
        sendCommand(input.value);
        input.value = '';
    });
    $('chat-clear-btn')?.addEventListener('click', async () => {
        await api.clearChatHistory();
        updateUI({ chatMessages: [] });
        log('system', 'Conversation history cleared.');
    });

    $('console-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const input = $('console-input');
        const cmd = input.value.trim();
        if (!cmd) return;
        input.value = '';
        appendConsoleLine(`PS ${appState.consoleCwd || ''}> ${cmd}`, 'text-purple-300 font-semibold');
        try {
            const res = await api.consoleExec(cmd, appState.consoleCwd);
            if (res.cwd) {
                updateUI({ consoleCwd: res.cwd });
                if ($('console-cwd-label')) $('console-cwd-label').textContent = res.cwd;
            }
            if (res.output) appendConsoleLine(res.output, res.ok ? 'text-emerald-300' : 'text-red-400');
        } catch (err) {
            appendConsoleLine(`Error: ${err.message}`, 'text-red-400');
        }
    });

    $('refresh-files-btn')?.addEventListener('click', async () => {
        const res = await api.files(appState.consoleCwd || '');
        updateUI({ workspaceFiles: res.files || [], consoleCwd: res.cwd || appState.consoleCwd });
    });
    $('workspace-file-list')?.addEventListener('click', async (e) => {
        const row = e.target.closest('[data-file-path]');
        if (!row) return;
        const p = row.dataset.filePath;
        const isDir = row.dataset.fileDir === '1';
        if (isDir) {
            const res = await api.files(p);
            updateUI({ workspaceFiles: res.files || [], consoleCwd: res.cwd || p });
            if ($('console-cwd-label')) $('console-cwd-label').textContent = res.cwd || p;
        } else {
            try {
                const res = await api.readFile(p);
                const pathEl = $('workspace-file-path') || $('file-editor-path');
                const contentEl = $('workspace-file-content') || $('file-editor-content');
                if (pathEl) pathEl.value = res.path;
                if (contentEl) contentEl.value = res.content;
            } catch (err) {
                log('system', `Read file error: ${err.message}`);
            }
        }
    });
    $('save-file-btn')?.addEventListener('click', async () => {
        const pathEl = $('workspace-file-path') || $('file-editor-path');
        const contentEl = $('workspace-file-content') || $('file-editor-content');
        const p = pathEl?.value.trim();
        if (!p) return;
        try {
            const res = await api.writeFile(p, contentEl?.value || '');
            log('system', `Saved ${res.path} (${res.bytes} bytes).`);
            const filesRes = await api.files(appState.consoleCwd || '');
            updateUI({ workspaceFiles: filesRes.files || [] });
        } catch (err) {
            log('system', `Write file error: ${err.message}`);
        }
    });

    // Design Studio View
    document.querySelectorAll('[data-theme-color]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const hex = btn.dataset.themeColor;
            document.documentElement.style.setProperty('--jarvis-accent', hex);
            log('system', `Applied theme accent ${hex}`);
        });
    });
    $('design-blur-range')?.addEventListener('input', (e) => {
        const px = e.target.value;
        if ($('design-blur-val')) $('design-blur-val').textContent = `${px}px`;
        document.querySelectorAll('.glass-panel').forEach((el) => {
            el.style.backdropFilter = `blur(${px}px)`;
        });
    });
    $('design-reset-btn')?.addEventListener('click', () => {
        document.documentElement.style.removeProperty('--jarvis-accent');
        if ($('design-blur-range')) $('design-blur-range').value = '14';
        if ($('design-blur-val')) $('design-blur-val').textContent = '14px';
        document.querySelectorAll('.glass-panel').forEach((el) => {
            el.style.backdropFilter = '';
        });
        log('system', 'Reset theme to default.');
    });
    $('design-toggle-hud')?.addEventListener('click', () => $('hud').classList.toggle('collapsed'));
    $('design-test-tts')?.addEventListener('click', () => speakReply('Agentic OS neural voice link operational.'));
    $('design-gen-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const promptText = $('design-gen-prompt').value.trim();
        if (!promptText) return;
        $('design-gen-preview').innerHTML = '<div class="text-purple-300 text-xs animate-pulse">Generating UI component via VPS Ollama…</div>';
        try {
            const fullPrompt = `Return ONLY a clean HTML snippet using Tailwind CSS classes (dark theme, no markdown fences, no <html> wrapper) for: ${promptText}`;
            const res = await api.chat(fullPrompt, publicState(), 'coder');
            const html = (res.response || '').replace(/^```html\s*/i, '').replace(/```\s*$/i, '').trim();
            $('design-gen-preview').innerHTML = html;
            $('design-gen-code').value = html;
        } catch (err) {
            $('design-gen-preview').innerHTML = `<div class="text-red-400 text-xs">${err.message}</div>`;
        }
    });
    $('design-gen-code')?.addEventListener('input', (e) => {
        $('design-gen-preview').innerHTML = e.target.value;
    });

    // Web Intelligence View
    $('web-search-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const q = $('web-search-input').value.trim();
        if (!q) return;
        const box = $('web-search-results');
        box.textContent = 'Searching live web…';
        try {
            const res = await api.webSearch(q);
            box.textContent = res.result || JSON.stringify(res, null, 2);
        } catch (err) {
            box.textContent = `Search error: ${err.message}`;
        }
    });

    $('web-fetch-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const url = $('web-fetch-input').value.trim();
        if (!url) return;
        const outEl = $('web-fetch-results') || $('web-fetch-output');
        outEl.textContent = `Fetching ${url}…`;
        try {
            const res = await api.webFetch(url);
            outEl.textContent = res.content || res.text || '(empty response)';
        } catch (err) {
            outEl.textContent = `Fetch error: ${err.message}`;
        }
    });
    $('web-save-memory-btn')?.addEventListener('click', async () => {
        const outEl = $('web-fetch-results') || $('web-fetch-output');
        const text = outEl.textContent.trim();
        const url = $('web-fetch-input').value.trim() || 'web';
        if (!text || text.startsWith('Enter any')) return;
        await api.addMemory(`${url}: ${text.slice(0, 600)}`, 'web-intel');
        log('system', `Saved web snippet from ${url} to long-term memory.`);
    });

    // Inbox actions
    $('inbox-read-all').addEventListener('click', async () => {
        await api.markInboxRead();
        refreshActiveTab('Inbox');
    });

    // Scheduler form & actions
    $('job-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const kind = $('job-kind').value;
        const when = $('job-when').value.trim();
        const payload = {
            name: $('job-name').value.trim(),
            prompt: $('job-prompt').value.trim(),
            role: $('job-role').value,
            ...(kind === 'daily' ? { daily_at: when } : { every_minutes: parseFloat(when) }),
        };
        try {
            await api.addJob(payload);
            $('job-name').value = '';
            $('job-prompt').value = '';
            refreshActiveTab('Calendar');
        } catch (err) {
            log('system', `Schedule error: ${err.message}`);
        }
    });
    $('job-list').addEventListener('click', async (e) => {
        const runBtn = e.target.closest('[data-run-job]');
        const delBtn = e.target.closest('[data-del-job]');
        if (runBtn) {
            await api.runJob(runBtn.dataset.runJob);
            refreshActiveTab('Calendar');
        } else if (delBtn) {
            await api.deleteJob(delBtn.dataset.delJob);
            refreshActiveTab('Calendar');
        }
    });

    // Memory form, search & delete
    $('memory-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        await api.addMemory($('memory-text').value.trim(), $('memory-tags').value.trim());
        $('memory-text').value = '';
        $('memory-tags').value = '';
        refreshActiveTab('Memory');
    });
    $('memory-search').addEventListener('input', () => refreshActiveTab('Memory'));
    $('memory-list').addEventListener('click', async (e) => {
        const del = e.target.closest('[data-del-memory]');
        if (del) {
            await api.deleteMemory(del.dataset.delMemory);
            refreshActiveTab('Memory');
        }
    });

    // Agent role selection
    $('role-cards').addEventListener('click', (e) => {
        const card = e.target.closest('[data-select-role]');
        if (card) {
            updateUI({ role: card.dataset.selectRole });
            log('system', `Switched active agent to ${card.dataset.selectRole}.`);
        }
    });

    // Settings refresh
    $('refresh-settings').addEventListener('click', () => refreshActiveTab('Settings'));

    // Approval modal
    $('approval-allow').addEventListener('click', () => sendApproval(true));
    $('approval-deny').addEventListener('click', () => sendApproval(false));

    // Login modal
    $('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        $('login-error').classList.add('hidden');
        try {
            await api.login($('login-token').value);
            $('login-token').value = '';
            updateUI({ authenticated: true });
            await loadDashboard();
        } catch (err) {
            $('login-error').textContent = err.message;
            $('login-error').classList.remove('hidden');
        }
    });
    window.addEventListener('jarvis:unauthorized', () => updateUI({ authRequired: true, authenticated: false }));

    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.code === 'Space') { e.preventDefault(); toggleVoice(); }
    });

    subscribe((changed) => {
        if ('currentTab' in changed) refreshActiveTab(changed.currentTab);
        if (['currentTab', 'businessTab', 'currency', 'model', 'voiceActive', 'role'].some((k) => k in changed)) {
            socket?.send({ type: 'state', state: publicState() });
        }
    });
}

// ---------------- Boot ----------------
let telemetryTimer = null;

async function pollLiveTelemetry() {
    if (!appState.authenticated) return;
    try {
        const [connRes, auditRes] = await Promise.all([api.connectors(), api.audit(6)]);
        updateUI({
            connectors: connRes.connectors || [],
            ollamaUrl: connRes.ollama_url || appState.ollamaUrl,
            failsafeEnabled: connRes.failsafe ?? appState.failsafeEnabled,
            hardware: connRes.hardware || appState.hardware,
            audit: auditRes.audit || appState.audit,
        });
    } catch {}
}

async function loadDashboard() {
    connectSocket();
    const [config, stats, models, rolesRes, inboxRes, welcomeRes] = await Promise.all([
        api.config(), api.stats(), api.models(), api.roles(), api.inbox(), api.welcome(),
    ]);
    updateUI({
        sttEngine: config.stt,
        ttsEngine: config.tts,
        toolMode: config.tool_mode || 'auto',
        currencies: config.currencies,
        user: stats.user,
        rates: stats.rates,
        models: models.models,
        model: welcomeRes.model || models.default,
        ollamaOnline: welcomeRes.vps_online ?? models.ollama_online,
        ollamaUrl: models.ollama_url || 'http://147.189.168.95:11434',
        failsafeEnabled: models.failsafe_enabled ?? true,
        connectors: welcomeRes.connectors || [],
        hardware: welcomeRes.hardware || null,
        system: welcomeRes.system || null,
        audit: welcomeRes.recent_audit || [],
        welcomeGreeting: welcomeRes.greeting || '',
        roles: rolesRes.roles,
        inbox: inboxRes.items,
        unreadInbox: inboxRes.unread,
    });
    if (welcomeRes.greeting) {
        log('jarvis', welcomeRes.greeting, `welcome · ${welcomeRes.model}`);
        setTimeout(() => speakReply(welcomeRes.greeting), 450);
    }
    try {
        const vp = await api.voicePersonas();
        syncVoicePersonaUI(vp.active_voice_id || '');
    } catch {}
    setTimeout(() => updateUI({ stats, tokens: stats.tokens_processed }), 300);
    clearInterval(telemetryTimer);
    telemetryTimer = setInterval(pollLiveTelemetry, 8000);
}

async function boot() {
    initRenderer();
    bindDOM();
    log('system', 'Booting Agentic OS…');
    try {
        const st = await api.authStatus();
        updateUI({ authRequired: st.auth_required, authenticated: st.authenticated, authUser: st.user });
        if (!st.authenticated) return;
        await loadDashboard();
    } catch (err) {
        console.error(err);
        updateUI({ status: 'offline' });
        log('system', 'Backend unreachable.');
    }
}

boot();
