# Contributing to Clawith 🦞

Thanks for your interest in contributing! Whether it's a bug fix, new feature, translation, or documentation improvement — every contribution matters.

## Quick Start

1. **Fork** this repo and clone your fork
2. Set up the dev environment:
   ```bash
   bash setup.sh    # Backend + frontend + database
   bash restart.sh  # Start services → http://localhost:3008
   ```
3. Create a branch: `git checkout -b my-feature`
4. Make your changes
5. Push and open a Pull Request

## What Can I Contribute?

| Area | Examples |
|------|---------|
| 🐛 Bug fixes | UI glitches, API errors, edge cases |
| ✨ Features | New agent skills, tools, UI improvements |
| 🔧 MCP Integrations | New MCP server connectors |
| 🌍 Translations | New languages or improving existing ones |
| 📖 Documentation | README, guides, code comments |
| 🧪 Tests | Unit tests, integration tests |

**New to the project?** Look for issues labeled [`good first issue`](https://github.com/dataelement/Clawith/labels/good%20first%20issue).

## Bug Reports

When reporting a bug, please include:
- Steps to reproduce
- Expected vs actual behavior
- Clawith version and deployment method (Docker / Source)
- Logs or screenshots if available

**Priority guide:**

| Type | Priority |
|------|----------|
| Core functions broken (login, agents, security) | 🔴 Critical |
| Non-critical bugs, performance issues | 🟡 Medium |
| Typos, minor UI issues | 🟢 Low |

## Feature Requests

Please describe:
- The problem you're trying to solve
- Your proposed solution (if any)
- Why this would be useful

## Pull Request Process

1. **Link an issue** — Create one first if it doesn't exist
2. **Keep it focused** — One PR per feature/fix
3. **Test your changes** — Make sure nothing is broken
4. **Follow code style:**
   - Backend: Python — formatted with `ruff`
   - Frontend: TypeScript — standard React conventions
5. Use `Fixes #<issue_number>` in the PR description

## Project Structure

```
backend/
├── app/
│   ├── api/          # FastAPI route handlers
│   ├── models/       # SQLAlchemy models
│   ├── services/     # Business logic
│   └── core/         # Auth, events, middleware
frontend/
├── src/
│   ├── pages/        # Page components
│   ├── components/   # Reusable UI components
│   ├── stores/       # Zustand state management
│   └── i18n/         # Translations
```

## Language Policy

To ensure all contributors can participate effectively, please use **English** for issues, PRs, and code comments.

为了确保所有贡献者都能有效参与，请使用**英语**提交 Issue、PR 和代码注释。

すべてのコントリビューターが効果的に参加できるよう、Issue、PR、コードコメントは**英語**でお願いします。

모든 기여자가 효과적으로 참여할 수 있도록, Issue, PR, 코드 코멘트는 **영어**로 작성해 주세요.

Para garantizar que todos los contribuidores puedan participar de manera efectiva, utilice **inglés** para issues, PRs y comentarios de código.

لضمان مشاركة جميع المساهمين بفعالية، يرجى استخدام **اللغة الإنجليزية** في الـ Issues وطلبات السحب وتعليقات الكود.

## Windows Development

Clawith is primarily developed on Linux/macOS, but can run on Windows with a few adjustments.

### Prerequisites

- **Python 3.11+** — Install from [python.org](https://www.python.org/downloads/) (check "Add to PATH")
- **Node.js 18+** — Install from [nodejs.org](https://nodejs.org/)
- **Docker Desktop** — For PostgreSQL and Redis (recommended over native installs)

### Database & Redis via Docker

```powershell
docker run -d --name clawith-postgres -p 5432:5432 -e POSTGRES_PASSWORD=yourpass -e POSTGRES_DB=clawith postgres:15
docker run -d --name clawith-redis -p 6379:6379 redis:7
```

### Backend Setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Create .env (copy from .env.example and adjust DATABASE_URL / REDIS_URL)
# Run database migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend Setup

```powershell
cd frontend
npm install
npm run dev
```

### Common Windows Issues

| Issue | Solution |
|-------|----------|
| `UnicodeEncodeError` / GBK encoding | Set `PYTHONUTF8=1` in environment variables, or run `chcp 65001` before starting |
| System proxy intercepting LLM API calls | Set `NO_PROXY=*` or unset `HTTP_PROXY` / `HTTPS_PROXY` in your terminal |
| `uvicorn --reload` crashes with watchfiles | Remove `--reload` flag, or install `watchfiles`: `pip install watchfiles` |
| File path errors with backslashes | Use `pathlib.Path` — the codebase already does this in most places |

> **Note**: The recommended deployment method is Docker (`docker compose up -d`), which works identically on Windows, macOS, and Linux. The instructions above are for local development without Docker.

## Getting Help

Stuck? Open a [Discussion](https://github.com/dataelement/Clawith/discussions) or ask in the related issue. We're happy to help! 🙌
