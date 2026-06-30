import { Amplify } from 'aws-amplify';

// Configure Amplify to authenticate against the existing Cognito user pool
// that the backend CDK stacks provisioned. No Amplify backend (amplify init,
// DataStore, GraphQL, etc.) is used — this is purely the Amplify client
// library pointed at our CDK-managed pool.
//
// Values come from Vite env vars (see .env.example and scripts/setup-env.sh).
Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID,
      userPoolClientId: import.meta.env.VITE_COGNITO_CLIENT_ID,
      loginWith: {
        username: false,
        email: true,
      },
    },
  },
});
