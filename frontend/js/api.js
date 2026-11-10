// REST client for the FastAPI brain. Works when served by FastAPI (same origin) or opened from disk.
const BASE = location.protocol.startsWith('http') ? '' : 'http://localhost:8000';

async function request(path, options = {}) {
    const res = await fetch(BASE + path, options);
    if (!res.ok) throw new Error(`${options.method || 'GET'} ${path} -> ${res.status}`);
    return res.json();
}

export const api = {
    stats: () => request('/api/dashboard/stats'),
    models: () => request('/api/models'),
    config: () => request('/api/config'),
    user: () => request('/api/user'),
    chat: (input, state) => request('/api/jarvis/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input, state }),
    }),
};

export const WS_URL = BASE
    ? BASE.replace(/^http/, 'ws') + '/ws/jarvis'
    : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/jarvis`;
