#!/usr/bin/env bash
# 05-test-stack.sh — Validate the entire LLM stack
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

# Determine proxy URL
PROXY_URL="${LITELLM_URL:-}"
API_KEY="${LITELLM_KEY:-sk-dgxspark-litellm-change-me}"

if [ -z "$PROXY_URL" ]; then
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
  NODEPORT=$(kubectl -n llm-stack get svc litellm-proxy -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || true)
  EXTERNAL=$(kubectl -n llm-stack get svc litellm-proxy -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)

  if [ -n "$EXTERNAL" ]; then
    PROXY_URL="http://${EXTERNAL}:4000"
  elif [ -n "$NODEPORT" ]; then
    PROXY_URL="http://${NODE_IP}:${NODEPORT}"
  else
    echo "Cannot determine proxy URL. Set LITELLM_URL or use port-forward."
    echo "  kubectl -n llm-stack port-forward svc/litellm-proxy 4000:4000 &"
    echo "  LITELLM_URL=http://localhost:4000 $0"
    exit 1
  fi
fi

echo "=== Testing LLM Stack ==="
echo "Proxy: ${PROXY_URL}"
echo ""

# --- Test 1: Health check ---
echo "--- Test 1: LiteLLM health ---"
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "${PROXY_URL}/health/readiness")
if [ "$HTTP_CODE" = "200" ]; then
  echo "PASS: LiteLLM is healthy"
else
  echo "FAIL: LiteLLM returned HTTP ${HTTP_CODE}"
  exit 1
fi

# --- Test 2: List models ---
echo ""
echo "--- Test 2: Available models ---"
curl -s "${PROXY_URL}/v1/models" \
  -H "Authorization: Bearer ${API_KEY}" | python3 -m json.tool 2>/dev/null || \
curl -s "${PROXY_URL}/v1/models" -H "Authorization: Bearer ${API_KEY}"

# --- Test 3: Chat with Gemma 4 (primary) ---
echo ""
echo "--- Test 3: Chat with gemma4 ---"
RESPONSE=$(curl -s "${PROXY_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
    "model": "gemma4",
    "messages": [
      {"role": "system", "content": "You are a K3s troubleshooting assistant. Be concise."},
      {"role": "user", "content": "How do I check if the NVIDIA device plugin is running in K3s? One-liner only."}
    ],
    "max_tokens": 200,
    "temperature": 0.1
  }')

echo "$RESPONSE" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    content = data['choices'][0]['message']['content']
    model = data.get('model', 'unknown')
    usage = data.get('usage', {})
    print(f'Model: {model}')
    print(f'Tokens: {usage}')
    print(f'Response: {content[:500]}')
    print('PASS')
except Exception as e:
    print(f'FAIL: {e}')
    print(data if 'data' in dir() else 'No response')
" 2>/dev/null || echo "FAIL: Could not parse response"

# --- Test 4: Chat with Qwen 3.5 (may cold-start) ---
echo ""
echo "--- Test 4: Chat with qwen35 (may take 60-90s on cold start) ---"
RESPONSE=$(curl -s --max-time 300 "${PROXY_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
    "model": "qwen35",
    "messages": [
      {"role": "user", "content": "Write a one-liner bash command to find all K3s pods in CrashLoopBackOff state."}
    ],
    "max_tokens": 200,
    "temperature": 0.1
  }')

echo "$RESPONSE" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    content = data['choices'][0]['message']['content']
    model = data.get('model', 'unknown')
    print(f'Model: {model}')
    print(f'Response: {content[:500]}')
    print('PASS')
except Exception as e:
    print(f'FAIL (may be cold-starting): {e}')
" 2>/dev/null || echo "FAIL: Qwen3.5 may still be cold-starting. Retry in ~90s."

echo ""
echo "=== Stack test complete ==="
echo ""
echo "Use from any LAN client:"
echo "  export OPENAI_API_BASE=${PROXY_URL}/v1"
echo "  export OPENAI_API_KEY=${API_KEY}"
