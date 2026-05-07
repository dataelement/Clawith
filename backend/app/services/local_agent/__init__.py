"""Local agent bridge subsystem.

Replaces OpenClaw gateway polling with a reverse-connected WebSocket
session channel. User's local `clawith-bridge` dials in and streams
session events (from Claude Code, Hermes, OpenClaw, ...) to Clawith
in real time.

See plan: clawith-5min-agent-session-agent-clawit-calm-yeti.md
"""
