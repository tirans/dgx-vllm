#!/usr/bin/env bash
# scripts/lib/bootstrap-ansible.sh
# Shared bootstrap function: ensures uv + ansible-core + collections are
# installed. Sourced by setup.sh and clean-k3s.sh so they stay in sync.
#
# Usage from another script:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/bootstrap-ansible.sh"
#   bootstrap_ansible

bootstrap_ansible() {
  local info ok error
  info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
  ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
  error() { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

  # Ensure uv is available
  if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | INSTALLER_NO_MODIFY_PATH=1 sh
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
  ok "uv available: $(uv --version)"

  # Ensure ansible-playbook is available
  if command -v ansible-playbook &>/dev/null; then
    ok "Ansible already installed: $(ansible --version | head -1)"
  else
    info "Installing Ansible..."
    if command -v apt-get &>/dev/null; then
      sudo apt-get update -qq
      sudo apt-get install -y -qq ansible-core
    else
      uv pip install --system ansible-core
    fi
    ok "Ansible installed: $(ansible --version | head -1)"
  fi

  # Ensure collections (idempotent — galaxy will skip if already present)
  if [[ -f requirements.yml ]]; then
    info "Installing Ansible Galaxy collections..."
    if ! ansible-galaxy collection install -r requirements.yml --force-with-deps; then
      error "ansible-galaxy collection install failed."
      return 1
    fi
    # Verify the required collection is actually present, otherwise later
    # playbook tasks fail with cryptic module-not-found errors.
    if ! ansible-galaxy collection list 2>/dev/null | grep -q '^kubernetes\.core'; then
      error "Required collection 'kubernetes.core' is not installed after galaxy install."
      return 1
    fi
    ok "Ansible Galaxy collections installed."
  fi
}
