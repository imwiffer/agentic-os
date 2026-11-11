// Jarvis voice engine.
//  STT "browser": Web Speech API (Chrome/Edge).  STT "server": streams 16 kHz PCM16 to the backend (Whisper/Vosk).
//  Level meter + simple energy VAD drive the .listening glow on the mic icon in both modes.
//  TTS: speechSynthesis by default, or server audio (ElevenLabs/Coqui) played from a Blob.

const TARGET_RATE = 16000;

function downsample(float32, fromRate) {
    if (fromRate === TARGET_RATE) return float32;
    const ratio = fromRate / TARGET_RATE;
    const out = new Float32Array(Math.floor(float32.length / ratio));
    for (let i = 0; i < out.length; i++) {
        const start = Math.floor(i * ratio), end = Math.min(Math.floor((i + 1) * ratio), float32.length);
        let sum = 0;
        for (let j = start; j < end; j++) sum += float32[j];
        out[i] = sum / Math.max(end - start, 1);
    }
    return out;
}

function toPCM16(float32) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
}

export class VoiceEngine {
    /**
     * @param {object} o
     * @param {'browser'|'server'} o.mode
     * @param {(text:string)=>void} o.onTranscript   final transcript (browser mode)
     * @param {(text:string)=>void} [o.onInterim]
     * @param {(on:boolean)=>void} [o.onLevel]       sound detected / silence
     * @param {()=>void} [o.onUtteranceStart]        server mode: speech began
     * @param {()=>void} [o.onUtteranceEnd]          server mode: speech ended
     * @param {(buf:ArrayBuffer)=>void} [o.sendAudio]
     * @param {(msg:string)=>void} [o.onError]
     */
    constructor(o) {
        Object.assign(this, { threshold: 0.018, silenceMs: 900, maxUtteranceMs: 15000 }, o);
        this.active = false;
        this.paused = false;
        this.speaking = false;
        this.utterance = false;
        this.preroll = [];
    }

    static browserSupported() {
        return 'SpeechRecognition' in window || 'webkitSpeechRecognition' in window;
    }

    async start() {
        if (this.mode === 'browser' && !VoiceEngine.browserSupported()) {
            throw new Error('Web Speech API not supported in this browser. Use Chrome/Edge, or enable STT_ENGINE=whisper on the server.');
        }
        this.active = true;
        this.paused = false;
        await this._startMic();
        if (this.mode === 'browser') this._startRecognition();
    }

    stop() {
        this.active = false;
        this.recognition?.abort();
        this.stream?.getTracks().forEach((t) => t.stop());
        this.ctx?.close().catch(() => {});
        this.ctx = this.stream = null;
        if (this.speaking) { this.speaking = false; this.onLevel?.(false); }
    }

    /** Stop capturing while Jarvis thinks/speaks (prevents it hearing itself). */
    pause() {
        this.paused = true;
        this.recognition?.abort();
        if (this.utterance) { this.utterance = false; this.onUtteranceEnd?.(); }
    }

    resume() {
        if (!this.active) return;
        this.paused = false;
        if (this.mode === 'browser') this._startRecognition();
    }

    // ----- Web Speech API -----
    _startRecognition() {
        if (!this.recognition) {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            const r = new SR();
            r.lang = 'en-US';
            r.interimResults = true;
            r.continuous = false;
            r.onresult = (event) => {
                let interim = '', final = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const res = event.results[i];
                    if (res.isFinal) final += res[0].transcript; else interim += res[0].transcript;
                }
                if (interim) this.onInterim?.(interim);
                if (final.trim()) {
                    this.paused = true; // wait for the reply before listening again
                    this.onTranscript(final.trim());
                }
            };
            r.onend = () => {
                this.recognizing = false;
                if (this.active && !this.paused) setTimeout(() => this._startRecognition(), 200);
            };
            r.onerror = (e) => {
                if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
                    this.onError?.('Microphone permission denied.');
                    this.stop();
                } else if (!['no-speech', 'aborted'].includes(e.error)) {
                    this.onError?.(`Speech recognition error: ${e.error}`);
                }
            };
            this.recognition = r;
        }
        if (this.recognizing || this.paused || !this.active) return;
        try { this.recognition.start(); this.recognizing = true; } catch { /* already started */ }
    }

    // ----- Mic capture, level meter, VAD, PCM streaming -----
    async _startMic() {
        this.stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        });
        try {
            this.ctx = new AudioContext({ sampleRate: TARGET_RATE });
        } catch {
            this.ctx = new AudioContext(); // e.g. Firefox: resample manually
        }
        await this.ctx.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url));
        const source = this.ctx.createMediaStreamSource(this.stream);
        const node = new AudioWorkletNode(this.ctx, 'pcm-processor');
        const mute = this.ctx.createGain();
        mute.gain.value = 0;
        node.port.onmessage = (e) => this._onFrame(e.data);
        source.connect(node).connect(mute).connect(this.ctx.destination);
    }

    _onFrame(frame) {
        if (!this.active) return;
        let sum = 0;
        for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
        const rms = Math.sqrt(sum / frame.length);
        const now = performance.now();

        if (rms > this.threshold) {
            this.lastVoice = now;
            if (!this.speaking) {
                this.speaking = true;
                if (!this.paused) this.onLevel?.(true);
                if (this.mode === 'server' && !this.paused && !this.utterance) {
                    this.utterance = true;
                    this.utteranceStart = now;
                    this.onUtteranceStart?.();
                    this.preroll.forEach((buf) => this.sendAudio?.(buf)); // include the word onset
                    this.preroll = [];
                }
            }
        } else if (this.speaking && now - this.lastVoice > this.silenceMs) {
            this.speaking = false;
            this.onLevel?.(false);
            this._endUtterance();
        }

        if (this.mode !== 'server' || this.paused) return;
        const pcm = toPCM16(downsample(frame, this.ctx.sampleRate)).buffer;
        if (this.utterance) {
            this.sendAudio?.(pcm);
            if (now - this.utteranceStart > this.maxUtteranceMs) this._endUtterance();
        } else {
            this.preroll.push(pcm);
            if (this.preroll.length > 3) this.preroll.shift();
        }
    }

    _endUtterance() {
        if (!this.utterance) return;
        this.utterance = false;
        this.paused = true; // wait for reply
        this.onUtteranceEnd?.();
    }
}

