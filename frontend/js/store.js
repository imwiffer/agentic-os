// Global reactive state (a tiny Pinia/Redux-style store).
// updateUI(newState) merges a patch into appState and notifies renderers of changed keys only.

export const appState = {
    currentTab: 'Dashboard',   // sidebar section
    businessTab: 'Overview',   // "Your business, in focus" tab
    voiceActive: false,        // mic toggled on
    listening: false,          // sound currently detected
    status: 'idle',            // idle | listening | transcribing | thinking | speaking | offline
    connected: false,          // WebSocket to the brain
    authRequired: false,
    authenticated: true,
    authUser: null,
    currency: 'USD',
    rates: { USD: 1 },
    currencies: ['USD'],
    models: [],
    model: 'qwen2.5:0.5b',
    ollamaOnline: false,
    ollamaUrl: 'http://147.189.168.95:11434',
    failsafeEnabled: true,
    hardware: null,
    welcomeGreeting: '',
    role: 'jarvis',            // active agent persona
    roles: [],
    toolMode: 'auto',
    stats: null,
    user: null,
    system: null,              // VPS health snapshot
    connectors: [],            // live connector telemetry
    inbox: [],
    unreadInbox: 0,
    memories: [],
    jobs: [],
    audit: [],
    steps: [],                 // live tool execution trace
    chatMessages: [],          // persistent chat tab history
    consoleCwd: '',
    workspaceFiles: [],
    approvalRequest: null,     // { id, tool, args, reason }
    tokens: 0,
    engine: null,              // last reasoning engine used: llm | rules
    sttEngine: 'browser',
    ttsEngine: 'browser',
    toolCounts: {},
};

const listeners = new Set();

export function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
}

export function updateUI(newState) {
    const changed = {};
    for (const [key, value] of Object.entries(newState)) {
        if (appState[key] !== value) {
            appState[key] = value;
            changed[key] = value;
        }
    }
    if (Object.keys(changed).length) listeners.forEach((fn) => fn(changed, appState));
    return appState;
}

window.appState = appState;
window.updateUI = updateUI;
