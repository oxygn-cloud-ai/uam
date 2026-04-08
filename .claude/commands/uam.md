---
name: uam
description: Manage the uam model router proxy (status, start, stop, setup, add-server, uninstall)
argument-hint: "[status|start|stop|refresh|setup|add-server|uninstall]"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

<objective>
Manage the uam model router proxy. Parse $ARGUMENTS to determine which subcommand to run.
</objective>

<context>
- uam proxy runs on http://127.0.0.1:5100
- PID file: ~/.uam/uam.pid
- Config: ~/.uam/config.json
- State: ~/.uam/models.json
- The proxy is started via: python -m uam
- ANTHROPIC_BASE_URL must be set to http://127.0.0.1:5100 for Claude Code to use the proxy
</context>

<process>

Parse `$ARGUMENTS` (case-insensitive, default to "status" if empty):

## /uam status (default)
1. Check if proxy is running: `curl -s --max-time 2 http://127.0.0.1:5100/health`
2. If running, show: status (running/stopped), model count, default model, listen address
3. If not running, say "uam proxy is not running. Use /uam start to start it."

## /uam start
1. Check if already running via health endpoint
2. If already running, say so
3. If not running:
   ```bash
   mkdir -p ~/.uam && nohup python -m uam > ~/.uam/uam.log 2>&1 &
   sleep 3
   ```
4. Verify it started via health check
5. Show model count and default model

## /uam stop
1. Read PID from ~/.uam/uam.pid
2. If PID file exists: `kill $PID` and remove PID file
3. If not: say "uam is not running"
4. Verify it stopped via health check

## /uam refresh
1. Check proxy is running
2. `curl -s -X POST http://127.0.0.1:5100/refresh -H "Authorization: Bearer $(cat ~/.uam/token 2>/dev/null)"`
3. Show updated model count
4. Then show the model list (same as /model)

## /uam add-server

Add a remote local-backend server (Ollama, vLLM, etc.) to ~/.uam/config.json without hand-editing JSON. Re-runnable.

1. Check the proxy is running: `curl -s --max-time 2 http://127.0.0.1:5100/health`
   If not running, tell the user: "uam proxy is not running. Use /uam start to start it."

2. Ask via AskUserQuestion: "What kind of server are you adding?"
   Options:
   - Ollama (default port 11434)
   - vLLM (default port 8000)
   - llama.cpp server (default port 8080)
   - LocalAI (default port 8080)
   - TGI / Text Generation Inference (default port 3000)
   - Aphrodite (default port 2242)
   - TabbyAPI (default port 5000)
   - Other OpenAI-compatible server

3. Ask via AskUserQuestion: "Enter the server address (e.g. 192.168.1.50:11434, http://my-server:11434):"
   - If no scheme, the proxy will prepend `http://`
   - If no port, prepend the default port for the chosen server type before sending
   - If the user enters just an IP/hostname with no port at all, append the default port

4. Probe reachability (best-effort, do not abort on failure):
   ```bash
   curl -s --connect-timeout 5 "$URL/api/tags" >/dev/null 2>&1 && echo reachable || \
   curl -s --connect-timeout 5 "$URL/v1/models" >/dev/null 2>&1 && echo reachable || \
   echo unreachable
   ```
   If unreachable, warn the user but continue — the server may not be running right now.

5. POST to /config/local-servers using the bearer token from `~/.uam/token`:
   ```bash
   TOKEN=$(cat ~/.uam/token 2>/dev/null)
   curl -s -X POST http://127.0.0.1:5100/config/local-servers \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $TOKEN" \
     -d "{\"url\": \"$URL\", \"api_format\": \"openai\"}"
   ```
   - On 4xx, surface the error message from the response body and stop.
   - On 200, the response includes the updated server list — show it.

6. Trigger discovery so the new backend's models become available:
   ```bash
   curl -s -X POST http://127.0.0.1:5100/refresh \
     -H "Authorization: Bearer $TOKEN"
   ```

7. Show summary: "Added {url}. Discovered N total models. Use /model to enable specific models from the new backend."

