# DGX Spark LLM Stack

One-command LLM inference stack for NVIDIA DGX Spark (GB10). Deploys [KubeAI](https://github.com/kubeai-project/kubeai) + [vLLM](https://github.com/vllm-project/vllm) + [LiteLLM](https://github.com/BerriAI/litellm) on K3s with Ansible, giving you a self-hosted OpenAI-compatible API on your LAN.

```bash
cd ansible && ./setup.sh    # that's it
```

## What You Get

- **OpenAI-compatible API** at `http://<spark-ip>:4000/v1/...` accessible from any LAN client
- **Two MoE models** running on a single GB10 GPU with scale-from-zero
- **Tool/function calling** support on Gemma 4
- **Drop-in compatibility** with Open WebUI, Continue.dev, Claude Code, Cursor, and any OpenAI SDK client
- **Idempotent deploys** -- safe to re-run, resume after failure, or update configuration

## Architecture

```
Clients (laptops, IDEs, Open WebUI, curl)
          |
          v
  LiteLLM Proxy :4000      model aliasing, API keys, routing
          |
          v
  KubeAI Gateway :8000     scale-from-zero, model lifecycle
          |
    +-----+-----+
    v           v
  vLLM        vLLM          GPU inference (shared nvidia.com/gpu: 1)
  Gemma 4     Qwen 3.5
  26B-A4B     35B-A3B
```

All components run as K3s pods in the `llm-stack` namespace.

## Models

| Model | Type | Active Params | Speed | Replicas | Use Case |
|-------|------|---------------|-------|----------|----------|
| [Gemma 4 26B-A4B](https://huggingface.co/google/gemma-4-26B-A4B-it) | MoE | 3.8B | ~52 tok/s | Always warm | Daily driver, fast coding, tool calling |
| [Qwen 3.5 35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B) | MoE | 3B | ~47 tok/s | Scale from zero | Complex reasoning, heavy coding |

Both models coexist on the GB10's 128GB unified memory. KubeAI scales Qwen from 0 to 1 replica on first request (~60-90s cold start).

## Prerequisites

- NVIDIA DGX Spark with DGX Linux OS
- NVIDIA drivers >= 580.x (pre-installed on DGX Linux OS)
- [HuggingFace token](https://huggingface.co/settings/tokens) with access to gated models

Everything else (K3s, Helm, NVIDIA device plugin, KubeAI, vLLM, LiteLLM) is installed automatically.

## Quick Start

```bash
git clone https://github.com/<your-org>/dgx-spark-llm-stack.git
cd dgx-spark-llm-stack/ansible
./setup.sh
```

On first run, `setup.sh` will:
1. Install Ansible if not present
2. Prompt for a vault password, your HuggingFace token, and a LiteLLM API key (auto-generates one if you press Enter)
3. Encrypt secrets with Ansible Vault
4. Run all 7 Ansible roles in order

When it finishes, test from any machine on your LAN:

```bash
curl http://<spark-ip>:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-litellm-key>" \
  -d '{
    "model": "gemma4",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Usage

```bash
# Re-run (idempotent, safe to repeat)
./setup.sh

# Dry run (preview changes, touch nothing)
./setup.sh --dry-run

# Resume after a failure at the KubeAI step
./setup.sh --tags kubeai,litellm,validate

# Health check only
./setup.sh --tags validate

# Skip Ansible bootstrap (already installed)
./setup.sh --skip-bootstrap
```

### Available Model Names

Use these names in the `model` field of your API requests:

| Name | Routes To |
|------|-----------|
| `gemma4` | Gemma 4 26B-A4B |
| `qwen35` | Qwen 3.5 35B-A3B |
| `gpt-4` | Gemma 4 (compatibility alias for tools that hardcode `gpt-4`) |

### Client Configuration

Any OpenAI-compatible client works. Set the base URL and API key:

```bash
export OPENAI_API_BASE=http://<spark-ip>:4000/v1
export OPENAI_API_KEY=<your-litellm-key>
```

To retrieve your API key later:

```bash
cd ansible && ansible-vault view group_vars/all/vault.yml
```

## Configuration

All settings live in a single file: [`ansible/group_vars/all/vars.yml`](ansible/group_vars/all/vars.yml)

### Key Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `vllm_image` | `nvcr.io/nvidia/vllm:26.01-py3` | vLLM container image (must support SM_121) |
| `gpu_memory_utilization` | `0.7` | GPU memory fraction (keep at 0.7 for unified memory) |
| `litellm_port` | `4000` | LiteLLM proxy port |
| `litellm_service_type` | `LoadBalancer` | K8s service type (LoadBalancer or NodePort) |
| `validate_cold_start_models` | `false` | Test scale-from-zero models during validation |

### Adding a Model

Add an entry under `models:` in `vars.yml`:

```yaml
models:
  my-new-model:
    enabled: true
    owner: org-name
    url: "hf://org-name/model-name"
    features: [TextGeneration]
    min_replicas: 0        # 0 = scale from zero, 1 = always warm
    max_replicas: 1
    extra_args:
      - "--max-model-len=32768"
    litellm_aliases:
      - my-model            # name clients use in API requests
```

Then re-run:

```bash
./setup.sh --tags kubeai,litellm,validate
```

### Secrets

Secrets are encrypted with [Ansible Vault](https://docs.ansible.com/ansible/latest/vault_guide/index.html) in `ansible/group_vars/all/vault.yml`:

| Secret | Purpose |
|--------|---------|
| `hf_token` | HuggingFace access token for downloading gated models |
| `litellm_master_key` | API key for LiteLLM proxy (used by all clients) |

```bash
# View secrets
ansible-vault view group_vars/all/vault.yml

# Edit secrets
ansible-vault edit group_vars/all/vault.yml
```

## Multi-Spark Deployment

Deploy across multiple DGX Sparks from a single control node:

```bash
# Create your inventory
cp ansible/inventory/multi-spark.yml.example ansible/inventory/multi-spark.yml
```

Edit `ansible/inventory/multi-spark.yml`:

```yaml
all:
  hosts:
    spark-01:
      ansible_host: 192.168.1.101
    spark-02:
      ansible_host: 192.168.1.102
  vars:
    ansible_user: dgxuser
    ansible_become: true
```

Deploy:

```bash
./setup.sh -i inventory/multi-spark.yml
```

Per-host overrides go in `ansible/host_vars/<hostname>/vars.yml`. For example, to run only Qwen on `spark-02`:

```yaml
# ansible/host_vars/spark-02/vars.yml
models:
  gemma4-26b-a4b:
    enabled: false
  qwen35-35b-a3b:
    enabled: true
    min_replicas: 1
```

## DGX Spark Hardware Notes

The GB10 has specific requirements that differ from datacenter GPUs:

- **`gpu-memory-utilization=0.7`** -- The GB10 uses unified memory (shared CPU/GPU). Higher values cause OOM kills.
- **`--enforce-eager`** -- Disables CUDA graph capture, required for stability on GB10.
- **Shared memory** -- vLLM pods get a 16Gi `/dev/shm` emptyDir mount for NCCL.
- **SM_121** -- The GB10 is compute capability 12.1, not 10.0. Only vLLM builds compiled for SM_121 work. Check the [vLLM DGX Spark compatibility thread](https://forums.developer.nvidia.com/t/vllm-containers/362721) for tested images.
- **Driver 580.x** -- Driver 590.x has a known CUDAGraph capture deadlock on GB10 unified memory.

## Ansible Roles

The stack is deployed in 7 stages, each an independent Ansible role:

| # | Role | Tag | What It Does |
|---|------|-----|-------------|
| 1 | `preflight` | `preflight` | Validates GPU, driver version, disk space, OS |
| 2 | `prerequisites` | `prerequisites` | Installs Helm, jq, python3, system packages |
| 3 | `k3s` | `k3s` | Installs K3s, configures NVIDIA containerd runtime |
| 4 | `nvidia_plugin` | `nvidia` | Deploys NVIDIA device plugin, verifies GPU visibility |
| 5 | `kubeai` | `kubeai` | Installs KubeAI operator and model catalog |
| 6 | `litellm` | `litellm` | Deploys LiteLLM proxy with templated config |
| 7 | `validation` | `validate` | Runs health checks and chat completion tests |

Run specific roles with `--tags`:

```bash
./setup.sh --tags k3s,nvidia          # Just K3s and NVIDIA plugin
./setup.sh --tags kubeai,litellm      # Just model and proxy layers
./setup.sh --tags validate            # Just health checks
```

The `preflight` role always runs (even with `--tags`) to prevent deploying on incompatible hardware.

## Repo Structure

```
ansible/
  setup.sh                          # Entry point -- bootstrap + run
  site.yml                          # Master playbook
  ansible.cfg                       # Ansible configuration
  requirements.yml                  # Galaxy collection dependencies
  inventory/
    localhost.yml                   # Default: single Spark, local
    multi-spark.yml.example         # Template for fleet deployment
  group_vars/all/
    vars.yml                        # All tunables (models, images, ports)
    vault.yml                       # Encrypted secrets (created by setup.sh)
  host_vars/                        # Per-host overrides
  roles/
    preflight/                      # Hardware validation
    prerequisites/                  # System packages + Helm
    k3s/                            # K3s + NVIDIA runtime
    nvidia_plugin/                  # NVIDIA device plugin
    kubeai/                         # KubeAI operator + models
    litellm/                        # LiteLLM proxy
    validation/                     # Health checks
scripts/legacy/                     # Original bash scripts (reference only)
```

## Troubleshooting

**Preflight fails with driver version error**
```
DGX Spark GB10 requires driver >= 580.x
```
Update NVIDIA drivers via DGX Linux OS package manager. Avoid 590.x (CUDAGraph deadlock).

**Model stuck in pending / not starting**
```bash
kubectl -n llm-stack get pods
kubectl -n llm-stack describe pod <pod-name>
kubectl -n llm-stack logs <pod-name>
```
Common cause: vLLM image doesn't support SM_121. Try a different `vllm_image` in `vars.yml`.

**Qwen 3.5 times out on first request**
Expected behavior -- scale-from-zero takes 60-90 seconds on first request. Set `litellm_timeout: 300` in `vars.yml` (default) to accommodate this.

**LiteLLM returns old model list after config change**
Re-run `./setup.sh --tags litellm,validate`. The playbook automatically restarts the proxy when config changes.

**GPU not visible after install**
```bash
kubectl get nodes -o json | jq '.items[].status.allocatable["nvidia.com/gpu"]'
```
If `null`, the NVIDIA device plugin isn't running or the containerd runtime isn't configured. Re-run `./setup.sh --tags k3s,nvidia`.

## License

Apache-2.0
