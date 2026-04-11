# Connecting Visual Studio 2019 to DGX Spark LLM Stack

Use your DGX Spark as a local AI coding assistant in Visual Studio 2019 via the Local LLM Chat extension.

## Prerequisites

- DGX Spark LLM stack running (`./setup.sh` completed successfully)
- Your DGX Spark IP address (e.g., `<SPARK_IP>`)
- Your LiteLLM API key (retrieve with `ansible-vault view group_vars/all/vault.yml`)
- Visual Studio 2019 (version 16.x) with Extension support

> **Note:** Visual Studio 2019 has limited AI extension support compared to VS 2022+.
> The recommended extensions below target VS 2019 compatibility where possible.

## Option 1: Local LLM Chat Extension (Recommended)

### Step 1: Install the Extension

1. Open Visual Studio 2019
2. Go to **Extensions** > **Manage Extensions**
3. Search for **"Local LLM Chat"** by Markus Begerow
4. Click **Download** and restart Visual Studio
5. Alternatively, install from [Visual Studio Marketplace](https://marketplace.visualstudio.com/items?itemName=MarkusBegerow.local-llm-chat-vs)

### Step 2: Configure the Extension

1. Go to **Tools** > **Options** > **Local LLM Chat**
2. Set the following:

| Setting | Value |
|---------|-------|
| **API Endpoint** | `http://<SPARK_IP>:4000/v1/chat/completions` |
| **API Key** | Your LiteLLM key (`sk-dgxspark-...`) |
| **Model** | `gemma4` |

3. Click **OK**

### Step 3: Use the Chat Window

1. Open the chat window: **View** > **Other Windows** > **Local LLM Chat**
2. Type a question or paste code for explanation
3. The response comes from Gemma 4 on your DGX Spark

## Option 2: Visual Studio Local LLM Plugin

### Step 1: Install

1. Go to **Extensions** > **Manage Extensions**
2. Search for **"Visual Studio Local LLM Plugin"**
3. Install and restart Visual Studio

### Step 2: Configure

1. Go to **Tools** > **Options** > **Local LLM Plugin**
2. Select provider: **Custom / OpenAI Compatible**
3. Set:

| Setting | Value |
|---------|-------|
| **API URL** | `http://<SPARK_IP>:4000/v1` |
| **API Key** | Your LiteLLM key (`sk-dgxspark-...`) |
| **Model** | `gemma4` |

4. Click **Test Connection** to verify
5. Click **OK**

## Option 3: REST Client (No Extension Needed)

If extensions aren't available for your VS 2019 edition, you can use the DGX Spark directly via HTTP from your code or a script:

### PowerShell Quick Test

```powershell
$headers = @{
    "Content-Type" = "application/json"
    "Authorization" = "Bearer sk-dgxspark-your-key-here"
}

$body = @{
    model = "gemma4"
    messages = @(
        @{ role = "user"; content = "Explain this C# code: public async Task<T> GetAsync<T>(string url)" }
    )
    max_tokens = 500
} | ConvertTo-Json -Depth 3

$response = Invoke-RestMethod -Uri "http://<SPARK_IP>:4000/v1/chat/completions" `
    -Method Post -Headers $headers -Body $body

$response.choices[0].message.content
```

### C# HTTP Client

```csharp
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using Newtonsoft.Json;

var client = new HttpClient();
client.BaseAddress = new Uri("http://<SPARK_IP>:4000/v1/");
client.DefaultRequestHeaders.Authorization =
    new AuthenticationHeaderValue("Bearer", "sk-dgxspark-your-key-here");

var request = new
{
    model = "gemma4",
    messages = new[] { new { role = "user", content = "Hello from Visual Studio!" } },
    max_tokens = 100
};

var json = JsonConvert.SerializeObject(request);
var content = new StringContent(json, Encoding.UTF8, "application/json");
var response = await client.PostAsync("chat/completions", content);
var result = await response.Content.ReadAsStringAsync();
Console.WriteLine(result);
```

### Using the OpenAI .NET SDK

Install via NuGet: `Install-Package OpenAI`

```csharp
using OpenAI;
using OpenAI.Chat;

// Point to your DGX Spark instead of OpenAI
var options = new OpenAIClientOptions
{
    Endpoint = new Uri("http://<SPARK_IP>:4000/v1")
};

var client = new ChatClient(
    model: "gemma4",
    credential: new ApiKeyCredential("sk-dgxspark-your-key-here"),
    options: options
);

ChatCompletion completion = await client.CompleteChatAsync("Hello from VS 2019!");
Console.WriteLine(completion.Content[0].Text);
```

## Available Models

| Model Name | Best For | Notes |
|------------|----------|-------|
| `gemma4` | All tasks | Fast, always warm, tool calling |
| `qwen35` | Complex reasoning | 60-90s cold start on first use |
| `gpt-4` | Compatibility | Alias for gemma4 |

## Troubleshooting

**Extension not available for VS 2019**
- Some AI extensions require VS 2022+. Use Option 3 (REST Client) as a fallback.
- Consider upgrading to VS 2022 Community (free) for better AI extension support.

**Connection refused**
- Verify the Spark is reachable: `ping <SPARK_IP>`
- Ensure port 4000 is accessible from your Windows machine
- Check Windows Firewall isn't blocking outbound connections to port 4000

**Authentication error**
- Verify the API key: test with curl or PowerShell (see Option 3 above)

**Slow first response**
- Qwen 3.5 scales from zero: first request takes 60-90s
- Gemma 4 should respond within seconds