## /uam setup
This is the one-time installation. Do the following steps:

1. Check if uam Python package is installed: `python3 -c "import uam"`. If not, find the uam repo and run `pip install -e /path/to/uam/repo`

2. Create ~/.uam/config.json if it doesn't exist (use default config from uam.config.default_config)

3. **Configure local model servers.** Ask the user via AskUserQuestion:

   "Do you have any local or network model servers? Select all that apply:"

   Options:
   - Ollama (default port 11434)
   - vLLM (default port 8000)
   - llama.cpp server (default port 8080)
   - LocalAI (default port 8080)
   - TGI / Text Generation Inference (default port 3000)
   - Aphrodite (default port 2242)
   - TabbyAPI (default port 5000)
   - Other OpenAI-compatible server
   - Skip — no local servers

   For each selected server type, ask via AskUserQuestion:
   "Where is your {server_type} server running?"
   Options:
   - This machine (localhost:{default_port}) — no config change needed, already probed by default
   - Remote server — ask for the address

   If "Remote server": ask "Enter the address for your {server_type} server (e.g., 192.168.1.50:{default_port} or http://my-server:{default_port}):"
   
   For remote addresses:
   - If no scheme given, prepend http://
   - If no port given, append the default port for the server type
   - Probe the server to verify it's reachable: `curl -s --connect-timeout 5 {url}/api/tags` or `curl -s --connect-timeout 5 {url}/v1/models`
   - If unreachable, warn the user but still add it (the server may not be running right now)

   After collecting all servers:
   - Read ~/.uam/config.json
   - Add any new remote server URLs to the `local.servers` array (avoid duplicates)
   - Save the updated config
   - Show summary: "Configured N server(s): {list}. They'll be probed each time the proxy starts."

   If "Skip" was selected, move on without changes.

4. Copy slash commands to user-level Claude commands directory:
   ```bash
   mkdir -p ~/.claude/commands
   cp .claude/commands/uam.md ~/.claude/commands/uam.md
   cp .claude/commands/model.md ~/.claude/commands/model.md
   ```

5. Copy hooks to user-level:
   ```bash
   mkdir -p ~/.claude/hooks
   cp .claude/hooks/uam-autostart.py ~/.claude/hooks/uam-autostart.py
   cp .claude/hooks/uam-ask-router.py ~/.claude/hooks/uam-ask-router.py
   chmod +x ~/.claude/hooks/uam-autostart.py ~/.claude/hooks/uam-ask-router.py
   ```

6. Read ~/.claude/settings.json (or create empty {}). Merge in the SessionStart and UserPromptSubmit hooks:
   - SessionStart hook: `python3 "$HOME/.claude/hooks/uam-autostart.py"`
   - UserPromptSubmit hook: `python3 "$HOME/.claude/hooks/uam-ask-router.py"`
   Save the merged settings back.

7. Check if ANTHROPIC_BASE_URL is already in shell profile (~/.zshrc or ~/.bashrc). If not, append:
   ```
   export ANTHROPIC_BASE_URL=http://127.0.0.1:5100
   ```

8. Tell the user:
   - Setup complete
   - Restart your terminal to pick up ANTHROPIC_BASE_URL
   - Then start a new Claude Code session — the proxy will auto-start
   - Use /model to configure which models are enabled and set a default

## /uam uninstall
Ask for confirmation first. Then:

1. Stop the proxy (if running)
2. Remove ~/.claude/commands/uam.md and ~/.claude/commands/model.md
3. Remove ~/.claude/hooks/uam-autostart.py and ~/.claude/hooks/uam-ask-router.py
4. Read ~/.claude/settings.json and remove the uam hooks (SessionStart and UserPromptSubmit entries that reference uam). Save it back.
5. Remove `export ANTHROPIC_BASE_URL=http://127.0.0.1:5100` from shell profile
6. Ask if user wants to remove ~/.uam/ directory (config + state)
7. Run `pip uninstall uam -y`
8. Tell the user: "uam removed. Built-in /model is restored. Restart your terminal and Claude Code."

</process>
