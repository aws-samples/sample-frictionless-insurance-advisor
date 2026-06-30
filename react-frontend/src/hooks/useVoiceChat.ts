import { useCallback, useEffect, useRef, useState } from 'react';
import { v4 as uuidv4 } from 'uuid';

import audioWorkletUrl from '../lib/audio-processor.worklet.js?url';
import { connectVoice, TranscriptRole, VoiceConnection } from '../lib/voice';
import type { Customer, VoiceMessage } from '../types';

export interface VoiceChatState {
  connected: boolean;
  recording: boolean;
  speaking: boolean;
  error: string | null;
  partialUser: string;
  partialAssistant: string;
  history: VoiceMessage[];
}

/**
 * Voice chat state + controls for the Voice page.
 *
 * One WebSocket per session. Opening/closing the session is the "clear"
 * action - a new connection means a new downstream insurance session id.
 *
 * We tell the voice agent which customer is in scope via a `voice_set_customer`
 * control message every time the `customer` prop changes. That way, switching
 * customers mid-chat routes subsequent tool calls to the new customer without
 * tearing down the voice connection.
 */
export function useVoiceChat(customer: Customer | null) {
  const [state, setState] = useState<VoiceChatState>({
    connected: false,
    recording: false,
    speaking: false,
    error: null,
    partialUser: '',
    partialAssistant: '',
    history: [],
  });

  const connRef = useRef<VoiceConnection | null>(null);
  const micCleanupRef = useRef<() => void>(() => {});
  const playCtxRef = useRef<AudioContext | null>(null);
  const playQueueRef = useRef<AudioBuffer[]>([]);
  const isPlayingRef = useRef(false);
  const forceNewBubbleRef = useRef(false);
  // Tracks recording state in a ref so queueAudio (captured by useCallback)
  // can see the current value without being recreated on every toggle.
  // When false, incoming audio chunks are dropped - user gets text only.
  const recordingRef = useRef(false);
  // Guards against duplicate connect() in React StrictMode's double-invoke
  // of effects during development.
  const connectingRef = useRef(false);

  // Keep the voice agent's customer context in sync when the selection
  // changes. The voice agent constructs BidiAgent (and its session manager)
  // once per WebSocket using the first voice_init it receives. So:
  // - switching between two existing customers can be done via voice_set_customer
  //   on the open connection (memory id changes can't be applied mid-stream
  //   anyway in BidiAgent - we just update the system prompt context)
  // - crossing the prospect/existing boundary requires a reconnect so the
  //   server-side BidiAgent is rebuilt with the right session manager
  //   (memory enabled vs. disabled).
  const lastCustomerIdRef = useRef<string | null>(customer?.customer_id ?? null);
  useEffect(() => {
    const nextId = customer?.customer_id ?? null;
    const prevId = lastCustomerIdRef.current;
    if (nextId === prevId) return;
    lastCustomerIdRef.current = nextId;

    const crossingProspectBoundary = (prevId === null) !== (nextId === null);
    if (crossingProspectBoundary && connRef.current?.isOpen()) {
      // Reconnect with the new customer context.
      connRef.current.close();
      connRef.current = null;
      if (!connectingRef.current) {
        connectingRef.current = true;
        void connect().finally(() => {
          connectingRef.current = false;
        });
      }
      return;
    }
    connRef.current?.setCustomer(nextId, customer?.name ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- connect is stable
  }, [customer]);

  const playNext = useCallback(() => {
    const ctx = playCtxRef.current;
    if (!ctx || ctx.state === 'closed') {
      isPlayingRef.current = false;
      return;
    }
    const buf = playQueueRef.current.shift();
    if (!buf) {
      isPlayingRef.current = false;
      setState((prev) => ({ ...prev, speaking: false }));
      return;
    }
    isPlayingRef.current = true;
    setState((prev) => ({ ...prev, speaking: true }));
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.onended = () => playNext();
    src.start();
  }, []);

  const queueAudio = useCallback(
    (audioBase64: string, format: string, sampleRate: number) => {
      // Only speak responses when voice chat is active. When the user is
      // typing, the transcripts still come through but we drop the audio.
      if (!recordingRef.current) return;
      try {
        if (!playCtxRef.current || playCtxRef.current.state === 'closed') {
          playCtxRef.current = new AudioContext({ sampleRate });
        }
        const ctx = playCtxRef.current;

        const binary = atob(audioBase64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

        if (format !== 'pcm') {
          console.warn('[voice-audio] unsupported format', format);
          return;
        }
        const aligned = new Uint8Array(bytes.byteLength);
        aligned.set(bytes);
        const pcm = new Int16Array(aligned.buffer);
        const floats = new Float32Array(pcm.length);
        for (let i = 0; i < pcm.length; i++) {
          floats[i] = pcm[i] / (pcm[i] < 0 ? 0x8000 : 0x7fff);
        }
        const buf = ctx.createBuffer(1, floats.length, sampleRate);
        buf.getChannelData(0).set(floats);
        playQueueRef.current.push(buf);
        if (!isPlayingRef.current) playNext();
      } catch (err) {
        console.error('[voice-audio] queue error', err);
      }
    },
    [playNext]
  );

  const handleTranscript = useCallback(
    (text: string, isFinal: boolean, role: TranscriptRole) => {
      if (!text) return;
      setState((prev) => {
        if (role === 'user') {
          if (!isFinal) return { ...prev, partialUser: text };
          return {
            ...prev,
            partialUser: '',
            history: [...prev.history, { id: uuidv4(), role: 'user', text }],
          };
        }
        if (!isFinal) {
          return {
            ...prev,
            partialAssistant: prev.partialAssistant + text,
            speaking: true,
          };
        }
        const hist = [...prev.history];
        const last = hist[hist.length - 1];
        const append = last && last.role === 'assistant' && !forceNewBubbleRef.current;
        if (append) {
          hist[hist.length - 1] = {
            ...last,
            text: last.text + (last.text ? ' ' : '') + text,
          };
        } else {
          hist.push({ id: uuidv4(), role: 'assistant', text });
          forceNewBubbleRef.current = false;
        }
        return {
          ...prev,
          partialAssistant: '',
          history: hist,
          speaking: false,
        };
      });
    },
    []
  );

  const connect = useCallback(async () => {
    try {
      setState((prev) => ({ ...prev, error: null }));
      const conn = await connectVoice({
        customerId: customer?.customer_id ?? null,
        customerName: customer?.name ?? null,
        onAudioChunk: queueAudio,
        onTranscript: handleTranscript,
        onInterruption: () => {
          playQueueRef.current = [];
          isPlayingRef.current = false;
          forceNewBubbleRef.current = true;
          setState((prev) => ({ ...prev, speaking: false, partialAssistant: '' }));
        },
        onConnected: () => setState((prev) => ({ ...prev, connected: true, error: null })),
        onDisconnected: () =>
          setState((prev) => ({
            ...prev,
            connected: false,
            recording: false,
            speaking: false,
          })),
        onError: (msg) => setState((prev) => ({ ...prev, error: msg })),
      });
      connRef.current = conn;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setState((prev) => ({ ...prev, error: msg }));
    }
  }, [customer, queueAudio, handleTranscript]);

  const stopRecording = useCallback(() => {
    micCleanupRef.current();
    micCleanupRef.current = () => {};
    recordingRef.current = false;
    setState((prev) => ({ ...prev, recording: false }));
  }, []);

  const disconnect = useCallback(() => {
    stopRecording();
    connRef.current?.close();
    connRef.current = null;
    playQueueRef.current = [];
    isPlayingRef.current = false;
    if (playCtxRef.current && playCtxRef.current.state !== 'closed') {
      playCtxRef.current.close();
    }
    playCtxRef.current = null;
    setState({
      connected: false,
      recording: false,
      speaking: false,
      error: null,
      partialUser: '',
      partialAssistant: '',
      history: [],
    });
  }, [stopRecording]);

  const startRecording = useCallback(async () => {
    if (!connRef.current?.isOpen()) {
      setState((prev) => ({ ...prev, error: 'Not connected' }));
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      const ctx = new AudioContext({ sampleRate: 16000 });
      await ctx.audioWorklet.addModule(audioWorkletUrl);
      const source = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, 'audio-capture-processor');

      worklet.port.onmessage = (event) => {
        if (!connRef.current?.isOpen()) return;
        if (event.data.type !== 'audio') return;
        const pcm = event.data.data as Int16Array;
        const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm.buffer)));
        connRef.current.send({
          type: 'bidi_audio_input',
          audio: base64,
          format: 'pcm',
          sample_rate: 16000,
          channels: 1,
        });
      };

      source.connect(worklet);
      // Don't route mic back to speakers.

      micCleanupRef.current = () => {
        try {
          worklet.disconnect();
          source.disconnect();
          stream.getTracks().forEach((t) => t.stop());
          ctx.close();
        } catch {
          /* ignore */
        }
      };

      recordingRef.current = true;
      setState((prev) => ({ ...prev, recording: true }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setState((prev) => ({ ...prev, error: `Microphone: ${msg}` }));
    }
  }, []);

  const sendText = useCallback((text: string) => {
    if (!connRef.current?.isOpen()) return;
    setState((prev) => ({
      ...prev,
      history: [...prev.history, { id: uuidv4(), role: 'user', text }],
    }));
    connRef.current.send({ type: 'bidi_text_input', text, role: 'user' });
  }, []);

  const clearHistory = useCallback(() => {
    // Close + reopen the WebSocket so the downstream insurance agent starts
    // a fresh session. Clears the on-screen history as a side effect.
    stopRecording();
    connRef.current?.close();
    connRef.current = null;
    playQueueRef.current = [];
    isPlayingRef.current = false;
    if (playCtxRef.current && playCtxRef.current.state !== 'closed') {
      playCtxRef.current.close();
    }
    playCtxRef.current = null;
    setState({
      connected: false,
      recording: false,
      speaking: false,
      error: null,
      partialUser: '',
      partialAssistant: '',
      history: [],
    });
    // Schedule a reconnect after state settles.
    setTimeout(() => {
      if (!connectingRef.current && !connRef.current) {
        connectingRef.current = true;
        void connect().finally(() => {
          connectingRef.current = false;
        });
      }
    }, 0);
  }, [connect, stopRecording]);

  const clearError = useCallback(() => setState((prev) => ({ ...prev, error: null })), []);

  useEffect(() => {
    // Auto-connect when the hook mounts (Voice page opens). Disconnect
    // when it unmounts (user navigates away or signs out). StrictMode
    // invokes effects twice in dev; connectingRef guards against the
    // duplicate connect, and the unmount path tears down either way.
    if (connectingRef.current || connRef.current) return;
    connectingRef.current = true;
    void connect().finally(() => {
      connectingRef.current = false;
    });
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot
  }, []);

  return {
    ...state,
    connect,
    disconnect,
    startRecording,
    stopRecording,
    sendText,
    clearHistory,
    clearError,
  };
}
