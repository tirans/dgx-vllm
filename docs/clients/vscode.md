# Connecting VS Code to DGX Spark LLM Stack

Use your DGX Spark as a local AI coding assistant in VS Code via the Continue extension.

## Prerequisites

- DGX Spark LLM stack running (`./setup.sh` completed successfully)
- Your DGX Spark IP address (e.g., `<SPARK_IP>`)
- Your LiteLLM API key (retrieve with `ansible-vault view group_vars/all/vault.yml`)
- VS Code installed

## Step 1: Install Continue Extension

1. Open VS Code
2. Press `Ctrl+Shift+X` to open the Extensions panel
3. Search for **"Continue"** by Continue.dev
4. Click **Install**
5. Reload VS Code when prompted

## Step 2: Open Continue Configuration

1. Click the Continue icon in the sidebar (or press `Ctrl+L` to open the chat panel)
2. Click the **gear icon** at the bottom of the Continue panel
3. This opens `~/.continue/config.json`

Alternatively, open the file directly:

```bash
# macOS / Linux
code ~/.continue/config.json

# Windows
code %USERPROFILE%\.continue\config.json
```

## Step 3: Configure the DGX Spark Provider

Replace or merge into your `config.json`:

```json
{
  "models": [
    {
      "title": "Gemma 4 26B-A4B (DGX Spark)",
      "provider": "openai",
      "model": "gemma4",
      "apiBase": "http://<SPARK_IP>:4000/v1",
      "apiKey": "sk-dgxspark-your-key-here"
    },
    {
      "title": "Qwen 3.5 35B-A3B (DGX Spark)",
      "provider": "openai",
      "model": "qwen35",
      "apiBase": "http://<SPARK_IP>:4000/v1",
      "apiKey": "sk-dgxspark-your-key-here"
    }
  ],
  "tabAutocompleteModel": {
    "title": "Gemma 4 Autocomplete",
    "provider": "openai",
    "model": "gemma4",
    "apiBase": "http://<SPARK_IP>:4000/v1",
    "apiKey": "sk-dgxspark-your-key-here"
  }
}
```

## Step 4: Select the Model

1. Open the Continue chat panel (`Ctrl+L`)
2. Click the model dropdown at the top of the panel
3. Select **"Gemma 4 26B-A4B (DGX Spark)"**

## Step 5: Verify

Type in the Continue chat panel:

```
What model are you? One sentence.
```

You should see a response from Gemma 4 running on your DGX Spark.

## Using Continue Features

### Chat (`Ctrl+L`)
Ask questions about your code, get explanations, or request changes.

### Inline Edit (`Ctrl+I`)
Select code, press `Ctrl+I`, describe the change. Continue edits inline.

### Tab Autocomplete
Start typing — Continue provides AI-powered completions using Gemma 4.

### Slash Commands
In the chat panel:
- `/edit` — Edit selected code
- `/comment` — Add comments to code
- `/test` — Generate tests
- `/explain` — Explain selected code

### Context Providers
Reference files and code in your prompts:
- `@file` — Reference a specific file
- `@code` — Reference a code block
- `@terminal` — Reference terminal output

## Available Models

| Model Name | Context Window | Best For |
|------------|---------------|----------|
| `gemma4` | 65K tokens | Chat, autocomplete, inline edit |
| `qwen35` | 32K tokens | Complex reasoning (60-90s cold start) |

## Advanced: Dual Model Setup

Use Gemma 4 for fast autocomplete and Qwen 3.5 for complex chat:

```json
{
  "models": [
    {
      "title": "Qwen 3.5 (Complex Tasks)",
      "provider": "openai",
      "model": "qwen35",
      "apiBase": "http://<SPARK_IP>:4000/v1",
      "apiKey": "sk-dgxspark-your-key-here"
    }
  ],
  "tabAutocompleteModel": {
    "title": "Gemma 4 (Fast Autocomplete)",
    "provider": "openai",
    "model": "gemma4",
    "apiBase": "http://<SPARK_IP>:4000/v1",
    "apiKey": "sk-dgxspark-your-key-here"
  }
}
```

## Troubleshooting

**"Could not connect to server" error**
- Verify the Spark is reachable: `ping <SPARK_IP>`
- Ensure the URL includes `/v1`: `http://<SPARK_IP>:4000/v1`
- Check the LiteLLM service: `curl http://<SPARK_IP>:4000/health/readiness`

**No autocomplete suggestions**
- Ensure `tabAutocompleteModel` is configured in `config.json`
- Check that the model name matches exactly (`gemma4`, not `Gemma4`)
- Restart VS Code after editing config

**Slow first response**
- Qwen 3.5 scales from zero: first request takes 60-90s
- Gemma 4 should respond within seconds

**Authentication error**
- Verify the API key in `config.json` matches: `ansible-vault view group_vars/all/vault.yml` on the Spark
- Keys are strings: make sure quotes are correct in the JSON
