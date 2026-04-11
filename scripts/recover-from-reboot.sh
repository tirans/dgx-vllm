#!/usr/bin/env bash
# recover-from-reboot.sh — Clean up zombie K3s pods after a host reboot
#
# K3s normally handles reboots gracefully, but unclean shutdowns can leave
# pods stuck in 'Unknown' or 'UnexpectedAdmissionError' state. This script
# force-deletes them so deployments/daemonsets recreate them.
#
# Usage:
#   ./scripts/recover-from-reboot.sh           # Run on the Spark
#   ssh spark "bash -s" < scripts/recover-from-reboot.sh  # Run remotely
set -euo pipefail

KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
export KUBECONFIG

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error() { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

if ! kubectl get nodes &>/dev/null; then
  error "kubectl can't reach the cluster. Is K3s running?"
  echo "Try: sudo systemctl status k3s"
  exit 1
fi

info "Checking node status..."
kubectl get nodes
echo ""

info "Looking for zombie pods (Unknown / Error / UnexpectedAdmissionError)..."
ZOMBIE_PODS=$(kubectl get pods -A 2>/dev/null \
  | grep -E 'Unknown|Error|UnexpectedAdmission' \
  | awk '{print $1, $2}')

if [[ -z "${ZOMBIE_PODS}" ]]; then
  ok "No zombie pods found. Cluster is clean."
else
  ZOMBIE_COUNT=$(echo "${ZOMBIE_PODS}" | wc -l)
  warn "Found ${ZOMBIE_COUNT} zombie pod(s). Force-deleting..."
  echo "${ZOMBIE_PODS}" | while read -r ns name; do
    [[ -z "${ns}" || -z "${name}" ]] && continue
    info "Deleting ${ns}/${name}"
    kubectl -n "${ns}" delete pod "${name}" --force --grace-period=0 2>&1 \
      | grep -v "^Warning:" || true
  done
  ok "Cleanup complete."
fi

echo ""
info "Waiting 30s for replacement pods to start..."
sleep 30

info "Current pod state:"
kubectl get pods -A

echo ""
GPU_COUNT=$(kubectl get node -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "?")
if [[ "${GPU_COUNT}" == "0" || -z "${GPU_COUNT}" ]]; then
  warn "GPU not yet visible to scheduler. Wait 30-60s and re-run kubectl get nodes."
  warn "If still 0, restart the device plugin pod:"
  echo "  kubectl -n nvidia-device-plugin delete pods --all"
else
  ok "GPUs allocatable: ${GPU_COUNT}"
fi
