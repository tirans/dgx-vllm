#!/usr/bin/env bash
# 01-install-k3s.sh — Install K3s on DGX Spark with NVIDIA runtime support
set -euo pipefail

echo "=== Installing K3s on DGX Spark ==="

# Install K3s with containerd (default)
# --disable=traefik: we don't need the default ingress for LLM serving
# --write-kubeconfig-mode=644: allow non-root kubectl access
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
  --disable=traefik \
  --write-kubeconfig-mode=644 \
  --kubelet-arg=max-pods=64" sh -

# Wait for K3s to be ready
echo "Waiting for K3s node to be ready..."
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl wait --for=condition=Ready node --all --timeout=120s

# Configure NVIDIA container runtime for K3s containerd
echo "=== Configuring NVIDIA container runtime ==="

CONTAINERD_TEMPLATE="/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl"
mkdir -p "$(dirname "$CONTAINERD_TEMPLATE")"

# Only write if not already configured
if [ ! -f "$CONTAINERD_TEMPLATE" ] || ! grep -q "nvidia-container-runtime" "$CONTAINERD_TEMPLATE"; then
cat > "$CONTAINERD_TEMPLATE" << 'TOML'
version = 2

[plugins."io.containerd.grpc.v1.cri".containerd]
  default_runtime_name = "nvidia"

[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.nvidia]
  privileged_without_host_devices = false
  runtime_engine = ""
  runtime_root = ""
  runtime_type = "io.containerd.runc.v2"

[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.nvidia.options]
  BinaryName = "/usr/bin/nvidia-container-runtime"
  SystemdCgroup = true
TOML
  echo "NVIDIA runtime configured. Restarting K3s..."
  systemctl restart k3s
  sleep 10
  kubectl wait --for=condition=Ready node --all --timeout=120s
else
  echo "NVIDIA runtime already configured."
fi

# Symlink kubeconfig for convenience
mkdir -p "$HOME/.kube"
ln -sf /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"

echo "=== K3s installed and ready ==="
kubectl get nodes -o wide
