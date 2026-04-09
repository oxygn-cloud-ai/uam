---
name: uam
description: Manage the uam model router proxy (status, start, stop, setup, update, add-server, list-openrouter, uninstall)
argument-hint: "[status|start|stop|refresh|setup|update|add-server|list-openrouter|uninstall]"
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

## /uam list-openrouter [filter]

List all OpenRouter models available through the proxy in a table with pricing, context window, and capabilities.

1. Check the proxy is running:
   ```bash
   curl -s --max-time 2 http://127.0.0.1:5100/health
   ```
   If not running, tell the user: "uam proxy is not running. Use /uam start to start it."

2. Fetch OpenRouter models with metadata:
   ```bash
   curl -s 'http://127.0.0.1:5100/v1/models?backend=openrouter&metadata=true'
   ```

3. Parse the JSON response. Each model in `data[]` has:
   - `id` (e.g. `openrouter:google/gemini-2.0-flash`)
   - `original_model` (e.g. `google/gemini-2.0-flash`)
   - `enabled` (boolean)
   - `metadata.name` (human-readable name)
   - `metadata.context_length` (integer or null)
   - `metadata.pricing_prompt` (cost per token as string, e.g. "0.00001")
   - `metadata.pricing_completion` (cost per token as string)
   - `metadata.modality` (e.g. "text+image->text")

4. If $ARGUMENTS is provided, filter to models whose `original_model` or `metadata.name` contains the filter string (case-insensitive).

5. Format as a table using python3 for clean column alignment:
   ```bash
   curl -s 'http://127.0.0.1:5100/v1/models?backend=openrouter&metadata=true' | python3 -c "
   import json, sys
   data = json.load(sys.stdin)
   models = data.get('data', [])
   # Apply filter if provided
   f = '$ARGUMENTS'.strip().lower()
   if f:
       models = [m for m in models if f in m.get('original_model','').lower() or f in m.get('metadata',{}).get('name','').lower()]
   # Format context length
   def fmt_ctx(n):
       if not n: return '?'
       if n >= 1000000: return f'{n//1000000}M'
       return f'{n//1000}K'
   # Format price per 1M tokens
   def fmt_price(s):
       try:
           p = float(s) * 1000000
           if p == 0: return 'free'
           if p < 0.01: return f'\${p:.4f}'
           return f'\${p:.2f}'
       except: return '?'
   print(f'OpenRouter Models ({len(models)} shown)\n')
   print(f'{\"Model\":<45} {\"Context\":>8} {\"In/1M\":>10} {\"Out/1M\":>10} {\"Modality\":<20}')
   print('-' * 95)
   for m in sorted(models, key=lambda x: x.get('original_model','')):
       md = m.get('metadata', {})
       name = m.get('original_model', m['id'])
       ctx = fmt_ctx(md.get('context_length'))
       pin = fmt_price(md.get('pricing_prompt', '0'))
       pout = fmt_price(md.get('pricing_completion', '0'))
       mod = md.get('modality', '')
       en = '[x]' if m.get('enabled') else '[ ]'
       print(f'{en} {name:<42} {ctx:>8} {pin:>10} {pout:>10} {mod:<20}')
   "
   ```

6. If there are more than 50 models and no filter was provided, ask via AskUserQuestion:
   "There are N OpenRouter models. Would you like to filter by keyword?"
   Options:
   - Show all
   - Filter by keyword (then ask for the keyword)

## /uam update

Update uam to the latest version from the repo. Pulls new code, copies updated slash commands and hooks, and restarts the proxy.

1. Find the uam repo directory:
   ```bash
   python3 -c "import uam; import pathlib; print(pathlib.Path(uam.__file__).resolve().parent.parent.parent)"
   ```
   Store the result as `$UAM_REPO`.

2. Show the current version:
   ```bash
   python3 -c "import uam; print(f'Current: v{uam.__version__}')"
   ```

3. Pull latest changes:
   ```bash
   git -C "$UAM_REPO" pull --ff-only 2>&1
   ```
   If this fails (e.g., local changes), show the error and stop. Do not force-pull.

4. Show the new version and recent changelog:
   ```bash
   python3 -c "
   # Re-import to pick up new version
   import importlib, uam
   importlib.reload(uam)
   print(f'Updated: v{uam.__version__}')
   "
   git -C "$UAM_REPO" log --oneline -5
   ```

5. Copy updated slash commands and hooks to user-level directories:
   ```bash
   cp "$UAM_REPO/.claude/commands/uam.md" ~/.claude/commands/uam.md
   cp "$UAM_REPO/.claude/commands/model.md" ~/.claude/commands/model.md
   # Copy hooks if they exist
   if [ -d "$UAM_REPO/.claude/hooks" ]; then
     cp "$UAM_REPO/.claude/hooks/"*.py ~/.claude/hooks/ 2>/dev/null
     chmod +x ~/.claude/hooks/uam-*.py 2>/dev/null
   fi
   ```

6. Restart the proxy if it was running:
   ```bash
   WAS_RUNNING=$(curl -s --max-time 2 http://127.0.0.1:5100/health 2>/dev/null && echo yes || echo no)
   if [ "$WAS_RUNNING" = "yes" ]; then
     PID=$(cat ~/.uam/uam.pid 2>/dev/null)
     [ -n "$PID" ] && kill "$PID" 2>/dev/null
     # Also check by port in case PID file is stale
     PPID=$(lsof -i :5100 -t 2>/dev/null)
     [ -n "$PPID" ] && kill "$PPID" 2>/dev/null
     rm -f ~/.uam/uam.pid
     sleep 1
     nohup python3 -m uam > ~/.uam/uam.log 2>&1 &
     sleep 3
     curl -s --max-time 5 http://127.0.0.1:5100/health
   fi
   ```

7. Show summary:
   ```
   uam updated to v{version}.
   - Slash commands: updated
   - Hooks: updated
   - Proxy: restarted (if was running)
   
   Start a new Claude Code session to pick up the updated slash commands.
   ```

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
