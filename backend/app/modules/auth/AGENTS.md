# Auth module

Auth owns local password verifiers and opaque login sessions. `public.py` is the only cross-owner
surface; persistence and encoded verifier/session forms are private. Password KDF work runs outside
database transactions. Session tokens are random and only their digest is stored.

Login resolves enabled Identity/Tenant facts and freezes Permission scope once. Authentication and
logout validate the stored snapshot, stored expiry, and logout marker without rereading current
roles, Membership enablement, or grants. Logout never cancels a Run. Do not add SSO, registration,
recovery, live reauthorization, authorization generations, or revocation sweeps here.

Lifetime is explicit and validation does not implicitly renew a session. `authenticate_session` exposes the validated Principal and fixed expiry to product transport; it never renews access or changes Runs. G006 applies the approved 24-hour product policy; see the [capture decision](../../../../.agents/notes/implemented/architecture/2026-09-06-minimal-auth-capture.md).
