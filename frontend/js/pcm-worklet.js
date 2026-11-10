// AudioWorklet: forwards mic samples to the main thread in ~100 ms batches.
class PCMProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.batchSize = Math.round(sampleRate / 10);
        this.buffer = new Float32Array(this.batchSize);
        this.offset = 0;
    }

    process(inputs) {
        const channel = inputs[0]?.[0];
        if (channel) {
            let i = 0;
            while (i < channel.length) {
                const n = Math.min(channel.length - i, this.batchSize - this.offset);
                this.buffer.set(channel.subarray(i, i + n), this.offset);
                this.offset += n;
                i += n;
                if (this.offset === this.batchSize) {
                    this.port.postMessage(this.buffer.slice(0));
                    this.offset = 0;
                }
            }
        }
        return true;
    }
}

registerProcessor('pcm-processor', PCMProcessor);
