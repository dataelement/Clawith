# Product-input transport

This package contains G006 HTTP and WebSocket adapters for Auth, Session, Group, Trigger, Heartbeat, Channel and input attachments. `app.application` mounts these routers. Deleted legacy API module identities remain absent; do not add compatibility re-exports.

Authenticate human requests through Auth's public login contract and authenticate provider events through Channel or Trigger intake. Parse bounded transport inputs, call public owner services or application composition, and serialize their results. Do not import owner models, repositories, legacy services or concrete storage. Run and product owners retain lifecycle and persistence authority.

WebSocket delivery reads committed authorized product data with bounded pages. Login expiry closes access, not Agent execution. Release connection tasks on disconnect, expiry and failure. Message acceptance, Run completion and external delivery remain separate outcomes.

`events.py` owns the shared Session/Group WebSocket login, polling, expiry and close loop. Routes supply owner-authorized bounded history pages; optional Session execution subscriptions remain separate. Pass the application close signal so committed-history-only Group sockets also stop before resources are disposed.

Session execution deltas use the bounded application stream subscription, separate from persisted chat history. Preserve Run/step/attempt identity and explicit discard/resync signals; never turn transient Model text into accepted messages. Poll authentication/history on a time boundary, not once per delta, and remove the subscription on every exit.
