#!/usr/bin/env bash
# clean-k3s.sh — Bash entry point for the K3s cleanup playbook
#
# Thin wrapper around ansible/clean.yml. The actual cleanup logic lives in
# the playbook so it stays consistent with the rest of the stack and works
# for multi-Spark fleets via inventory.
#
# Usage:
#   ./scripts/clean-k3s.sh                  # Interactive (asks before each step)
#   ./scripts/clean-k3s.sh --yes            # Skip prompts, nuke everything
#   ./scripts/clean-k3s.sh --keep-cache     # Skip /data/hf-cache (preserves model downloads)
#   ./scripts/clean-k3s.sh -i inventory/multi-spark.yml  # Wipe a fleet
#   ./scripts/clean-k3s.sh --help
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="${SCRIPT_DIR}/../ansible"

EXTRA_ARGS=()
INVENTORY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)
      EXTRA_ARGS+=("-e" "force=true")
      shift
      ;;
    --keep-cache)
      EXTRA_ARGS+=("-e" "keep_hf_cache=true")
      shift
      ;;
    -i|--inventory)
      INVENTORY="-i $2"
      shift 2
      ;;
    --help|-h)
      sed -n '3,15p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }

# --- Bootstrap Ansible if missing (shared with setup.sh) ---
cd "${ANSIBLE_DIR}"
# shellcheck source=lib/bootstrap-ansible.sh
source "${SCRIPT_DIR}/lib/bootstrap-ansible.sh"
bootstrap_ansible

# --- Sudo cache check ---
if ! sudo -n true 2>/dev/null; then
  info "Sudo cache empty. You will be prompted for your password."
  EXTRA_ARGS+=("--ask-become-pass")
fi

# --- Run the playbook ---
info "Running ansible-playbook clean.yml ${INVENTORY} ${EXTRA_ARGS[*]}"
echo ""

# shellcheck disable=SC2086
exec ansible-playbook clean.yml ${INVENTORY} "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
