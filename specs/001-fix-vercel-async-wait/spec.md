# Feature Specification: Vercel Async Deployment Wait Recovery

**Feature Branch**: `001-fix-vercel-async-wait`
**Created**: 2026-08-05
**Status**: Draft
**Input**: User description: "Fix the Vercel asynchronous deployment wait bug with the smallest
possible change. Keep non-terminal deployments pending, poll the exact deployment through the
existing durable Runtime, settle only on a provider terminal state, and never create a duplicate
deployment."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Receive the Final Deployment Result (Priority: P1)

As a user who asks an Agent to deploy a project to Vercel, I receive a final response after the
specific deployment reaches a terminal state instead of seeing the Agent remain stuck waiting after
Vercel has completed the deployment.

**Why this priority**: This is the reported production failure. A deployment can finish successfully
while the user never receives a completion response.

**Independent Test**: Start one deployment that reports BUILDING before READY. The system must keep
the operation pending, check the same deployment again, settle it as successful, resume the same
Run, and make the final result available for the Agent response.

**Acceptance Scenarios**:

1. **Given** Vercel accepts a deployment and reports INITIALIZING, QUEUED, or BUILDING, **When** the
   initial deployment call completes, **Then** the operation remains pending and the Run waits for
   the existing Runtime polling mechanism.
2. **Given** the tracked deployment is pending, **When** Vercel later reports READY, **Then** the
   original deployment operation succeeds and the same Run continues to its final response.
3. **Given** the tracked deployment is pending, **When** Vercel later reports ERROR or CANCELED,
   **Then** the original deployment operation fails and the same Run continues through existing
   failure handling.

---

### User Story 2 - Avoid Duplicate Deployments (Priority: P2)

As a user waiting for a deployment, I expect status checks to observe the deployment already created
for my request and never create additional deployments.

**Why this priority**: Repeating an external write while polling can deploy stale or duplicate
versions and violates the existing exactly-once Tool contract.

**Independent Test**: Exercise multiple non-terminal status checks followed by a terminal status and
verify that the provider receives exactly one create request while every status check uses the
original deployment identifier.

**Acceptance Scenarios**:

1. **Given** a deployment has already been created, **When** one or more Runtime polls execute,
   **Then** each poll performs only an exact status read for the original deployment.
2. **Given** a poll is resumed after a process restart, **When** it executes, **Then** it uses the
   persisted deployment identifier and does not repeat project creation, upload, repository linking,
   or deployment creation.

### Edge Cases

- A status read times out after a stable deployment identifier has already been received; the
  deployment must not be reported as successful solely because the create request was accepted.
- A successful status response omits a usable state, reports an unknown state, identifies a
  different deployment, or lacks a valid deployment URL; the system must not fabricate success.
- Vercel reports READY immediately in the create response; the operation completes without entering
  the asynchronous wait path.
- Vercel reports ERROR or CANCELED immediately; the operation fails without scheduling another poll.
- A project contains multiple deployments; list results must not settle the deployment operation
  because only the exact deployment identifier is authoritative.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat INITIALIZING, QUEUED, and BUILDING as non-terminal deployment
  states.
- **FR-002**: A non-terminal deployment MUST remain pending and include a stable operation identity,
  exact deployment identity, and instructions for the existing Runtime polling mechanism.
- **FR-003**: Every status check MUST query the exact deployment created by the original request.
- **FR-004**: A status check MUST NOT create a project, upload files, link a repository, or create a
  deployment.
- **FR-005**: READY MUST settle the original operation as successful.
- **FR-006**: ERROR and CANCELED MUST settle the original operation as failed.
- **FR-007**: A status timeout after receipt of a stable deployment identity MUST NOT settle the
  operation as successful.
- **FR-008**: A missing, unknown, or mismatched provider state MUST NOT settle the operation as
  successful.
- **FR-009**: All non-terminal checks for one deployment MUST retain the same operation identity so
  the existing Runtime can settle the original operation at terminal completion.
- **FR-010**: Deployment list results MUST NOT settle or resume the original deployment operation.
- **FR-011**: The final terminal result MUST allow the same Run to continue and produce its existing
  user-facing completion or failure response.
- **FR-012**: The change MUST reuse the existing asynchronous Runtime scheduling, waiting, resume, and
  settlement behavior without changing generic wait behavior or other Tool contracts.

### Key Entities

- **Deployment Operation**: The single external deployment created for the user request, identified
  by a stable provider deployment identity and a stable operation identity.
- **Deployment State Observation**: One exact observation of the tracked deployment, containing its
  provider state and usable URL when available.
- **Agent Run**: The existing execution that initiated the deployment, waits while the operation is
  pending, and continues after terminal settlement.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In all automated scenarios where a deployment progresses through one or more
  non-terminal states to READY, the initiating Run resumes and reaches its final response.
- **SC-002**: Every tested deployment operation issues exactly one provider deployment-create
  request, regardless of the number of status checks.
- **SC-003**: All tested provider terminal states map deterministically: READY succeeds, while ERROR
  and CANCELED fail.
- **SC-004**: No tested non-terminal, missing, unknown, timed-out, or mismatched status is recorded as
  successful.
- **SC-005**: Existing asynchronous Runtime regression tests continue to pass without behavior
  changes outside the Vercel deployment path.

## Assumptions

- The existing durable Runtime scheduler, timer resume, waiting checkpoint, and operation settlement
  contracts remain the authoritative implementation and already function for declared asynchronous
  Tool operations.
- Polling uses the existing fixed two-second interval for this stopgap fix.
- General retry backoff, maximum retry counts, total operation deadlines, provider cancellation,
  rejected-resume reclamation, and generic Model/Runtime wait conflicts remain out of scope.
- The public deployment request remains unchanged; polling is an internal continuation of an already
  accepted operation.
- No other Tool behavior is changed unless a separate reproducible failure is established.
