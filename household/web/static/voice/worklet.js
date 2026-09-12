// AudioWorklet: microphone float samples at the context rate -> 16 kHz mono Int16 PCM in 100 ms frames.
// Runs off the main thread. Each frame is posted as a transferable ArrayBuffer of 1600 samples (3200 bytes).

const TARGET_RATE = 16000;
const FRAME_SAMPLES = TARGET_RATE / 10; // 100 ms

class Pcm16Downsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.position = 0;
    this.pending = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.carry = new Float32Array(0);
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    // Join the carried tail with the new block so interpolation never reads past the end.
    const samples = new Float32Array(this.carry.length + channel.length);
    samples.set(this.carry, 0);
    samples.set(channel, this.carry.length);
    let index = this.position;
    while (index + 1 < samples.length) {
      const base = Math.floor(index);
      const frac = index - base;
      const value = samples[base] * (1 - frac) + samples[base + 1] * frac;
      const clamped = Math.max(-1, Math.min(1, value));
      this.pending[this.filled++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      if (this.filled === FRAME_SAMPLES) {
        this.port.postMessage(this.pending.buffer, [this.pending.buffer]);
        this.pending = new Int16Array(FRAME_SAMPLES);
        this.filled = 0;
      }
      index += this.ratio;
    }
    const consumed = Math.floor(index);
    this.carry = samples.slice(consumed);
    this.position = index - consumed;
    return true;
  }
}

registerProcessor("pcm16-downsampler", Pcm16Downsampler);
