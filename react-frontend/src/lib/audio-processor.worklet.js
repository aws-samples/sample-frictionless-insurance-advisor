// Converts mic Float32 samples to Int16 PCM and hands them back to the main
// thread. Runs in the AudioWorklet thread for consistent timing.
class AudioCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const inputData = input[0];
    const pcmData = new Int16Array(inputData.length);
    for (let i = 0; i < inputData.length; i++) {
      const s = Math.max(-1, Math.min(1, inputData[i]));
      pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    this.port.postMessage({ type: "audio", data: pcmData });
    return true;
  }
}

registerProcessor("audio-capture-processor", AudioCaptureProcessor);
