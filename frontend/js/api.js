// REST client for the FastAPI brain. Works when served by FastAPI (same origin over http/https) or opened locally.
const BASE = location.protocol.startsWith('http') ? '' : 'http://127.0.0.1:8000';

async function request(path, options = {}) {
    const res = await fetch(BASE + path, { credentials: 'same-origin', ...options });
    if (res.status === 401) {
        window.dispatchEvent(new CustomEvent('jarvis:unauthorized'));
    }
    if (!res.ok) {
        let detail = `${options.method || 'GET'} ${path} -> ${res.status}`;
        try {
            const body = await res.json();
            if (body?.detail) detail = body.detail;
        } catch {}
        throw new Error(detail);
    }
    return res.json();
}

const json = (method, body) => ({
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
});

export const api = {
    authStatus: () => request('/api/auth/status'),
    login: (token) => request('/api/login', json('POST', { token })),
    logout: () => request('/api/logout', { method: 'POST' }),
    stats: () => request('/api/dashboard/stats'),
    syncLive: () => request('/api/dashboard/sync-live', json('POST', {})),
    models: () => request('/api/models'),
    addModel: (payload) => request('/api/models', json('POST', payload)),
    deleteModel: (id) => request(`/api/models/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    config: () => request('/api/config'),
    setToolMode: (payload) => request('/api/config/tool-mode', json('POST', payload)),
    connectors: () => request('/api/connectors'),
    hardware: () => request('/api/hardware'),
    detectHardware: () => request('/api/hardware/detect', json('POST', {})),
    welcome: () => request('/api/jarvis/welcome'),
    user: () => request('/api/user'),
    saveUser: (name, title = 'System Operator', email = '') =>
        request('/api/user', json('POST', typeof name === 'object' ? name : { name, title, email })),
    saveFinance: (payload) => request('/api/finance', json('POST', payload)),
    saveMetric: (payload) => request('/api/metrics', json('POST', payload)),
    deleteMetric: (key) => request(`/api/metrics/${encodeURIComponent(key)}`, { method: 'DELETE' }),
    saveUsage: (payload) => request('/api/usage', json('POST', payload)),
    system: () => request('/api/system'),
    roles: () => request('/api/roles'),
    memories: (q = '') => request(`/api/memories${q ? `?q=${encodeURIComponent(q)}` : ''}`),
    addMemory: (text, tags = '') => request('/api/memories', json('POST', { text, tags })),
    deleteMemory: (id) => request(`/api/memories/${id}`, { method: 'DELETE' }),
    jobs: () => request('/api/jobs'),
    addJob: (payload) => request('/api/jobs', json('POST', payload)),
    deleteJob: (id) => request(`/api/jobs/${id}`, { method: 'DELETE' }),
    runJob: (id) => request(`/api/jobs/${id}/run`, { method: 'POST' }),
    inbox: () => request('/api/inbox'),
    markInboxRead: (id = null) => request('/api/inbox/read', json('POST', { id })),
    audit: (limit = 100) => request(`/api/audit?limit=${limit}`),
    chat: (input, state, role = 'jarvis') => request('/api/jarvis/chat', json('POST', { input, state, role })),
    chatHistory: (limit = 60) => request(`/api/chat/history?limit=${limit}`),
    clearChatHistory: () => request('/api/chat/history', { method: 'DELETE' }),
    consoleExec: (command, cwd = '') => request('/api/console/exec', json('POST', { command, cwd })),
    files: (path = '.') => request(`/api/files?path=${encodeURIComponent(path)}`),
    readFile: (path) => request(`/api/files/read?path=${encodeURIComponent(path)}`),
    writeFile: (path, content, append = false) => request('/api/files/write', json('POST', { path, content, append })),
    webSearch: (query) => request('/api/web/search', json('POST', { query })),
    webFetch: (url) => request('/api/web/fetch', json('POST', { url })),
    tokenStats: () => request('/api/tokens/stats'),
    searchExtensions: (q) => request(`/api/extensions/search?q=${encodeURIComponent(q)}`),
    installExtension: (namespace, name, version = 'latest') => request('/api/extensions/install', json('POST', { namespace, name, version })),
    listExtensions: () => request('/api/extensions/list'),
    configureRemoteGpu: (url) => request('/api/remote_gpu/configure', json('POST', { url })),
    voicePersonas: () => request('/api/voice/personas'),
    setVoicePersona: (voice_id) => request('/api/voice/persona', json('POST', { voice_id })),
    fishAudioSpeak: (text, voice_id = '') => request('/api/fish_audio/speak', json('POST', { text, voice_id })),
};

export const WS_URL = BASE
    ? BASE.replace(/^http/, 'ws') + '/ws/jarvis'
    : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/jarvis`;
