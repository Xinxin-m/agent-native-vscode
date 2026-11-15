---
name: chat-dashboard
description: Install and operate this user's private local Library Workspace.
---

Read {{TOOL_ROOT}}/README.md and {{TOOL_ROOT}}/docs/AGENT-ONBOARDING.md.
Preserve existing private configuration, transcripts and curated projects.
Use Python 3.11+ and parse-only indexing for installation. Automatic model labeling requires explicit user opt-in.
Do not send paid chat messages, stop sessions, delete transcripts, or expose the local server publicly during installation QA.
Use native provider profile IDs for account routing; never substitute provider UUIDs or infer account ownership from shared transcript directories.
Before restarting a running service, inspect /api/runs and data/execution/ for active work.
