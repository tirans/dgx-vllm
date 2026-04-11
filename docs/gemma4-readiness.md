# Gemma 4 Readiness Check

Gemma 4 (`google/gemma-4-26B-A4B-it`) requires Transformers >= 4.51 (released 2026-04-01).
No pre-built vLLM image for DGX Spark supports it yet. Use this guide to check periodically.

## Quick Check: Test a vLLM Image

Run on the DGX Spark:

```bash
# Replace the image tag with whichever you want to test
IMAGE="scitrera/dgx-spark-vllm:0.14.1-t5"

docker run --rm "$IMAGE" python3 -c "
import transformers
print(f'Transformers version: {transformers.__version__}')
try:
    from transformers import AutoConfig
    cfg = AutoConfig.from_pretrained('google/gemma-4-26B-A4B-it', trust_remote_code=True)
    print(f'Model type: {cfg.model_type}')
    print('PASS: Gemma 4 is supported')
except Exception as e:
    print(f'FAIL: {e}')
"
```

**Expected output when ready:**
```
Transformers version: 4.51.0  (or higher)
Model type: gemma4
PASS: Gemma 4 is supported
```

## Images to Watch

| Image | Check Command |
|-------|--------------|
| NGC official | `docker run --rm nvcr.io/nvidia/vllm:26.04-py3 python3 -c "import transformers; print(transformers.__version__)"` |
| scitrera -t5 | `docker run --rm scitrera/dgx-spark-vllm:0.14.1-t5 python3 -c "import transformers; print(transformers.__version__)"` |
| eugr custom | Build from `github.com/eugr/spark-vllm-docker` (main branch, always latest) |

## When Ready: Re-enable Gemma 4

1. Edit `ansible/group_vars/all/vars.yml`:

```yaml
models:
  gemma4-26b-a4b:
    enabled: true          # was false
    min_replicas: 1        # always warm
    ...
    litellm_aliases:
      - gemma4
      - gpt-4             # move alias back from qwen35

  qwen35-35b-a3b:
    enabled: true
    min_replicas: 0        # scale from zero (secondary)
    ...
    litellm_aliases:
      - qwen35             # remove gpt-4 alias
```

2. Update `vllm_image` to the compatible image.

3. Re-deploy:

```bash
./setup.sh --tags kubeai,litellm,validate
```

## Build Your Own (Fastest Path)

Use [eugr/spark-vllm-docker](https://github.com/eugr/spark-vllm-docker) to build vLLM from main branch (includes latest Transformers):

```bash
git clone https://github.com/eugr/spark-vllm-docker.git
cd spark-vllm-docker
./build-and-copy.sh --solo
```

Then set `vllm_image` to the locally built image tag.
