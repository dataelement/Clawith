---
description: Restart Clawith frontend and backend services together
---

# Restart Clawith Services

// turbo-all

1. Run the restart script:
```bash
/Users/ray/Documents/antigravity/Clawith/restart.sh
```

## Manual Log Viewing
- Backend: `tail -f /tmp/clawith_backend.log`
- Frontend: `tail -f /tmp/clawith_frontend.log`

## Notes
- Script kills old processes, starts backend + frontend, waits for ports, verifies proxy
- Cpolar (if needed): `/Users/ray/Downloads/cpolar http 8008`
- Set `PUBLIC_BASE_URL` env var to cpolar URL if Feishu is configured´