// ----- Text-to-speech -----
const PREFERRED_VOICES = ['Google UK English Male', 'Microsoft Ryan', 'Microsoft George', 'Daniel', 'Arthur', 'Microsoft Guy'];

export function cleanSpokenText(text) {
    if (!text) return '';
    let s = String(text);
    // Strip code blocks and inline code
    s = s.replace(/```[\s\S]*?```/g, ' ');
    s = s.replace(/`([^`]+)`/g, '$1');
    // Strip markdown links, headers, bullets
    s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
    s = s.replace(/[*_#~>|]/g, ' ');
    // Strip URLs and JSON
    s = s.replace(/https?:\/\/\S+/g, ' ');
    s = s.replace(/\{[^}]+\}/g, ' ');
    s = s.replace(/\s+/g, ' ').trim();
    // Grab only the first 1-2 punchy sentences, max 140 chars
    const sentences = s.match(/[^.!?]+[.!?]+/g);
    let candidate = sentences && sentences.length > 0 ? sentences.slice(0, 2).join(' ').trim() : s;
    if (candidate.length > 140) {
        candidate = candidate.slice(0, 137).trim() + '…';
    }
    return candidate || s.slice(0, 100);
}

export const speaker = {
    voice: null,
    muted: localStorage.getItem('jarvis_muted') === 'true',
    currentAudio: null,

    pickVoice() {
        const voices = window.speechSynthesis?.getVoices() ?? [];
        this.voice =
            PREFERRED_VOICES.map((n) => voices.find((v) => v.name.includes(n))).find(Boolean) ||
            voices.find((v) => v.lang === 'en-GB') ||
            voices.find((v) => v.lang.startsWith('en')) || null;
    },

    stop() {
        if (this.currentAudio) {
            try {
                this.currentAudio.pause();
                this.currentAudio.currentTime = 0;
            } catch {}
            this.currentAudio = null;
        }
        if ('speechSynthesis' in window) {
            try { speechSynthesis.cancel(); } catch {}
        }
    },

    toggleMute() {
        this.muted = !this.muted;
        try { localStorage.setItem('jarvis_muted', String(this.muted)); } catch {}
        if (this.muted) {
            this.stop();
        }
        return this.muted;
    },

    async speak(text, { onstart, onend } = {}) {
        if (this.muted) {
            onend?.();
            return;
        }
        const spoken = cleanSpokenText(text);
        if (!spoken) { onend?.(); return; }

        this.stop();

        // 1. Try high-fidelity Fish Audio Neural TTS first
        try {
            const res = await fetch('/api/fish_audio/speak', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: spoken }),
            });
            if (res.ok) {
                if (this.muted) { onend?.(); return; }
                const blob = await res.blob();
                this.playBlob(blob, { onstart, onend });
                return;
            }
        } catch (e) {
            console.debug('Fish Audio fallback to browser speech:', e);
        }

        // 2. Fallback to Web Speech Synthesis
        if (!('speechSynthesis' in window) || this.muted) { onend?.(); return; }
        if (!this.voice) this.pickVoice();
        speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(spoken);
        if (this.voice) u.voice = this.voice;
        u.rate = 1.05;
        u.pitch = 0.88;
        u.onstart = () => onstart?.();
        u.onend = u.onerror = () => onend?.();
        speechSynthesis.speak(u);
    },

    playBlob(blob, { onstart, onend } = {}) {
        if (this.muted) { onend?.(); return; }
        this.stop();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        this.currentAudio = audio;
        const done = () => {
            if (this.currentAudio === audio) this.currentAudio = null;
            URL.revokeObjectURL(url);
            onend?.();
        };
        audio.onplay = () => onstart?.();
        audio.onended = done;
        audio.onerror = done;
        audio.play().catch(done);
    },
};

if ('speechSynthesis' in window) speechSynthesis.onvoiceschanged = () => speaker.pickVoice();
