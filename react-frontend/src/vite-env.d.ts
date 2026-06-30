/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AWS_REGION: string;
  readonly VITE_COGNITO_USER_POOL_ID: string;
  readonly VITE_COGNITO_CLIENT_ID: string;
  readonly VITE_API_BASE_URL: string;
  readonly VITE_AGENT_RUNTIME_ARN: string;
  readonly VITE_VOICE_RUNTIME_ARN: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
