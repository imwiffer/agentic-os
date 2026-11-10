// Agentic OS bootstrap: wires DOM events, the WebSocket loop, voice and the tools registry.
//
// The loop: Listen (mic) -> Send (WS) -> Process (LLM on backend) -> Action (WS command)
//           -> React (tools registry updates UI) -> Speak (TTS) -> listen again.
import { appState, subscribe, updateUI } from './store.js';
import { api, WS_URL } from './api.js';
import { JarvisSocket } from './socket.js';
import { currentModel, initRenderer, log, logInterim, clearInterim } from './ui.js';
import { executeAction } from './tools.js';
import { VoiceEngine, speaker } from './voice.js';

const $ = (id) => document.getElementById(id);

let voice = null;
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
});

// ---------------- Brain connection ----------------
const socket = new JarvisSocket(WS_URL, {
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

async function handleMessage(msg) {
    switch (msg.type) {
        case 'ready':
            break;
        case 'status':
            updateUI({ status: msg.status });
            break;
        case 'transcript':
            clearInterim();
            log('user', msg.text);
            break;
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
    const meta = [msg.engine, msg.latency_ms != null ? `${msg.latency_ms}ms` : ''].filter(Boolean).join(' · ');
    log('jarvis', msg.text, meta);
    if (msg.note) log('system', msg.note);
    updateUI({ engine: msg.engine === 'llm' ? `llm (${msg.model})` : 'rules', tokens: msg.tokens_total ?? appState.tokens + (msg.tokens || 0) });
    if (msg.audio) {
        pendingReply = msg.text; // server TTS incoming; fall back to browser voice if it never arrives
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
}

// ---------------- Sending commands ----------------
async function sendCommand(text) {
    text = text.trim();
    if (!text) return;
    voice?.pause();
    clearInterim();
    log('user', text);
    updateUI({ status: 'thinking' });

    if (socket.send({ type: 'user_text', text })) return;

    // REST fallback when the socket is down
    try {
        const res = await api.chat(text, publicState());
        for (const cmd of res.actions) {
            log('action', `⚡ ${cmd.action}${cmd.target ? ' → ' + cmd.target : ''}`);
            await executeAction(cmd);
        }
        handleReply({ ...res, text: res.response, audio: false });
    } catch {
        log('system', 'Brain offline. Start the backend: cd backend; uvicorn app.main:app --reload');
        updateUI({ status: 'offline' });
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
        onUtteranceStart: () => {
            socket.send({ type: 'audio_start', sample_rate: 16000 });
        },
        onUtteranceEnd: () => socket.send({ type: 'audio_end' }),
        sendAudio: (buf) => socket.sendBinary(buf),
        onError: (message) => {
            log('system', message);
            if (/denied|not supported/i.test(message)) toggleVoice(false);
        },
    });
    try {
        if (mode === 'server' && !socket.ready) throw new Error('Server STT needs the brain connection.');
        await voice.start();
        updateUI({ voiceActive: true });
        log('system', `Voice online (${mode === 'browser' ? 'Web Speech API' : 'server ' + appState.sttEngine}). Try "open finances".`);
    } catch (err) {
        voice = null;
        log('system', err.message || String(err));
        updateUI({ voiceActive: false });
    }
}

// ---------------- DOM wiring ----------------
function selectMode(mode) {
    const models = appState.models.filter((m) => m.location === mode);
    if (!models.length) return;
    if (currentModel().location === mode) return;
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
        log('system', `Model ${m.id} · ${m.location} · ${m.status || 'unknown'} · STT ${appState.sttEngine} · TTS ${appState.ttsEngine}`);
    });

    $('hud-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const input = $('hud-input');
        sendCommand(input.value);
        input.value = '';
    });

    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.code === 'Space') { e.preventDefault(); toggleVoice(); }
    });

    // Keep the server's view of the UI in sync with local changes.
    subscribe((changed) => {
        if (['currentTab', 'businessTab', 'currency', 'model', 'voiceActive'].some((k) => k in changed)) {
            socket.send({ type: 'state', state: publicState() });
        }
    });
}

// ---------------- Boot ----------------
async function boot() {
    initRenderer();
    bindDOM();
    log('system', 'Booting Agentic OS…');
    try {
        const [config, stats, models] = await Promise.all([api.config(), api.stats(), api.models()]);
        updateUI({
            sttEngine: config.stt,
            ttsEngine: config.tts,
            currencies: config.currencies,
            user: stats.user,
            rates: stats.rates,
            models: models.models,
            model: models.default,
        });
        if (!models.ollama_online && !models.cloud_ready) {
            log('system', 'No LLM reachable — using the built-in rule engine. Start Ollama or add an API key for full AI.');
        }
        setTimeout(() => updateUI({ stats, tokens: stats.tokens_processed }), 400); // count up from $0
    } catch (err) {
        console.error(err);
        updateUI({ status: 'offline' });
        log('system', 'Backend unreachable. Run: cd backend; uvicorn app.main:app --reload');
    }
}

boot();
