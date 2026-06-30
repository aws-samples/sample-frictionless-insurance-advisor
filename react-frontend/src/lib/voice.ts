/**
 * WebSocket client for the Voice AgentCore runtime.
 *
 * Browsers can't set custom headers on WebSocket, so we use AgentCore's
 * browser-friendly OAuth auth path: base64url-encode the Cognito access
 * token and pass it as a Sec-WebSocket-Protocol subprotocol of the form
 * `base64UrlBearerAuthorization.<base64url-jwt>` alongside a sentinel
 * subprotocol `base64UrlBearerAuthorization`.
 *
 * See "Browser JavaScript client with OAuth" in the AgentCore WebSocket docs:
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-websocket.html
 */
import { fetchAuthSession } from 'aws-amplify/auth';

export type TranscriptRole = 'user' | 'assistant';

export interface ConnectOptions {
  customerId: string | null;
  customerName: string | null;
  onAudioChunk: (audioBase64: string, format: string, sampleRate: number) => void;
  onTranscript: (text: string, isFinal: boolean, role: TranscriptRole) => void;
  onInterruption?: () => void;
  onConnected?: () => void;
  onDisconnected?: (closeCode?: number) => void;
  onError?: (msg: string) => void;
}

export interface VoiceConnection {
  send: (message: unknown) => void;
  setCustomer: (customerId: string | null, customerName: string | null) => void;
  close: () => void;
  isOpen: () => boolean;
}

function base64UrlEncode(value: string): string {
  // btoa handles plain ASCII; Cognito JWTs are ASCII (base64url header/payload/signature).
  return btoa(value)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}

function buildWsUrl(runtimeArn: string, region: string, sessionId: string): string {
  const encoded = encodeURIComponent(runtimeArn);
  const url = new URL(`https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${encoded}/ws`);
  url.searchParams.set('qualifier', 'DEFAULT');
  url.searchParams.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', sessionId);
  return url.toString().replace(/^https:/, 'wss:');
}

export async function connectVoice(options: ConnectOptions): Promise<VoiceConnection> {
  const runtimeArn = import.meta.env.VITE_VOICE_RUNTIME_ARN;
  const region = import.meta.env.VITE_AWS_REGION;
  if (!runtimeArn) throw new Error('VITE_VOICE_RUNTIME_ARN is not set');
  if (!region) throw new Error('VITE_AWS_REGION is not set');

  const session = await fetchAuthSession();
  const token = session.tokens?.accessToken?.toString();
  if (!token) throw new Error('Not authenticated');

  const sessionId = crypto.randomUUID();
  const wsUrl = buildWsUrl(runtimeArn, region, sessionId);

  const protocols = [
    `base64UrlBearerAuthorization.${base64UrlEncode(token)}`,
    'base64UrlBearerAuthorization',
  ];

  const ws = new WebSocket(wsUrl, protocols);

  return new Promise<VoiceConnection>((resolve, reject) => {
    let settled = false;

    const send = (message: unknown) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(message));
      }
    };

    const setCustomer = (customerId: string | null, customerName: string | null) => {
      send({ type: 'voice_set_customer', customerId, customerName });
    };

    ws.onopen = () => {
      // Initial customer context, if the page already has a selection.
      send({
        type: 'voice_init',
        customerId: options.customerId,
        customerName: options.customerName,
      });
      options.onConnected?.();
      if (!settled) {
        settled = true;
        resolve({
          send,
          setCustomer,
          close: () => ws.close(),
          isOpen: () => ws.readyState === WebSocket.OPEN,
        });
      }
    };

    ws.onerror = (event) => {
      const msg = 'WebSocket error';
      console.error('[voice-ws] error', event);
      options.onError?.(msg);
      if (!settled) {
        settled = true;
        reject(new Error(msg));
      }
    };

    ws.onclose = (event) => {
      options.onDisconnected?.(event.code);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'bidi_audio_stream' && data.audio) {
          options.onAudioChunk(data.audio, data.format || 'pcm', data.sample_rate || 24000);
        } else if (data.type === 'bidi_transcript_stream') {
          const role: TranscriptRole = data.role === 'user' ? 'user' : 'assistant';
          const isFinal = data.is_final !== false;
          options.onTranscript(data.text || '', isFinal, role);
        } else if (data.type === 'bidi_text_response' && data.text) {
          options.onTranscript(data.text, true, 'assistant');
        } else if (data.type === 'bidi_interruption') {
          options.onInterruption?.();
        }
      } catch (err) {
        console.error('[voice-ws] parse error', err);
      }
    };
  });
}
