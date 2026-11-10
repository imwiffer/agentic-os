// REST client for the FastAPI brain. Works when served by FastAPI (same origin over http/https) or opened locally.
const BASE = location.protocol.startsWith('http') ? '' : 'http://localhost:8000';

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
    models: () => request('/api/models'),
    config: () => request('/api/config'),
    user: () => request('/api/user'),
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
};

export const WS_URL = BASE
    ? BASE.replace(/^http/, 'ws') + '/ws/jarvis'
    : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/jarvis`;
