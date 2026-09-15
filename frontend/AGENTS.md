# AGENTS.md — Clawith Frontend

These frontend-specific rules apply to `frontend/**` and supplement the repository-wide [conventions](../AGENTS.md#2-conventions).

The Frontend is a React 19 and TypeScript web application built with Vite. It provides the user-facing interfaces for configuring, operating, and observing agents. It consumes Backend and Runtime contracts but does not own execution lifecycle or security decisions.

Project scripts and dependencies are defined in `package.json`; `package-lock.json` records the resolved dependency graph. Application source lives in `src/`, Frontend tests live in `tests/`, static assets live in `public/`, and `dist/` is generated build output.

## Commands

Run Frontend commands from `frontend/`:

| Action                               | Command                                  |
| ------------------------------------ | ---------------------------------------- |
| Install locked dependencies          | `npm ci`                                 |
| Run the development server           | `npm run dev`                            |
| Run a focused test file              | `node --test tests/<test_file>.test.mjs` |
| Run the complete Frontend test suite | `npm test`                               |
| Run static type checks               | `npx tsc --noEmit`                       |
| Run lint checks                      | `npm run lint`                           |
| Check formatting                     | `npm run format:check`                   |
| Format supported files               | `npm run format`                         |
| Build the production bundle          | `npm run build`                          |
| Preview shadcn components            | `npm run dev:ui`                         |
| Build the isolated component preview | `npm run build:ui`                       |
| Add a shadcn component               | `npm run ui:add -- <component>`          |

Use focused tests during development. Use the repository testing policy as the authority for when the complete Frontend suite and production build are required.

## Application layout

```text
package.json       Project scripts and dependency declarations.
package-lock.json  Locked npm dependency graph.
index.html         Vite HTML entry document.
vite.config.ts     Development-server and production-build configuration.
tsconfig.json      TypeScript project and strictness configuration.
eslint.config.js   Frontend lint configuration.
public/            Static files copied into the built application.
tests/             Frontend contract, behavior, and regression tests.
src/main.tsx       React application bootstrap.
src/App.tsx        Top-level providers and route composition.
src/pages/         Route-level product screens and feature composition.
src/components/    Reusable presentation and interaction components.
src/hooks/         Shared React hooks.
src/services/      Backend API and external-service client boundaries.
src/stores/        Shared client-side state stores.
src/types/         Shared TypeScript types.
src/i18n/          Localization setup and resources.
src/styles/        Shared style and theme definitions.
src/utils/         Shared pure helpers.
src/assets/        Source-controlled assets imported by the application.
```

Detailed feature structure belongs to the nearest path-specific instruction or owning architecture document, not this file.

## UI foundation

`components.json` configures source-owned shadcn/ui components in `src/components/ui/`, the `cn()` helper in `src/lib/utils.ts`, and the neutral light/dark theme in `src/styles/ui.css`. Keep the configured Radix foundation and Tabler icon library when adding components. `ui.css` scans only the new component and preview directories; register another new-UI source directory there when it gains a consumer.

`ui.html` and `src/ui-preview/main.tsx` provide an isolated preview for component work. They load Tailwind Preflight and the new theme; the legacy application entry does not. `build:ui` writes `dist-ui/`, while the default build continues to produce only the application in `dist/`. Do not import the new global stylesheet into legacy pages before the clean UI cutover. The [UI foundation Note](../.agents/notes/proposed/architecture/2026-09-01-frontend-shadcn-ui-foundation.md) owns the rewrite boundary and verification requirements.

## State ownership

Each Frontend fact has one state owner. Do not dual-write the same committed business fact into React Query, Zustand, component state, and browser storage.

- Remote Backend data belongs to the React Query cache. Mutations update or invalidate the owning query.
- Cross-route or remount-surviving client interaction state belongs to an owning Zustand store.
- State used only by one mounted component or feature subtree remains local React state.
- A user-editable draft may have local state because it is not yet the committed server fact. Define how the draft initializes, saves, resets, and responds to a server refresh.
- Durable browser preferences and credentials are accessed through their owning store or utility. Do not scatter independent `localStorage` or `sessionStorage` reads and writes across components.
- Backend, Runtime, WebSocket, SSE, and shared-event subscriptions belong to an owning service or feature hook. Business components consume its values and callbacks rather than opening a second subscription. The owner handles reconnection, ordering, deduplication, cancellation, and cleanup.
- Realtime events update or invalidate the same owner used by ordinary reads; they do not create a second realtime-only representation.

Derived display values remain pure computations over their authoritative state; do not persist or subscribe to another independently updated copy.

## Component and data-access boundaries

Components render product state and coordinate user interaction. Backend access, authentication headers, endpoint construction, transport errors, and response parsing belong to `src/services/` or an owning feature hook; do not add raw `fetch()` calls or direct credential reads to business components.

React Query hooks own remote reads, mutations, cache keys, invalidation, and loading/error state. Service functions return typed application values or a documented application error, not raw `Response` objects that force each consumer to reinterpret the transport contract.

Pass components the values and callbacks they need. Do not pass an entire service, store, Runtime object, or transport client merely to avoid defining the component contract.

## Feature and presentation boundaries

Route pages and feature-level containers own product-flow orchestration. Reusable presentation components receive typed values, display state, and callbacks through explicit props; they do not fetch data, interpret Backend or Runtime lifecycle, mutate shared stores directly, or coordinate unrelated features.

Keep feature-specific components, hooks, services, types, and utilities close to their owning feature. Move code into a shared directory only after a current second consumer proves the shared contract. Do not create generic components or hooks for hypothetical reuse.

When a page becomes large, split it by owned responsibility and data flow, not by arbitrary line ranges or visual fragments that still require the parent to pass its entire state.

## Testing

Prefer behavior tests that execute the owning service, reducer, state transition, or utility and assert its public result. Use source-text contract tests only for narrow static constraints that cannot yet be exercised through the current harness; do not use regex matches as evidence that a component renders correctly or that a user journey works.

Each test asserts the layer it owns:

- Service and state tests cover data transformation, ordering, deduplication, cache updates, error normalization, and lifecycle transitions.
- Component or browser validation covers rendered content, interaction, focus, scrolling, responsive layout, and navigation.
- `npm run build` proves TypeScript compilation and production bundling, not user-visible behavior.

When a change affects browser-only behavior that the automated harness cannot exercise, validate it in a real browser and report the automation gap. Do not describe a source-contract match or successful build as browser acceptance.

Run the focused owning test during development. Add `npm run lint`, `npm run format:check`, and `npx tsc --noEmit` for changed Frontend code; add the production build when the change affects application composition, routing, assets, styles, build configuration, or assembled user-visible behavior.

## TypeScript contracts

Keep `strict` TypeScript and `noImplicitAny` enabled. New and changed component props, hook results, service inputs and outputs, store state, events, and Backend response models use explicit types.

Treat external JSON, browser messages, storage values, and third-party payloads as `unknown` until the owning boundary validates or narrows them. Do not use `any`, broad index signatures, unchecked casts, non-null assertions, or optional fields merely to silence a mismatch. When an exception is unavoidable, keep it at the narrowest boundary and explain why the precise type is unavailable.

Define a shared type at the layer that owns the contract. Components and feature consumers import that type instead of recreating local variants of the same Backend, Runtime, or state shape.

Handle closed state and event unions exhaustively. Extensible external inputs must define explicit unknown-value behavior rather than falling through an accidental default.

## Runtime and mutation presentation

Render Backend and Runtime states according to their documented contracts. Do not infer completion, success, permission, delivery, or recoverability from an HTTP success, request acceptance, missing error, assistant text, local timer, or optimistic UI state.

Keep accepted, queued, running, waiting, completed, failed, cancelled, synchronized, and delivered outcomes distinct when the Backend contract distinguishes them. A mutation invalidates or updates the owning React Query state only from its documented committed result.

Use optimistic UI only for reversible presentation or interaction state with a defined rollback. Do not optimistically publish irreversible external effects, Runtime completion, permission changes, or durable business results.

Preserve canonical Backend error identity, safe message, code, trace, Run, and retryability fields when available. Do not classify errors by matching English message fragments.
