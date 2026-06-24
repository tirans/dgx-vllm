#!/usr/bin/env bash
# k3s-reboot-recover.sh — Auto-heal K3s workloads after a host reboot.
#
# Managed by Ansible (roles/k3s). Deployed to /usr/local/bin and run on every
# boot by k3s-reboot-recover.service (oneshot, After=k3s.service). Safe to run
# by hand at any time; this is the automated form of scripts/recover-from-reboot.sh.
#
# After an ungraceful reboot, K3s pods are routinely left in a terminal/limbo
# phase (Completed/Error/Unknown/UnexpectedAdmissionError) that their controllers
# do not always replace promptly, and the GPU may not be re-advertised before the
# model pod is admitted. This script waits for the node to be Ready, force-deletes
# that debris so its Deployment/DaemonSet/StatefulSet recreates it fresh, and —
# only if needed — nudges the NVIDIA device plugin so a GPU becomes allocatable.
#
# Env knobs (all optional):
#   KUBECONFIG             kubeconfig path           (default /etc/rancher/k3s/k3s.yaml)
#   K3S_REAP_NODE_TIMEOUT  secs to wait for Ready    (default 300)
#   K3S_REAP_GPU           "true"/"false" GPU step   (default true)
#   K3S_REAP_GPU_TIMEOUT   secs to wait for GPU      (default 120)
set -uo pipefail   # deliberately NOT -e: best-effort, must never fail the boot

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"; export KUBECONFIG
KUBECTL="$(command -v kubectl 2>/dev/null || echo /usr/local/bin/kubectl)"
NODE_TIMEOUT="${K3S_REAP_NODE_TIMEOUT:-300}"
GPU_ENABLED="${K3S_REAP_GPU:-true}"
GPU_TIMEOUT="${K3S_REAP_GPU_TIMEOUT:-120}"
log() { echo "[k3s-reboot-recover] $*"; }

command -v jq >/dev/null 2>&1 || { log "jq not found; cannot filter pods safely — exiting."; exit 0; }

# 1) Wait for the node to be Ready (bounded; never block the boot indefinitely).
log "waiting up to ${NODE_TIMEOUT}s for node Ready..."
deadline=$(( $(date +%s) + NODE_TIMEOUT ))
until "$KUBECTL" wait --for=condition=Ready node --all --timeout=10s >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    log "node not Ready after ${NODE_TIMEOUT}s; exiting without reaping."
    exit 0
  fi
  sleep 5
done
log "node is Ready."

# 2) Reap reboot debris: pods in a terminal phase (Failed/Succeeded/Unknown) that
#    are NOT owned by a Job (Job pods are meant to terminate — e.g. the KubeAI
#    cache loader). Covers Completed, Error, Unknown, Evicted and the GPU pod's
#    UnexpectedAdmissionError. jq is installed by the prerequisites role.
debris="$("$KUBECTL" get pods -A -o json 2>/dev/null | jq -r '
  .items[]
  | select(((.metadata.ownerReferences // []) | any(.kind == "Job")) | not)
  | select(.status.phase == "Failed" or .status.phase == "Succeeded" or .status.phase == "Unknown")
  | "\(.metadata.namespace) \(.metadata.name)"')"

if [ -z "$debris" ]; then
  log "no reboot debris pods to reap."
else
  log "reaping $(printf '%s\n' "$debris" | grep -c .) debris pod(s)..."
  printf '%s\n' "$debris" | while read -r ns name; do
    { [ -z "$ns" ] || [ -z "$name" ]; } && continue
    log "  delete ${ns}/${name}"
    "$KUBECTL" -n "$ns" delete pod "$name" --force --grace-period=0 >/dev/null 2>&1 || true
  done
fi

# 3) GPU safety net. If the device-plugin pod was reaped above, its DaemonSet
#    recreates it and it re-registers on its own — so we only force a bounce if a
#    GPU is still not allocatable (e.g. the plugin pod was Running but lost its
#    kubelet registration across the containerd restart).
if [ "$GPU_ENABLED" = "true" ]; then
  log "waiting up to ${GPU_TIMEOUT}s for an allocatable GPU..."
  gpu_deadline=$(( $(date +%s) + GPU_TIMEOUT )); gpu="0"
  while [ "$(date +%s)" -lt "$gpu_deadline" ]; do
    gpu="$("$KUBECTL" get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo 0)"
    { [ -n "$gpu" ] && [ "$gpu" != "0" ]; } && break
    sleep 10
  done
  if [ -z "$gpu" ] || [ "$gpu" = "0" ]; then
    log "no allocatable GPU after ${GPU_TIMEOUT}s; bouncing nvidia-device-plugin."
    "$KUBECTL" -n nvidia-device-plugin rollout restart daemonset >/dev/null 2>&1 \
      || "$KUBECTL" -n nvidia-device-plugin delete pods --all --force --grace-period=0 >/dev/null 2>&1 \
      || true
  else
    log "GPU allocatable: ${gpu}."
  fi
fi

log "recovery complete."
exit 0
