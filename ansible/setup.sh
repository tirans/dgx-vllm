#!/usr/bin/env bash
# setup.sh — Bootstrap Ansible and run the DGX Spark LLM Stack playbook
#
# Usage:
#   ./setup.sh                          # Full install (single Spark, local)
#   ./setup.sh --dry-run                # Dry run (ansible --check)
#   ./setup.sh --tags kubeai,litellm    # Partial run
#   ./setup.sh -i inventory/multi.yml   # Multi-Spark via SSH
#   ./setup.sh --skip-bootstrap         # Skip Ansible install, run playbook only
#   ./setup.sh --help                   # Show this help
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Defaults
INVENTORY=""
TAGS=""
DRY_RUN=""
SKIP_BOOTSTRAP=false
EXTRA_ARGS=()

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      sed -n '3,10p' "$0"
      exit 0
      ;;
    --dry-run)
      DRY_RUN="--check"
      shift
      ;;
    --tags)
      TAGS="--tags $2"
      shift 2
      ;;
    --skip-bootstrap)
      SKIP_BOOTSTRAP=true
      shift
      ;;
    -i|--inventory)
      INVENTORY="-i $2"
      shift 2
      ;;
    --vault-password-file)
      VAULT_PASS_FILE="$2"
      shift 2
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

# --- Color helpers ---
info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error() { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

# --- Step 1: Install Ansible if needed (shared with clean-k3s.sh) ---
if [[ "${SKIP_BOOTSTRAP}" == false ]]; then
  # shellcheck source=../scripts/lib/bootstrap-ansible.sh
  source "${SCRIPT_DIR}/../scripts/lib/bootstrap-ansible.sh"
  bootstrap_ansible
fi

# --- Step 2: Create vault if it doesn't exist ---
VAULT_FILE="group_vars/all/vault.yml"
VAULT_PASS_FILE=".vault_pass"

if [[ ! -f "${VAULT_FILE}" ]]; then
  info "First run detected — creating Ansible Vault for secrets."
  echo ""

  # Get vault password
  if [[ ! -f "${VAULT_PASS_FILE}" ]]; then
    echo "Choose a vault password (used to encrypt/decrypt secrets):"
    read -s -r -p "Vault password: " VAULT_PASSWORD
    echo ""
    read -s -r -p "Confirm: " VAULT_PASSWORD_CONFIRM
    echo ""
    if [[ "${VAULT_PASSWORD}" != "${VAULT_PASSWORD_CONFIRM}" ]]; then
      error "Passwords do not match."
      exit 1
    fi
    echo "${VAULT_PASSWORD}" > "${VAULT_PASS_FILE}"
    chmod 600 "${VAULT_PASS_FILE}"
    ok "Vault password saved to ${VAULT_PASS_FILE}"
  fi

  # Collect secrets
  echo ""
  read -s -r -p "HuggingFace token (hf_...): " HF_TOKEN
  echo ""
  if [[ -z "${HF_TOKEN}" ]] || [[ ! "${HF_TOKEN}" =~ ^hf_ ]]; then
    error "Invalid HuggingFace token. Must start with 'hf_'."
    exit 1
  fi

  read -s -r -p "LiteLLM master key (or press Enter to auto-generate): " LITELLM_KEY
  echo ""

  if [[ -z "${LITELLM_KEY}" ]]; then
    LITELLM_KEY="sk-dgxspark-$(openssl rand -hex 16)"
    ok "Auto-generated LiteLLM key (retrieve later with: ansible-vault view ${VAULT_FILE})"
  fi

  # Create vault file
  cat > "${VAULT_FILE}" <<VAULT_EOF
---
hf_token: "${HF_TOKEN}"
litellm_master_key: "${LITELLM_KEY}"
VAULT_EOF

  if ! ansible-vault encrypt "${VAULT_FILE}"; then
    error "Failed to encrypt vault. Removing unencrypted file."
    rm -f "${VAULT_FILE}"
    exit 1
  fi
  ok "Vault created and encrypted at ${VAULT_FILE}"
  echo ""
else
  # Validate existing vault is actually encrypted
  if ! head -1 "${VAULT_FILE}" | grep -q '^\$ANSIBLE_VAULT'; then
    error "Vault file exists but is not encrypted (corrupted from a previous failed run?)."
    error "Delete it and re-run:  rm ${VAULT_FILE} ${VAULT_PASS_FILE} && ./setup.sh"
    exit 1
  fi
  ok "Vault exists at ${VAULT_FILE}"
fi

# Ensure vault password file exists for subsequent runs
if [[ ! -f "${VAULT_PASS_FILE}" ]]; then
  warn "Vault password file not found. You will be prompted."
  EXTRA_ARGS+=("--ask-vault-pass")
fi

# --- Step 3: Add vault pass file and .gitignore ---
if ! grep -q ".vault_pass" ../.gitignore 2>/dev/null; then
  echo -e "\n# Ansible vault password\nansible/.vault_pass" >> ../.gitignore
fi

# --- Step 4: Check if sudo needs a password ---
if ! sudo -n true 2>/dev/null; then
  info "Sudo requires a password. You will be prompted."
  EXTRA_ARGS+=("--ask-become-pass")
fi

# --- Step 5: Run the playbook ---
info "Running ansible-playbook site.yml ${DRY_RUN} ${TAGS} ${INVENTORY}"
echo ""

# shellcheck disable=SC2086
ansible-playbook site.yml \
  ${DRY_RUN} \
  ${TAGS} \
  ${INVENTORY} \
  "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

echo ""
ok "Done."
