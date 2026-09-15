# Agent Note: Frontend shadcn/ui Foundation

Status: proposed — the isolated shadcn/ui foundation is initialized; the application rewrite and clean cutover remain unimplemented

## Problem

The current Frontend is already a React 19 and TypeScript application built with Vite. React Router owns route composition, React Query owns remote server state, Zustand owns shared client state, Tabler supplies icons, and Recharts supplies charts. Replacing those working boundaries would add unrelated migration work.

The common UI layer is different. Dialogs, toasts, modals, buttons, inputs, tabs, tables, sidebars, focus behavior, styling, and theme values are implemented through multiple custom components, page-local markup, inline styles, and one large global stylesheet. Reusing those presentation contracts in a new design would preserve inconsistent interaction and accessibility behavior and require a permanent compatibility layer between two UI systems.

The Backend API and Runtime contracts are also being redesigned. Rebuilding pages against unstable APIs would mix Backend contract churn with visual and interaction work, and a passing Backend test, Frontend build, or source inspection would not prove the rewritten browser experience.

## Proposal

### Keep the valid application foundation

The rewrite keeps React 19, TypeScript, Vite, React Router, React Query, Zustand, Tabler Icons, and Recharts. The state and data boundaries in [the Frontend instructions](../../../../frontend/AGENTS.md) remain authoritative: React Query owns remote Backend data, Zustand owns cross-route client interaction state, and route or feature components own product-flow composition.

Only business state, service, API, routing, localization, and specialized visualization logic whose contracts remain valid may carry forward. Existing presentation markup, DOM structure, class names, generic component props, and CSS are not compatibility contracts.

### Establish one source-owned UI foundation

The rewritten Frontend adopts Tailwind CSS v4 and source-owned shadcn/ui components. Generated or copied shadcn/ui component source lives with the Frontend and is reviewed, tested, and maintained as repository code. Common interaction primitives use shadcn/ui and its Radix foundations where applicable, including dialog, alert dialog, sheet, toast, button, input, field, tabs, table, select, menu, popover, and tooltip behavior.

The theme uses semantic CSS variables such as background, foreground, primary, muted, border, and ring rather than page-specific color constants. Light and dark themes resolve the same semantic tokens. Tailwind utilities and shadcn/ui variants consume those tokens; business pages do not introduce another generic theme vocabulary.

Tailwind Preflight is enabled globally for the rewritten application at the clean cutover. It is not introduced while legacy global CSS remains authoritative. The rewrite removes or restyles every affected legacy selector and audits custom and third-party-rendered content against the reset before acceptance. This avoids carrying a Preflight opt-out or scoped reset as another permanent styling mode.

### Rewrite pages without a compatibility layer

Each target page is rebuilt directly on the new foundation. The rewrite does not preserve or wrap the old generic Dialog, Toast, Modal, Button, Input, Tabs, Table, or Sidebar APIs, and it does not preserve their DOM or class contracts. When a target page is rebuilt, its old generic presentation components and styles are removed rather than adapted. Development may be sequenced by page, but the Frontend does not cut over until the old generic UI system is gone; the shipped target has one common UI foundation, not old and new component systems in parallel.

Business-specific visualizations may remain custom when shadcn/ui has no equivalent responsibility. Recharts remains the charting boundary. Atlas may retain its distinct visual language and specialized components, but any common control or overlay inside those experiences uses the shared shadcn/ui or Radix interaction and accessibility behavior where applicable. Custom visuals must still tolerate global Preflight and meet the same theme, keyboard, focus, responsive, and performance acceptance contract.

### Initialize and inspect components independently

`frontend/components.json` selects the New York style, neutral CSS variables, Radix components, and Tabler icons. `frontend/src/styles/ui.css` contains the shared theme and global Tailwind Preflight for the new UI. Tailwind source detection is restricted to `src/components/ui/` and `src/ui-preview/`; legacy pages and standalone HTML prototypes are not scanned for utilities. Source-owned components live in `frontend/src/components/ui/`; `frontend/src/lib/utils.ts` owns class merging. The initial Button is copied from the verified official source snapshot recorded in `frontend/THIRD_PARTY_NOTICES.md`; the registry download failed with a TLS error. Future additions use `npm run ui:add -- <component>` when the registry is reachable. Button labels use `font-normal` (400), and button corners use `rounded-sm` (6 px with the current theme). These local presentation choices retain the upstream variants, sizing, disabled state, focus behavior, and Slot composition.

`frontend/ui.html` and `frontend/src/ui-preview/main.tsx` are a separate document entry for component inspection. This document imports the new stylesheet and does not import legacy application CSS, authentication, stores, or API services. `npm run dev:ui` opens the preview; `npm run build:ui` builds it into `frontend/dist-ui/`. The default application build retains `frontend/index.html` and does not ship the component preview. This is a development surface, not an application UI mode or a compatibility layer; remove it when a replacement component development surface owns the same responsibility.

### Input and field composition

`Input`, `Field`, `Label`, and `Separator` are source-owned shadcn components from the same verified snapshot. Input and Label use regular weight; Input uses `rounded-sm` to match the 6 px Button radius. Input retains its 36 px default height and upstream responsive type sizing. Field keeps labels, descriptions, and errors composable rather than introducing a second form API. Consumers explicitly connect `htmlFor`, `id`, `aria-describedby`, and `aria-invalid`; field presentation does not decide business validity.

`src/ui-preview/InputPreview.tsx` demonstrates empty, populated, invalid, disabled, and read-only fields plus a local name/email form. Validation runs on submit and focuses the first invalid input; editing clears that field's displayed error, and reset clears feedback. The example stores no server data and makes no request.

