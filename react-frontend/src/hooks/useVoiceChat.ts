import { useCallback, useEffect, useRef, useState } from 'react';
import { v4 as uuidv4 } from 'uuid';

import audioWorkletUrl from '../lib/audio-processor.worklet.js?url';
import {
  connectVoice,
  ConsentEvent,
  ConsentStatus,
  TranscriptRole,
  VOICE_TOKEN_EXPIRED_CLOSE_CODE,
  VoiceConnection,
} from '../lib/voice';
import type { Customer, FormFillEvent, FormSchema, VoiceMessage } from '../types';


/** The voice model ends a session after about this long with no speech and no typed message. */
export const IDLE_LIMIT_SECONDS = 300;
/**
 * A second token-expiry close within this long of the previous one is not retried: a fresh token
 * lasts far longer, so the sign-in is not usable and the advisor must sign in again.
 */
const TOKEN_RECONNECT_MIN_GAP_MS = 30_000;
/**
 * What the page tells the advisor after the runtime ended the session because the access token expired:
 * 'renewed' (a new session was opened with a fresh token) or 'expired' (it could not be).
 */
export type SessionNotice = 'renewed' | 'expired' | null;
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

/** Why consent went back to pending, for a short notice under the header. */
export type ConsentNotice = 'customer_changed' | null;


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

  // --- consent to record ------------------------------------
  // The runtime is the source of truth; these mirror what it last said.
  const [consent, setConsent] = useState<ConsentStatus>('pending');
  const [consentAt, setConsentAt] = useState<string | null>(null);
  const [consentNotice, setConsentNotice] = useState<ConsentNotice>(null);
  const consentRef = useRef<ConsentStatus>('pending');
  const consentAtRef = useRef<string | null>(null);
  /** The mic has started and the runtime has been asked to put the question. */
  const promptedRef = useRef(false);
  const currentCustomerIdRef = useRef<string | null>(customer?.customer_id ?? null);
  currentCustomerIdRef.current = customer?.customer_id ?? null;

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

  // Pause. "Pause voice chat" switches the microphone off and silences the assistant's voice
  // at once; the session itself stays open, so typing, the transcript, consent and any open
  // form carry on (replies come as text only). It is not a pause on the server: the voice
  // model ends a session after about 5 minutes with no speech and no typing, so while paused
  // a countdown runs, and typing a message restarts it. If the session has ended by the time
  // the advisor resumes, resuming starts a new one.
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);
  pausedRef.current = paused;
  /** When the idle limit is reached while paused (epoch ms), or null when not paused. */
  const [idleDeadline, setIdleDeadline] = useState<number | null>(null);
  /** True after a resume had to start a new session because the old one had ended. */
  const [sessionRestarted, setSessionRestarted] = useState(false);
  const [sessionNotice, setSessionNotice] = useState<SessionNotice>(null);
  const lastTokenReconnectRef = useRef(0);
  /** Set below, once `connect`, `stopRecording` and `cutPlayback` exist. */
  const reconnectAfterTokenExpiryRef = useRef<() => void>(() => {});
  /** The audio buffer that is playing now, so a pause can stop it mid-word. */
  const currentSourceRef = useRef<AudioBufferSourceNode | null>(null);
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
      // Reconnect with the new customer context. The new session starts
      // without consent: picking a customer in the sidebar is a different
      // person from the prospect, so the question is asked again.
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
    src.connect(ctx.destination);
    currentSourceRef.current = src;
    src.onended = () => {
      if (currentSourceRef.current === src) currentSourceRef.current = null;
      playNext();
    };
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
        queuedSecondsRef.current += buf.duration;
        if (!isPlayingRef.current) playNext();
      } catch (err) {
        console.error('[voice-audio] queue error', err);
      }
    },
    [playNext]
  );

  /** Drop any text still waiting on audio (interruption, clear, disconnect). */
  /**
   * Stop the assistant's voice now: the buffer that is playing, and everything queued behind it.
   * Text that was being held back to stay in step with the sound is shown straight away.
   */
  const cutPlayback = useCallback(() => {
    const src = currentSourceRef.current;
    currentSourceRef.current = null;
    if (src) {
      src.onended = null;
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
    }
    playQueueRef.current = [];
    isPlayingRef.current = false;
    queuedSecondsRef.current = 0;
    lastReleaseAtRef.current = 0;
    pendingTextRef.current = pendingTextRef.current.map((c) => ({ ...c, releaseAt: 0 }));
    setState((prev) => ({ ...prev, speaking: false }));
  }, []);
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

  /** Mirror a consent event from the runtime. */
  const handleConsent = useCallback((event: ConsentEvent) => {
    consentRef.current = event.status;
    setConsent(event.status);
    setConsentNotice(event.reason === 'customer_changed' ? 'customer_changed' : null);
    if (event.status === 'confirmed') {
      const at = event.at;
      consentAtRef.current = at;
      setConsentAt(at);
    } else {
      consentAtRef.current = null;
      setConsentAt(null);
      if (event.status === 'declined') {
        // Declined: the session is ending and nothing was saved. Clear what is
        // on screen too.
        setState((prev) => ({ ...prev, history: [], partialUser: '', partialAssistant: '' }));
      }
    }
  }, []);

  const connect = useCallback(async (afterTokenExpiry = false) => {
    try {
      setState((prev) => ({ ...prev, error: null }));
      // A new session starts without consent.
      consentRef.current = 'pending';
      consentAtRef.current = null;
      setConsent('pending');
      setConsentAt(null);
      setConsentNotice(null);
      promptedRef.current = false;
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
        onConsent: handleConsent,
        onFormOpen: (productType, schema) =>
          formHandlersRef.current?.onFormOpen?.(productType, schema),
        onFormFill: (event) => formHandlersRef.current?.onFormFill?.(event),
        onConnected: () => setState((prev) => ({ ...prev, connected: true, error: null })),
        onDisconnected: (closeCode) => {
          setState((prev) => ({
            ...prev,
            connected: false,
            recording: false,
            speaking: false,
          }));
          if (closeCode === VOICE_TOKEN_EXPIRED_CLOSE_CODE) reconnectAfterTokenExpiryRef.current();
        },
        onError: (msg) => setState((prev) => ({ ...prev, error: msg })),
      });
      connRef.current = conn;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setState((prev) => ({ ...prev, error: msg }));
      if (afterTokenExpiry) setSessionNotice('expired');
    }
  }, [customer, queueAudio, handleTranscript, handleConsent]);

  const stopRecording = useCallback(() => {
    micCleanupRef.current();
    micCleanupRef.current = () => {};
    recordingRef.current = false;
    setState((prev) => ({ ...prev, recording: false }));
  }, []);

  const disconnect = useCallback(() => {
    stopRecording();
    setSessionNotice(null);
    setPaused(false);
    setIdleDeadline(null);
    connRef.current?.close();
    connRef.current = null;
    playQueueRef.current = [];
    isPlayingRef.current = false;
    discardPendingText();
    if (playCtxRef.current && playCtxRef.current.state !== 'closed') {
      playCtxRef.current.close();
    }
    playCtxRef.current = null;
    currentSourceRef.current = null;
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
      // First time the mic opens in a session without consent: have the
      // assistant ask the question now rather than wait to be spoken to.
      if (consentRef.current === 'pending' && !promptedRef.current) {
        promptedRef.current = true;
        connRef.current?.send({ type: 'voice_consent_prompt' });
      }
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
    // A typed message counts as activity for the voice model's idle limit.
    if (pausedRef.current) setIdleDeadline(Date.now() + IDLE_LIMIT_SECONDS * 1000);
  }, []);

  const clearHistory = useCallback(() => {
    // Close + reopen the WebSocket so the downstream insurance agent starts
    // a fresh session. Clears the on-screen history as a side effect.
    stopRecording();
    setPaused(false);
    setIdleDeadline(null);
    setSessionRestarted(false);
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

  /** Pause: microphone off, the assistant's voice cut now, session left open. */
  const pauseVoice = useCallback(() => {
    stopRecording();
    cutPlayback();
    setSessionRestarted(false);
    setPaused(true);
    setIdleDeadline(Date.now() + IDLE_LIMIT_SECONDS * 1000);
  }, [stopRecording, cutPlayback]);
  /**
   * Resume (or start) voice. If the session ended while paused, a new one is opened first;
   * consent is then asked again, because it belongs to a session.
   */
  const resumeVoice = useCallback(async () => {
    const wasPaused = pausedRef.current;
    setPaused(false);
    setIdleDeadline(null);
    if (!connRef.current?.isOpen()) {
      connRef.current = null;
      if (!connectingRef.current) {
        connectingRef.current = true;
        try {
          await connect();
        } finally {
          connectingRef.current = false;
        }
      }
      if (wasPaused) setSessionRestarted(true);
    }
    await startRecording();
  }, [connect, startRecording]);
  const dismissSessionRestarted = useCallback(() => setSessionRestarted(false), []);
  const dismissSessionNotice = useCallback(() => setSessionNotice(null), []);

  // The runtime ends a session when the access token it was opened with expires. The conversation on
  // screen stays; a new session starts with a fresh token and asks for recording consent again, and the
  // microphone stays off until the advisor starts it. If the fresh token is refused too, the page says so
  // and stops.
  reconnectAfterTokenExpiryRef.current = () => {
    const now = Date.now();
    const repeated = now - lastTokenReconnectRef.current < TOKEN_RECONNECT_MIN_GAP_MS;
    lastTokenReconnectRef.current = now;
    stopRecording();
    cutPlayback();
    setPaused(false);
    setIdleDeadline(null);
    connRef.current = null;
    if (repeated) {
      setSessionNotice('expired');
      return;
    }
    setSessionNotice('renewed');
    if (!connectingRef.current) {
      connectingRef.current = true;
      void connect(true).finally(() => {
        connectingRef.current = false;
      });
    }
  };
  const clearError = useCallback(() => setState((prev) => ({ ...prev, error: null })), []);

  /** The on-screen answer to the consent question. */
  const answerConsent = useCallback((status: 'confirmed' | 'declined') => {
    connRef.current?.send({ type: 'voice_consent', status });
  }, []);


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
    consent,
    consentAt,
    consentNotice,
    answerConsent,
    paused,
    idleDeadline,
    sessionRestarted,
    sessionNotice,
    dismissSessionNotice,
    connect,
    disconnect,
    startRecording,
    stopRecording,
    sendText,
    clearHistory,
    clearError,
    pauseVoice,
    resumeVoice,
    dismissSessionRestarted,
  };
}
