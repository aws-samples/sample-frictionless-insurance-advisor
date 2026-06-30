#!/bin/bash

echo "🗑️ Destroying Insurance Advisor AgentCore..."

# Destroy the CDK stack
echo "🏗️ Destroying CDK stack..."
uv run cdk destroy --all --force

echo "✅ Destruction completed!"