### Sequence Backend and Frontend evidence

Component implementation and mock-driven design previews may precede Backend API work. A business page integrates real services only after the Backend API contracts it consumes stabilize. Stabilization means the owning Backend contract and its Backend verification are complete enough for the Frontend service boundary to consume; it does not mean the Frontend behavior is accepted.

Frontend acceptance is collected separately. Type checking and a production build prove compilation and bundling. Browser tests prove rendered behavior and user journeys. Accessibility tests prove semantics, keyboard operation, focus management, and assistive-technology-relevant states. Visual tests prove the supported themes and viewport layouts. Performance measurements prove the applicable responsiveness targets in [Capacity, Performance, and Responsiveness](2026-08-28-capacity-performance-and-responsiveness.md). Backend, source, build, browser, deployment, and live-system evidence remain distinct.

## Alternatives considered

### Incrementally wrap the current generic components

An adapter could preserve the old Dialog, Toast, Modal, Button, Input, Tabs, Table, and Sidebar APIs while rendering shadcn/ui underneath. It would make local migrations smaller, but it would also preserve old prop, DOM, class, and behavioral contracts and create a dual UI system with an unclear removal point. The accepted clean rewrite removes those contracts instead.

### Add Tailwind utilities while retaining the custom component system

Tailwind alone could reduce some handwritten CSS without changing the component model. It would not consolidate focus management, keyboard interaction, overlay composition, accessibility semantics, or variant ownership. The target uses Tailwind v4 and shadcn/ui together as one foundation.

### Replace the complete Frontend stack

Changing React, Router, React Query, Zustand, the icon set, or the charting library would combine the UI rewrite with routing, state, and visualization migrations. Those libraries already own valid responsibilities, so the proposal changes the common UI foundation without replacing them.

## Acceptance criteria

- The Frontend remains React 19, TypeScript, and Vite, with React Router, React Query, Zustand, Tabler Icons, and Recharts retaining their current responsibilities.
- Tailwind CSS v4 and source-owned shadcn/ui components provide the only common UI foundation.
- Semantic CSS variables own common color, surface, border, focus-ring, and theme meaning across light and dark themes.
- Global Tailwind Preflight is enabled only with the clean rewritten application, and every retained custom or third-party-rendered surface is verified against it.
- Every target page is rebuilt without preserving old generic UI props, DOM structure, class names, or CSS contracts.
- No adapter, compatibility component, legacy style dependency, or parallel generic UI system remains for Dialog, Toast, Modal, Button, Input, Tabs, Table, or Sidebar behavior.
- Retained business state, service, API, routing, localization, and visualization logic follows its existing owner; presentation code does not become a second state or API owner.
- Atlas and other specialized visualizations may remain custom, but applicable common controls and overlays use the shared shadcn/ui or Radix interaction and accessibility behavior.
- Critical user journeys pass real-browser tests at supported viewport sizes and in supported themes.
- Dialogs, sheets, menus, popovers, tabs, inputs, and other interactive primitives pass keyboard, focus-order, focus-trap, focus-return, Escape, labeling, disabled-state, and automated accessibility checks applicable to each primitive.
- Visual regression evidence covers the application shell, common primitives, target pages, responsive layouts, and light and dark themes.
- Bundle size, browser long tasks, render behavior, route usability, and interaction latency meet the declared Frontend performance budget and the applicable responsiveness contract.
- Backend API stability, Frontend compilation and bundling, browser behavior, accessibility, visual fidelity, performance, deployment, and live acceptance are reported as separate evidence.

## Initialization verification

- `npm run lint`, `npm run format:check`, and `npx tsc --noEmit` pass.
- `npm test`: 204 tests pass.
- `npm run build` and `npm run build:ui` pass with separate application and preview output directories.
- `shadcn info` recognizes Vite, Tailwind v4, the Radix base, Tabler icons, configured aliases, and the installed Button.
- Browser checks at 1280 × 900 and 390 × 844 verify Button rendering, a 36 px default height, click handling, disabled state, keyboard Tab order, light/dark theme switching, and no horizontal overflow.
- Input/Field browser checks at 1280 × 1000 and 390 × 844 verify 36 px height, 6 px radius, 400 weight, label focus, disabled/read-only behavior, error associations, first-invalid focus, valid submission, reset, theme rendering, and no horizontal overflow. Lint, formatting, TypeScript, and the UI build pass after adding these components.
- Registry download, remaining components, rewritten business pages, full accessibility acceptance, production deployment, and live integrations are not verified.

## Risks

Global Preflight can change headings, lists, media, borders, form controls, embedded content, and Atlas surfaces. Enabling it only at the clean cutover avoids mixed reset behavior, but every retained custom surface still needs browser and visual verification.

Source ownership makes shadcn/ui components intentionally editable, which also makes local divergence possible. Changes to common primitives require one explicit owner, narrow variants, and component-level interaction and accessibility coverage.

The clean rewrite has a larger integration boundary than an adapter migration and can omit subtle business behavior. Reuse is limited to verified business logic, and critical journeys must be locked with browser acceptance before the old presentation is removed. Rollback uses the previous deployable Frontend artifact; it does not keep a runtime legacy UI switch.

Backend contract changes can invalidate integration work even after visual completion. Real service wiring starts only after its consumed Backend contract stabilizes; component previews do not establish API or live-browser business acceptance.

Tailwind utilities, source-owned primitives, charts, and retained custom visuals can increase CSS, JavaScript, render, or main-thread cost. The rewrite must measure bundle composition and browser responsiveness rather than treating framework adoption or a successful build as performance evidence.
