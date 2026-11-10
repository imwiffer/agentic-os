// Global reactive state (a tiny Pinia/Redux-style store).
// updateUI(newState) merges a patch into appState and notifies renderers of changed keys only.

export const appState = {
    currentTab: 'Dashboard',   // sidebar section
    businessTab: 'Overview',   // "Your business, in focus" tab
    voiceActive: false,        // mic toggled on
    listening: false,          // sound currently detected
    status: 'idle',            // idle | listening | transcribing | thinking | speaking | offline
    connected: false,          // WebSocket to the brain
    currency: 'USD',
    rates: { USD: 1 },
    currencies: ['USD'],
    models: [],
    model: null,
    stats: null,
    user: null,
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

// Handy for debugging from DevTools: appState, updateUI({currentTab: 'Inbox'})
window.appState = appState;
window.updateUI = updateUI;
