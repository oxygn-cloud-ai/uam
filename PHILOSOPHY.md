# uam Philosophy

## Vision

Make any AI model usable from inside Claude Code. The name says it: **Use Any Model**.

## Mission

Provide a reliable, transparent proxy that routes Claude Code's API requests to any backend — Anthropic, RunPod, OpenRouter, or local servers — with zero friction. An open source tool for the Claude Code community.

## Objectives

- Support every major inference backend and local model server
- Translate between Anthropic and OpenAI API formats transparently
- Auto-discover models with zero manual configuration
- Stay invisible to Claude Code — it should never know it's being rerouted

## Ambition

Stay lean. uam is a focused proxy tool that does one thing well. No UI, no dashboard, no cloud service, no analytics platform. If it can't be done with a slash command and a config file, it doesn't belong here.

## Design Principles

**Reliability and flexibility together.** uam must reliably handle any model the user throws at it. If a translation fails or a backend is down, Claude Code must never break — fall back to Anthropic silently.

**Never break Claude Code.** This is the top non-negotiable. The proxy sits in the critical path of every Claude Code request. If the proxy crashes, hangs, or returns garbage, the user's entire coding session is ruined. Every error path must degrade gracefully.

**Never expose API keys.** Config stores environment variable names, never values. Keys never appear in logs, error messages, state files, or git history. No exceptions.

**Zero config for basics.** `pip install` + `/uam setup` and you're running. No manual JSON editing required for the common case. Advanced configuration is available but never mandatory.

**Simplicity over features.** Resist feature creep. A smart developer should be able to read the entire codebase in an afternoon. When in doubt, don't add it.

## Non-Negotiables

1. Claude Code must always work, even if the proxy is broken
2. API keys must never be exposed in any form
3. Basic setup must require zero manual configuration
