#!/usr/bin/env bash
# 02-install-nvidia-plugin.sh — Install NVIDIA device plugin for K3s
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

echo "=== Verifying NVIDIA driver ==="
nvidia-smi || { echo "ERROR: nvidia-smi not found. Ensure NVIDIA drivers are installed."; exit 1; }

echo "=== Installing NVIDIA device plugin via Helm ==="
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update

helm upgrade --install nvidia-device-plugin nvdp/nvidia-device-plugin \
  --namespace nvidia-device-plugin \
  --create-namespace \
  --set runtimeClassName=nvidia \
  --set gfd.enabled=true

echo "Waiting for device plugin pods..."
kubectl -n nvidia-device-plugin wait --for=condition=Ready pod -l app.kubernetes.io/name=nvidia-device-plugin --timeout=120s

echo "=== Verifying GPU visibility ==="
sleep 5
GPU_COUNT=$(kubectl get nodes -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for node in data['items']:
    gpus = node.get('status', {}).get('allocatable', {}).get('nvidia.com/gpu', '0')
    print(gpus)
" 2>/dev/null || echo "0")

echo "GPUs visible to K3s: $GPU_COUNT"
if [ "$GPU_COUNT" = "0" ]; then
  echo "WARNING: No GPUs detected. Check NVIDIA runtime config and restart K3s."
  exit 1
fi

echo "=== NVIDIA device plugin ready ==="
