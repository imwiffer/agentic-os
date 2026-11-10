// Tools registry: the functions the AI is allowed to trigger in the browser.
// Backend sends { action: "openTab", target: "Finances" } -> executeAction() -> tools.openTab("Finances").
import { appState, updateUI } from './store.js';
import { api } from './api.js';
import { flash, highlightCard } from './ui.js';

const BUSINESS_TABS = ['Overview', 'Finances', 'Progress', 'Audience'];

async function refreshStats() {
    const stats = await api.stats();
    updateUI({ stats, rates: stats.rates, tokens: Math.max(appState.tokens, stats.tokens_processed) });
    return stats;
}

// Show a card: stay on the current tab if it has it, otherwise jump to the tab that does.
function revealCard(id) {
    const tabs = appState.stats?.tabs ?? {};
    const inCurrent = (tabs[appState.businessTab] ?? []).some((c) => c.id === id);
    if (!inCurrent || appState.currentTab !== 'Dashboard') {
        const tab = Object.keys(tabs).find((t) => tabs[t].some((c) => c.id === id)) ?? 'Overview';
        tools.openTab(tab);
    }
    requestAnimationFrame(() => highlightCard(id));
}

export const tools = {
    /** Clicks the matching sidebar link or business tab, exactly like a user would. */
    openTab(tabName) {
        const el = document.querySelector(`[data-tab="${tabName}"]`) || document.querySelector(`[data-btab="${tabName}"]`);
        if (!el) return false;
        el.click();
        flash(el);
        if (BUSINESS_TABS.includes(tabName)) {
            document.getElementById('business-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        return true;
    },

    /** Fetches fresh numbers from the API and spotlights monthly income. */
    async getIncome(target = 'monthly_income') {
        const stats = await refreshStats();
        revealCard(target);
        return stats.finance.monthly_income;
    },

    async checkBalance(target = 'cash_on_hand') {
        const stats = await refreshStats();
        revealCard(target);
        return stats.finance.cash_on_hand;
    },

    /** Changes the currency dropdown (which triggers the normal change handler). */
    setCurrency(currency) {
        const sel = document.getElementById('currency-select');
        if (![...sel.options].some((o) => o.value === currency)) return false;
        sel.value = currency;
        sel.dispatchEvent(new Event('change'));
        flash(sel);
        return true;
    },

    scrollPage(direction = 'down') {
        const main = document.getElementById('main-scroll');
        const step = main.clientHeight * 0.75;
        const top = { up: main.scrollTop - step, down: main.scrollTop + step, top: 0, bottom: main.scrollHeight }[direction];
        main.scrollTo({ top: top ?? main.scrollTop + step, behavior: 'smooth' });
        return true;
    },

    selectModel(modelId) {
        if (!appState.models.some((m) => m.id === modelId)) return false;
        updateUI({ model: modelId });
        flash(document.getElementById('model-button'));
        return true;
    },

    highlightCard: revealCard,
};

export async function executeAction(command) {
    const fn = tools[command.action];
    if (!fn) {
        console.warn('Unknown action from brain:', command);
        return false;
    }
    updateUI({ toolCounts: { ...appState.toolCounts, [command.action]: (appState.toolCounts[command.action] ?? 0) + 1 } });
    try {
        return await fn(command.target, command.args);
    } catch (err) {
        console.error(`Tool ${command.action} failed`, err);
        return false;
    }
}

window.jarvisTools = tools; // e.g. jarvisTools.openTab('Finances') from DevTools
