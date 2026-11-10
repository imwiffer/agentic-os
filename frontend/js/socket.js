// Auto-reconnecting WebSocket: JSON text frames for control, binary frames for audio.
export class JarvisSocket {
    constructor(url, { onOpen, onClose, onMessage, onBinary } = {}) {
        Object.assign(this, { url, onOpen, onClose, onMessage, onBinary });
        this.retry = 0;
        this.connect();
    }

    connect() {
        this.ws = new WebSocket(this.url);
        this.ws.binaryType = 'arraybuffer';
        this.ws.onopen = () => { this.retry = 0; this.onOpen?.(); };
        this.ws.onclose = () => {
            this.onClose?.();
            const delay = Math.min(1000 * 2 ** this.retry++, 10000);
            setTimeout(() => this.connect(), delay);
        };
        this.ws.onerror = () => this.ws.close();
        this.ws.onmessage = (event) => {
            if (typeof event.data === 'string') {
                try { this.onMessage?.(JSON.parse(event.data)); } catch (e) { console.error('Bad WS message', e); }
            } else {
                this.onBinary?.(event.data);
            }
        };
    }

    get ready() { return this.ws?.readyState === WebSocket.OPEN; }

    send(obj) {
        if (!this.ready) return false;
        this.ws.send(JSON.stringify(obj));
        return true;
    }

    sendBinary(buffer) {
        if (!this.ready) return false;
        this.ws.send(buffer);
        return true;
    }
}
