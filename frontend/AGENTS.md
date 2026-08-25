# Frontend AGENTS.md — Clawith Frontend Guidelines

---

## 1. Subsystem Overview

**Stack**: React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui.
**Root Spec**: Extended from root [`AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/AGENTS.md).

---

## 2. Common Commands

From `frontend/` directory:

| Action | Command |
|---|---|
| Run Dev Server | `npm run dev` |
| Type Check | `npx tsc --noEmit` |
| Run Linter | `npm run lint` |
| Build Production Bundle | `npm run build` |

---

## 3. Frontend Hard Rules (P0)

- **TypeScript Only**: Functional components only. Class components are strictly prohibited.
- **Single File Line Limit**: File length MUST NOT exceed 600 lines. Split into sub-components or custom hooks when approaching limit.
- **Interface vs Type**: Use `interface` for component Props and public API structures; use `type` for internal unions/tuples.
- **Naming Conventions**:
  - Components: `PascalCase`
  - Utilities & Hooks: `camelCase` (hooks MUST start with `use`)
  - Event Handlers: Internal handler functions `handle<Event>` (e.g., `handleSubmit`), prop callbacks `on<Event>` (e.g., `onSubmit`).
- **Export Style**: Named exports ONLY (`export function ComponentName`). Default exports (`export default`) are forbidden.
- **HTTP Client Wrapper (C4)**: NEVER `import axios` directly in UI components or pages. Always use the unified request module (`src/api/request.ts`).
- **No Unexplained `any`**: Avoid `any`. If unavoidable due to external library constraints, append `// eslint-disable-next-line @typescript-scope` with a explicit reason on the preceding line.
- **Comment Language**: Write all code comments in clear English.

---

## 4. UI & Aesthetics Guidelines

- **Design System**: Use Tailwind CSS and shadcn/ui components for consistent design tokens.
- **Responsive Layout**: Ensure layouts adapt gracefully to desktop and mobile viewports.
- **Micro-Interactions**: Use smooth CSS transitions and hover states for interactive elements.
