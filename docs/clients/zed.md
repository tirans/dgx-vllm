# Connecting Zed Editor to DGX Spark LLM Stack

Use your DGX Spark as an AI assistant provider in Zed.

## Prerequisites

- DGX Spark LLM stack running (`./setup.sh` completed successfully)
- Your DGX Spark IP address (e.g., `<SPARK_IP>`)
- Your LiteLLM API key (retrieve with `ansible-vault view group_vars/all/vault.yml`)
- Zed editor installed (https://zed.dev)

## Step 1: Set API Key Environment Variable

Zed reads API keys from environment variables. Add to your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export DGX_SPARK_API_KEY="sk-dgxspark-your-key-here"
```

Restart your terminal or run `source ~/.zshrc`.

## Step 2: Configure Zed Settings

Open Zed settings: `Cmd+,` (macOS) or `Ctrl+,` (Linux), or run the command palette action `zed: open settings`.

Add the following to your `settings.json`:

```json
{
  "language_models": {
    "openai_compatible": {
      "DGX Spark": {
        "api_url": "http://<SPARK_IP>:4000/v1",
        "available_models": [
          {
            "name": "gemma4",
            "display_name": "Gemma 4 26B-A4B (DGX Spark)",
            "max_tokens": 65536,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false
            }
          },
          {
            "name": "qwen35",
            "display_name": "Qwen 3.5 35B-A3B (DGX Spark)",
            "max_tokens": 32768,
            "capabilities": {
              "tools": false,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false
            }
          },
          {
            "name": "gpt-4",
            "display_name": "Gemma 4 (gpt-4 alias)",
            "max_tokens": 65536,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false
            }
          }
        ]
      }
    }
  }
}
```

## Step 3: Select the Model

1. Open the Assistant Panel (`Cmd+Shift+?` or `Ctrl+Shift+?`)
2. Click the model selector dropdown at the top
3. Select **"Gemma 4 26B-A4B (DGX Spark)"** under the "DGX Spark" provider

## Step 4: Verify

Type a message in the Assistant Panel:

```
What model are you? Reply in one sentence.
```

You should get a response from Gemma 4 running on your DGX Spark.

## Available Models

| Model Name | Context Window | Best For |
|------------|---------------|----------|
| Gemma 4 26B-A4B | 65K tokens | Fast coding, tool calling, daily use |
| Qwen 3.5 35B-A3B | 32K tokens | Complex reasoning (60-90s cold start) |

## Troubleshooting

**"No API key found" error**
- Ensure `DGX_SPARK_API_KEY` is exported in your shell profile
- Restart Zed completely after setting the variable

**Model not appearing in dropdown**
- Check the settings JSON is valid (no trailing commas)
- Restart Zed after editing settings

**Slow or no response**
- Qwen 3.5 scales from zero: first request takes 60-90s
- Gemma 4 should respond within seconds
- Test connectivity: `curl http://<SPARK_IP>:4000/health/readiness`

**Connection error**
- Ensure your machine is on the same LAN as the DGX Spark
- Check firewall: port 4000 must be open on the Spark
