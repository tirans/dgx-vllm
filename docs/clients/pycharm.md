# Connecting PyCharm to DGX Spark LLM Stack

Use your DGX Spark as an AI assistant in PyCharm via the JetBrains AI Assistant plugin.

## Prerequisites

- DGX Spark LLM stack running (`./setup.sh` completed successfully)
- Your DGX Spark IP address (e.g., `<SPARK_IP>`)
- Your LiteLLM API key (retrieve with `ansible-vault view group_vars/all/vault.yml`)
- PyCharm 2025.1+ (AI Assistant supports custom OpenAI-compatible endpoints since Jan 2025)

## Step 1: Install AI Assistant Plugin

1. Open **Settings** (`Ctrl+Alt+S`)
2. Go to **Plugins** > **Marketplace**
3. Search for **"AI Assistant"**
4. Click **Install** and restart PyCharm

## Step 2: Configure Custom Provider

1. Open **Settings** (`Ctrl+Alt+S`)
2. Navigate to **Tools** > **AI Assistant** > **Providers & API keys**
3. Click **"+ Add Provider"**
4. Select **"OpenAI API Compatible"**
5. Fill in:

| Field | Value |
|-------|-------|
| **Name** | DGX Spark |
| **API URL** | `http://<SPARK_IP>:4000/v1` |
| **API Key** | Your LiteLLM key (`sk-dgxspark-...`) |

6. Click **Test Connection** to verify
7. Click **OK**

## Step 3: Add Models

Still in **Tools** > **AI Assistant** > **Providers & API keys**:

1. Under your "DGX Spark" provider, click **"+ Add Model"**
2. Enter model ID: `gemma4`
3. Set display name: `Gemma 4 26B-A4B`
4. Repeat for `qwen35` (display name: `Qwen 3.5 35B-A3B`)

## Step 4: Assign Models to Features

In **Tools** > **AI Assistant** > **Models Assignment**:

| Feature | Recommended Model |
|---------|-------------------|
| **Core features** (chat, explain, refactor) | Gemma 4 26B-A4B |
| **Lightweight tasks** (commit messages, naming) | Gemma 4 26B-A4B |
| **Code completion** | Gemma 4 26B-A4B |

## Step 5: Use the AI Assistant

### Chat
- Open the AI Assistant tool window: **View** > **Tool Windows** > **AI Assistant**
- Or press `Alt+Enter` on selected code and choose an AI action

### Inline Actions
- Select code, right-click > **AI Actions** > **Explain Code** / **Refactor** / **Generate Tests**
- Or use `Alt+Enter` on any selection

### Code Completion
- AI-powered completions appear automatically as you type (if assigned in Step 4)

## Available Models

| Model Name | Best For | Notes |
|------------|----------|-------|
| `gemma4` | All features | Fast, supports tool calling |
| `qwen35` | Complex refactoring | 60-90s cold start on first use |
| `gpt-4` | Compatibility | Alias for gemma4 |

## Troubleshooting

**"Connection failed" in Test Connection**
- Verify the Spark is reachable: `ping <SPARK_IP>`
- Check port 4000 is open: `curl http://<SPARK_IP>:4000/health/readiness`
- Ensure the URL ends with `/v1` (not `/v1/`)

**"Model not found" error**
- Verify model names match exactly: `gemma4`, `qwen35`, or `gpt-4`
- List available models: `curl http://<SPARK_IP>:4000/v1/models -H "Authorization: Bearer YOUR_KEY"`

**Slow responses**
- Qwen 3.5 scales from zero: 60-90s on first request
- If Gemma 4 is slow, the vLLM pod may be loading the model into GPU memory (first request after pod restart)

**AI Assistant not available**
- Requires PyCharm 2025.1 or later
- The AI Assistant plugin must be installed and enabled
- You need a JetBrains AI subscription OR use the "Custom Providers" feature (available in Professional edition)
