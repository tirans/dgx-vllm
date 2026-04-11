#!/usr/bin/env bash
# 03-install-kubeai.sh — Install KubeAI operator and models
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Creating namespace and secrets ==="
kubectl create namespace llm-stack --dry-run=client -o yaml | kubectl apply -f -

# Check for HF secret
if ! kubectl -n llm-stack get secret hf-secret &>/dev/null; then
  echo "ERROR: hf-secret not found. Create it first:"
  echo "  kubectl -n llm-stack create secret generic hf-secret --from-literal=token=hf_YOUR_TOKEN"
  exit 1
fi

# Prepare model cache directory on host
echo "=== Preparing model cache directory ==="
sudo mkdir -p /data/hf-cache
sudo chmod 777 /data/hf-cache

echo "=== Installing KubeAI operator ==="
helm repo add kubeai https://www.kubeai.org
helm repo update

helm upgrade --install kubeai kubeai/kubeai \
  --namespace llm-stack \
  --values "${SCRIPT_DIR}/kubeai/values-kubeai.yaml" \
  --wait \
  --timeout 300s

echo "Waiting for KubeAI operator to be ready..."
kubectl -n llm-stack wait --for=condition=Available deployment/kubeai --timeout=120s

echo "=== Installing model catalog ==="
helm upgrade --install kubeai-models kubeai/models \
  --namespace llm-stack \
  --values "${SCRIPT_DIR}/kubeai/values-models.yaml" \
  --wait \
  --timeout 120s

echo "=== KubeAI installed ==="
echo ""
echo "Models:"
kubectl -n llm-stack get models
echo ""
echo "Pods (Gemma4 should be starting, Qwen3.5 will be at 0 replicas):"
kubectl -n llm-stack get pods
echo ""
echo "KubeAI API is available at:"
echo "  kubectl -n llm-stack port-forward svc/kubeai 8000:80"
echo "  curl http://localhost:8000/openai/v1/models"
