import { useCallback, useEffect, useRef, useState } from 'react';
import { v4 as uuidv4 } from 'uuid';

import audioWorkletUrl from '../lib/audio-processor.worklet.js?url';
import { connectVoice, TranscriptRole, VoiceConnection } from '../lib/voice';
import type { Customer, FormFillEvent, FormSchema, VoiceMessage } from '../types';

import { usePersistentBoolean } from './usePersistentBoolean';

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
/** One assistant transcript event held back until its audio is playing. */
interface PendingChunk {
  text: string;
  /** Wall-clock ms after which this may be shown. */
  releaseAt: number;
  /** True for a segment commit (moves into history), false for a partial. */
  isFinal: boolean;
}

export interface VoiceFormHandlers {
  onFormOpen?: (productType: string, schema: FormSchema) => void;
  onFormFill?: (event: FormFillEvent) => void;
}

export function useVoiceChat(
  customer: Customer | null,
  formHandlers?: VoiceFormHandlers,
  /** Field paths already filled from the customer record; relayed to the agent. */
  formPrefill?: Record<string, string>
) {
  // Held in a ref so re-renders of the owner don't tear down the socket:
  // `connect` must not depend on identity-unstable callbacks.
  const formHandlersRef = useRef<VoiceFormHandlers | undefined>(formHandlers);
  formHandlersRef.current = formHandlers;
  const formPrefillRef = useRef<Record<string, string> | undefined>(formPrefill);
  formPrefillRef.current = formPrefill;

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

  // Output mute — purely local. Audio still decodes and plays through a
  // GainNode whose gain we drop to 0, so the transcript still streams at
  // speaking pace; only the sound is silenced. Persisted so the choice
  // survives reconnects and reloads.
  const [muted, , toggleMuted] = usePersistentBoolean('insadv.voiceMuted', false);
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  /** Master gain all playback routes through, so mute is one value change. */
  const gainRef = useRef<GainNode | null>(null);

  // --- transcript/audio synchronisation ---------------------------------
  // Nova Sonic streams text and audio over the same socket, but text arrives
  // at network speed while audio can only play at speaking speed. Rendering
  // text on arrival therefore runs it ahead of the voice — sometimes by
  // several seconds, and briefly showing words the listener hasn't heard yet.
  //
  // So assistant text is held and released in step with playback: each chunk
  // waits until the audio already queued ahead of it has drained. When the
  // queue is empty (start of a turn, or text-only mode with audio dropped)
  // the delay is zero, so nothing feels sluggish.
  /** Seconds of audio queued but not yet started. */
  const queuedSecondsRef = useRef(0);
  /** Assistant text waiting for its audio, in arrival order. */
  const pendingTextRef = useRef<PendingChunk[]>([]);
  /** Keeps release times non-decreasing so chunks can never reorder. */
  const lastReleaseAtRef = useRef(0);
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
    connRef.current?.setCustomer(nextId, customer?.name ?? null, formPrefillRef.current);
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
      queuedSecondsRef.current = 0;
      setState((prev) => ({ ...prev, speaking: false }));
      return;
    }
    // This buffer is now playing rather than waiting, so it no longer counts
    // towards the backlog that text is being held behind.
    queuedSecondsRef.current = Math.max(0, queuedSecondsRef.current - buf.duration);
    isPlayingRef.current = true;
    setState((prev) => ({ ...prev, speaking: true }));
    const src = ctx.createBufferSource();
    src.buffer = buf;
    // Route through the master gain (mute), falling back to the destination
    // if for some reason it isn't set up yet.
    src.connect(gainRef.current ?? ctx.destination);
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
          const newCtx = new AudioContext({ sampleRate });
          const gain = newCtx.createGain();
          gain.gain.value = mutedRef.current ? 0 : 1;
          gain.connect(newCtx.destination);
          playCtxRef.current = newCtx;
          gainRef.current = gain;
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
        queuedSecondsRef.current += buf.duration;
        if (!isPlayingRef.current) playNext();
      } catch (err) {
        console.error('[voice-audio] queue error', err);
      }
    },
    [playNext]
  );

  /** Drop any text still waiting on audio (interruption, clear, disconnect). */
  const toggleMute = useCallback(() => toggleMuted(), [toggleMuted]);

  // Apply the mute state to live playback. A short ramp avoids a click, and
  // driving it from an effect keeps it correct however `muted` changed
  // (button, or restored from localStorage once a context exists).
  useEffect(() => {
    const gain = gainRef.current;
    const ctx = playCtxRef.current;
    if (gain && ctx) {
      gain.gain.setTargetAtTime(muted ? 0 : 1, ctx.currentTime, 0.01);
    }
  }, [muted]);

  /** Drop text still waiting on audio (interruption, clear, disconnect). */
  const discardPendingText = useCallback(() => {
    pendingTextRef.current = [];
    lastReleaseAtRef.current = 0;
    queuedSecondsRef.current = 0;
  }, []);

  /**
   * Commit a finished assistant segment into history. Segments append into one
   * bubble until an interruption forces a new one.
   */
  const commitAssistantSegment = useCallback(
    (prev: VoiceChatState, text: string): VoiceChatState => {
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
      return { ...prev, partialAssistant: '', history: hist, speaking: false };
    },
    []
  );

  // Release held text once the audio queued ahead of it has played. An 80ms
  // tick is fine — well below where stepping becomes visible — and it does
  // nothing at all while the queue is empty.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (pendingTextRef.current.length === 0) return;
      const now = Date.now();
      const due: PendingChunk[] = [];
      while (pendingTextRef.current.length && pendingTextRef.current[0].releaseAt <= now) {
        due.push(pendingTextRef.current.shift()!);
      }
      if (due.length === 0) return;
      setState((prev) =>
        due.reduce(
          (acc, chunk) =>
            chunk.isFinal
              ? commitAssistantSegment(acc, chunk.text)
              : {
                  ...acc,
                  partialAssistant: acc.partialAssistant + chunk.text,
                  speaking: true,
                },
          prev
        )
      );
    }, 80);
    return () => window.clearInterval(id);
  }, [commitAssistantSegment]);

  const handleTranscript = useCallback(
    (text: string, isFinal: boolean, role: TranscriptRole) => {
      if (!text) return;

      // The advisor's own words need no pacing — they already spoke them.
      if (role === 'user') {
        setState((prev) =>
          isFinal
            ? {
                ...prev,
                partialUser: '',
                history: [...prev.history, { id: uuidv4(), role: 'user', text }],
              }
            : { ...prev, partialUser: text }
        );
        return;
      }

      // Assistant text is paced against playback. Both partials and segment
      // commits go through the queue, otherwise the commit would jump the
      // final wording ahead of the audio still being spoken.
      const backlogMs = recordingRef.current ? queuedSecondsRef.current * 1000 : 0;
      const nothingWaiting = pendingTextRef.current.length === 0;

      // Nothing meaningful to wait for — apply now and keep it responsive.
      if (backlogMs < 120 && nothingWaiting) {
        setState((prev) =>
          isFinal
            ? commitAssistantSegment(prev, text)
            : { ...prev, partialAssistant: prev.partialAssistant + text, speaking: true }
        );
        return;
      }

      const releaseAt = Math.max(lastReleaseAtRef.current, Date.now() + backlogMs);
      lastReleaseAtRef.current = releaseAt;
      pendingTextRef.current.push({ text, releaseAt, isFinal });
    },
    [commitAssistantSegment]
  );

  const connect = useCallback(async () => {
    try {
      setState((prev) => ({ ...prev, error: null }));
      const conn = await connectVoice({
        customerId: customer?.customer_id ?? null,
        customerName: customer?.name ?? null,
        formPrefill: formPrefillRef.current,
        onAudioChunk: queueAudio,
        onTranscript: handleTranscript,
        onInterruption: () => {
          playQueueRef.current = [];
          isPlayingRef.current = false;
          forceNewBubbleRef.current = true;
          // The audio these chunks were waiting for has been dropped, so they
          // would otherwise sit in the queue forever.
          discardPendingText();
          setState((prev) => ({ ...prev, speaking: false, partialAssistant: '' }));
        },
        onFormOpen: (productType, schema) =>
          formHandlersRef.current?.onFormOpen?.(productType, schema),
        onFormFill: (event) => formHandlersRef.current?.onFormFill?.(event),
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
    discardPendingText();
    if (playCtxRef.current && playCtxRef.current.state !== 'closed') {
      playCtxRef.current.close();
    }
    playCtxRef.current = null;
    gainRef.current = null;
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
    discardPendingText();
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
    muted,
    connect,
    disconnect,
    startRecording,
    stopRecording,
    sendText,
    clearHistory,
    clearError,
    toggleMute,
  };
}
