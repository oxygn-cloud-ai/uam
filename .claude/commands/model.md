---
name: model
description: List and manage models — toggle on/off, set default, refresh discovery
argument-hint: "[refresh]"
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

<objective>
Show discovered models with on/off status and let the user manage them.
This command shadows Claude Code's built-in /model. On /uam uninstall, this file is deleted and the built-in /model is restored.
</objective>

<context>
- uam proxy: http://127.0.0.1:5100
- Model state: ~/.uam/models.json
- GET /v1/models returns all models with enabled status
- GET /state returns full state (default, aliases, models)
- POST /state updates state (accepts partial updates)
</context>

<process>

First, check if the proxy is running:
```bash
curl -s --max-time 2 http://127.0.0.1:5100/health
```
If not running, tell the user: "uam proxy is not running. Use /uam start to start it."

## /model (no args) — List and manage models

1. Fetch current state:
   ```bash
   curl -s http://127.0.0.1:5100/state
   ```

2. Fetch model list:
   ```bash
   curl -s http://127.0.0.1:5100/v1/models
   ```

3. Display models in a clear table format, grouped by backend:
   ```
   Default: claude-sonnet-4-6

   Anthropic:
     [x] claude-sonnet-4-6          (alias: claude)
     [x] claude-opus-4-6            (alias: opus)
     [ ] claude-haiku-4-5-20251001  (alias: haiku)

   OpenRouter:
     [x] openrouter:google/gemini-2.0-flash  (alias: gemini)
     [ ] openrouter:meta-llama/llama-3.1-70b (alias: llama)

   Local:
     [x] local:qwen2.5-coder        (alias: qwen)
   ```
   Where [x] = enabled, [ ] = disabled

4. Ask the user using AskUserQuestion: "Which models would you like to toggle on/off?" with options for each model that can be toggled, plus "Set default model" and "Done — no changes".

5. If the user wants to toggle models:
   - Build the update payload with the toggled models
   - POST to /state with the changes:
     ```bash
     curl -s -X POST http://127.0.0.1:5100/state -H "Content-Type: application/json" -d '{"models": {"model-id": {"enabled": true/false}}}'
     ```

6. If the user wants to set a default:
   - Show only enabled models as options using AskUserQuestion
   - POST the new default:
     ```bash
     curl -s -X POST http://127.0.0.1:5100/state -H "Content-Type: application/json" -d '{"default": "selected-model-id"}'
     ```

7. After any changes, show the updated model list again.

## /model refresh

1. Trigger re-discovery:
   ```bash
   curl -s -X POST http://127.0.0.1:5100/refresh
   ```

2. Then run the same flow as /model (list all models with state).

</process>

<success_criteria>
- [ ] Models are displayed clearly with on/off state and aliases
- [ ] User can toggle models on/off
- [ ] User can set a default model
- [ ] Changes are persisted to ~/.uam/models.json via the proxy API
- [ ] /model refresh re-discovers then shows the updated list
</success_criteria>
