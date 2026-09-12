// Browser client for /api/voice. Identify with member id + PIN, stream the microphone as 16 kHz PCM frames, play the
// agent's audio as it arrives, show transcripts, and re-dispatch every tool event as a CustomEvent("household:voice")
// on window so the household screen can subscribe without importing this file.

const AUDIO_RATE = 16000;

function bytesToBase64(bytes) {
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

function base64ToInt16(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
}

export class HouseholdVoice {
  constructor({ url, memberId, pin, onFrame } = {}) {
    this.url = url || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/voice`;
    this.memberId = memberId;
    this.pin = pin;
    this.onFrame = onFrame || (() => {});
    this.ws = null;
    this.audio = null;
    this.mic = null;
    this.workletNode = null;
    this.sources = new Set();
    this.nextPlayTime = 0;
    this.mode = null;
    this.identified = false;
  }

  // Connection

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: "identify", member_id: this.memberId, pin: this.pin }));
      ws.onerror = (event) => reject(event);
      ws.onclose = (event) => {
        this.stopMic();
        this.dispatch({ type: "closed", code: event.code, reason: event.reason });
        if (!this.identified) reject(new Error(`closed ${event.code} ${event.reason}`));
      };
      ws.onmessage = (event) => {
        const frame = JSON.parse(event.data);
        this.handle(frame);
        if (frame.type === "identified") {
          this.identified = true;
          resolve(frame);
        }
      };
    });
  }

  send(frame) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(frame));
  }

  sendText(text) {
    this.send({ type: "bidi_text_input", text, role: "user" });
  }

  stop() {
    this.send({ type: "stop" });
    this.stopMic();
    this.cancelPlayback();
  }

  // Frames

  handle(frame) {
    switch (frame.type) {
      case "identified":
        this.mode = frame.mode;
        break;
      case "fallback":
        this.mode = frame.mode;
        this.stopMic();
        this.cancelPlayback();
        break;
      case "bidi_audio_stream":
        this.play(frame);
        break;
      case "bidi_interruption":
        this.cancelPlayback();
        break;
      default:
        break;
    }
    this.dispatch(frame);
    this.onFrame(frame);
  }

  dispatch(frame) {
    window.dispatchEvent(new CustomEvent("household:voice", { detail: frame }));
  }

  // Microphone -> 16 kHz PCM frames

  async startMic() {
    if (this.mode !== "sonic") throw new Error("voice mode is not active");
    await this.ensureAudio();
    this.mic = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    await this.audio.audioWorklet.addModule(new URL("./worklet.js", import.meta.url));
    const source = this.audio.createMediaStreamSource(this.mic);
    this.workletNode = new AudioWorkletNode(this.audio, "pcm16-downsampler", { numberOfOutputs: 0 });
    this.workletNode.port.onmessage = (event) => {
      const bytes = new Uint8Array(event.data);
      this.send({ type: "bidi_audio_input", audio: bytesToBase64(bytes), format: "pcm", sample_rate: AUDIO_RATE, channels: 1 });
    };
    source.connect(this.workletNode);
  }

  stopMic() {
    if (this.workletNode) {
      this.workletNode.port.onmessage = null;
      this.workletNode.disconnect();
      this.workletNode = null;
    }
    if (this.mic) {
      for (const track of this.mic.getTracks()) track.stop();
      this.mic = null;
    }
  }

  // Playback: scheduled AudioBufferSourceNodes, cancelled on interruption

  async ensureAudio() {
    if (!this.audio) this.audio = new (window.AudioContext || window.webkitAudioContext)();
    if (this.audio.state === "suspended") await this.audio.resume();
  }

  play(frame) {
    if (!this.audio) {
      this.ensureAudio().then(() => this.play(frame));
      return;
    }
    const samples = base64ToInt16(frame.audio);
    if (!samples.length) return;
    const rate = frame.sample_rate || AUDIO_RATE;
    const buffer = this.audio.createBuffer(1, samples.length, rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 0x8000;
    const source = this.audio.createBufferSource();
    source.buffer = buffer;
    source.connect(this.audio.destination);
    const start = Math.max(this.audio.currentTime, this.nextPlayTime);
    source.start(start);
    this.nextPlayTime = start + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  cancelPlayback() {
    for (const source of this.sources) {
      try { source.stop(); } catch (_) { /* already stopped */ }
    }
    this.sources.clear();
    this.nextPlayTime = 0;
  }
}
