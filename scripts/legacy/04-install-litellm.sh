#!/usr/bin/env bash
# 04-install-litellm.sh — Deploy LiteLLM proxy on K3s
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Deploying LiteLLM proxy ==="
kubectl apply -f "${SCRIPT_DIR}/litellm/deployment.yaml"

echo "Waiting for LiteLLM proxy to be ready..."
kubectl -n llm-stack rollout status deployment/litellm-proxy --timeout=120s

echo ""
echo "=== LiteLLM proxy deployed ==="
echo ""

# Get the external IP/port
EXTERNAL=$(kubectl -n llm-stack get svc litellm-proxy -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
NODEPORT=$(kubectl -n llm-stack get svc litellm-proxy -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || true)
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

if [ -n "$EXTERNAL" ]; then
  echo "LiteLLM Proxy URL: http://${EXTERNAL}:4000/v1"
elif [ -n "$NODEPORT" ]; then
  echo "LiteLLM Proxy URL: http://${NODE_IP}:${NODEPORT}/v1"
else
  echo "LiteLLM Proxy URL (port-forward): kubectl -n llm-stack port-forward svc/litellm-proxy 4000:4000"
  echo "Then use: http://localhost:4000/v1"
fi

echo ""
echo "API Key (default): sk-dgxspark-litellm-change-me"
echo "  (change this in litellm/deployment.yaml before production use)"
echo ""
echo "Available models:"
echo "  gemma4    — Gemma 4 26B-A4B (fast, always warm)"
echo "  qwen35   — Qwen 3.5 35B-A3B (heavy, scale-from-zero)"
echo "  gpt-4    — alias for gemma4 (compatibility)"
