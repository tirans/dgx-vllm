# Using DGX Spark LLM Stack with Claude Code

Claude Code uses the Anthropic API protocol natively — it does **not** support
`OPENAI_BASE_URL` or OpenAI-compatible endpoints directly. To use your DGX Spark
models from within Claude Code, use one of these approaches.

## Prerequisites

- DGX Spark LLM stack running (`./setup.sh` completed successfully)
- Your DGX Spark IP address
- Your LiteLLM API key (retrieve with `ansible-vault view group_vars/all/vault.yml`)
- Claude Code installed

## Option 1: claude-code-proxy (Use Spark as primary model)

[claude-code-proxy](https://github.com/fuergaosi233/claude-code-proxy) translates
Anthropic API calls to OpenAI-compatible format, letting Claude Code talk to your Spark.

### Step 1: Install the Proxy

```bash
npm install -g claude-code-proxy
```

### Step 2: Start the Proxy

In one terminal:

```bash
claude-code-proxy \
  --openai-base-url http://<SPARK_IP>:4000/v1 \
  --openai-api-key <YOUR_LITELLM_KEY> \
  --openai-model qwen3
```

### Step 3: Launch Claude Code Through the Proxy

In another terminal:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8080
export ANTHROPIC_API_KEY=dummy
claude
```

Claude Code will now route all requests through your DGX Spark.

### Make It Permanent

Add to your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
# DGX Spark via claude-code-proxy (start the proxy first)
export ANTHROPIC_BASE_URL=http://localhost:8080
export ANTHROPIC_API_KEY=dummy
```

## Option 2: Call Spark from Within Claude Code (Hybrid)

Keep Claude Code on Anthropic's API for its strengths (tool use, agentic coding),
and call the Spark for specific tasks using the `!` shell prefix.

### Quick Query

In the Claude Code prompt, type:

```
! curl -s http://<SPARK_IP>:4000/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer <YOUR_LITELLM_KEY>" -d '{"model":"qwen3","messages":[{"role":"user","content":"Explain this error: segfault at 0x0"}]}' | python3 -c "import json,sys;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

The `!` prefix runs the command in your shell and the output lands in the conversation.

### Save a Helper Script

Create `~/bin/spark-ask`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SPARK_URL="${SPARK_URL:-http://<SPARK_IP>:4000/v1}"
SPARK_KEY="${SPARK_KEY:-<YOUR_LITELLM_KEY>}"
PROMPT="$*"

curl -s "${SPARK_URL}/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${SPARK_KEY}" \
  -d "{\"model\":\"qwen3\",\"messages\":[{\"role\":\"user\",\"content\":$(echo "$PROMPT" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}]}" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

```bash
chmod +x ~/bin/spark-ask
```

Then from Claude Code:

```
! spark-ask "Write a Python function to parse YAML"
```

## Option 3: Use VS Code + Continue Instead

For the best local-LLM coding experience, use the **Continue** extension in VS Code
which has native OpenAI-compatible support with chat, inline edit, and tab autocomplete.

See [docs/clients/vscode.md](vscode.md) for setup instructions.

## Available Models

| Model Name | Description | Notes |
|------------|-------------|-------|
| `qwen3` | Qwen3 30B-A3B (MoE) | Primary model, always warm |
| `gpt-4` | Alias for qwen3 | Compatibility for tools that hardcode gpt-4 |

## Verify Your Spark is Reachable

```bash
# Health check (no auth)
curl http://<SPARK_IP>:4000/health/readiness

# List models
curl -s http://<SPARK_IP>:4000/v1/models -H "Authorization: Bearer <YOUR_LITELLM_KEY>"

# Chat test
curl -s http://<SPARK_IP>:4000/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer <YOUR_LITELLM_KEY>" -d '{"model":"qwen3","messages":[{"role":"user","content":"Hello!"}]}' | python3 -m json.tool
```

## Troubleshooting

**claude-code-proxy: connection refused**
- Ensure the proxy is running in another terminal
- Check the Spark is reachable: `curl http://<SPARK_IP>:4000/health/readiness`

**"model does not exist" in Claude Code**
- Claude Code validates model names against Anthropic's list. Use the proxy approach (Option 1) which bypasses this.

**Authentication error**
- Verify your API key: `ansible-vault view group_vars/all/vault.yml` on the Spark

**Slow first response**
- Model may be loading into GPU memory on first request after pod restart. Wait ~30s.
