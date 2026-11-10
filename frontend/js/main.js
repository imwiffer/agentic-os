// Agentic OS bootstrap: wires DOM events, the WebSocket loop, voice, auth, approval gates and the tools registry.
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
            log('system', 'Neural link established.');
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
    updateUI({
        engine: msg.engine === 'llm' ? `llm (${msg.model})` : 'rules',
        tokens: msg.tokens_total ?? appState.tokens + (msg.tokens || 0),
    });
    if (msg.audio) {
        pendingReply = msg.text;
        replyTimer = setTimeout(() => speakReply(pendingReply), 8000);
    } else {
        speakReply(msg.text);
    }
}

function speakReply(text) {
    speaker.speak(text, { onstart: () => updateUI({ status: 'speaking' }), onend: finishTurn });
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
    updateUI({ status: 'thinking' });

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
        if (tab === 'Inbox') {
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
            const [sys, aud] = await Promise.all([api.system(), api.audit(80)]);
            updateUI({ system: sys, audit: aud.audit });
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

// ---------------- DOM wiring ----------------
function selectMode(mode) {
    const models = appState.models.filter((m) => m.location === mode);
    if (!models.length || currentModel().location === mode) return;
    updateUI({ model: (models.find((m) => m.available) || models[0]).id });
}

function bindDOM() {
    document.querySelectorAll('[data-tab]').forEach((el) => el.addEventListener('click', (e) => {
        e.preventDefault();
        updateUI({ currentTab: el.dataset.tab });
    }));
    document.querySelectorAll('[data-btab]').forEach((el) => el.addEventListener('click', () => {
        updateUI({ currentTab: 'Dashboard', businessTab: el.dataset.btab });
    }));
    document.querySelectorAll('[data-mode]').forEach((el) => el.addEventListener('click', () => selectMode(el.dataset.mode)));

    $('voice-btn').addEventListener('click', () => toggleVoice());
    $('chat-btn').addEventListener('click', () => { $('hud').classList.remove('collapsed'); $('hud-input').focus(); });
    $('hud-toggle').addEventListener('click', () => $('hud').classList.toggle('collapsed'));
    $('currency-select').addEventListener('change', (e) => updateUI({ currency: e.target.value }));

    $('model-button').addEventListener('click', (e) => { e.stopPropagation(); $('modelSelector').classList.toggle('hidden'); });
    $('model-list').addEventListener('click', (e) => {
        const item = e.target.closest('[data-model-id]');
        if (!item) return;
        updateUI({ model: item.dataset.modelId });
        $('modelSelector').classList.add('hidden');
    });
    document.addEventListener('click', (e) => {
        if (!$('modelSelector').contains(e.target)) $('modelSelector').classList.add('hidden');
    });

    $('details-btn').addEventListener('click', () => {
        const m = currentModel();
        log('system', `Model ${m.id} · ${m.location} · ${m.status || 'unknown'} · Role ${appState.role} · Mode ${appState.toolMode}`);
    });

    $('hud-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const input = $('hud-input');
        sendCommand(input.value);
        input.value = '';
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
async function loadDashboard() {
    connectSocket();
    const [config, stats, models, rolesRes, inboxRes] = await Promise.all([
        api.config(), api.stats(), api.models(), api.roles(), api.inbox(),
    ]);
    updateUI({
        sttEngine: config.stt,
        ttsEngine: config.tts,
        toolMode: config.tool_mode || 'confirm',
        currencies: config.currencies,
        user: stats.user,
        rates: stats.rates,
        models: models.models,
        model: models.default,
        roles: rolesRes.roles,
        inbox: inboxRes.items,
        unreadInbox: inboxRes.unread,
    });
    if (!models.ollama_online && !models.cloud_ready) {
        log('system', 'No LLM reachable — using the built-in rule engine. Start Ollama or add an API key for full AI.');
    }
    setTimeout(() => updateUI({ stats, tokens: stats.tokens_processed }), 400);
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
