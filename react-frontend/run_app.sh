#!/bin/bash
# Local run script for the React frontend.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env.local ]; then
  echo "No .env.local found. Running ./scripts/setup-env.sh to populate from SSM..."
  ./scripts/setup-env.sh
fi

echo "Installing dependencies..."
npm install

echo "Starting Vite dev server..."
npm run dev
