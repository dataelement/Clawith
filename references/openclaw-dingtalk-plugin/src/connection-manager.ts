/**
 * Connection Manager for DingTalk Stream Client
 *
 * Provides robust connection lifecycle management with:
 * - Exponential backoff with jitter for reconnection attempts
 * - Configurable max attempts and delay parameters
 * - Connection state tracking and event handling
 * - Proper cleanup of timers and resources
 * - Structured logging for all connection events
 */

import type { DWClient } from "dingtalk-stream";
import type {
  ConnectionState,
  ConnectionManagerConfig,
  ConnectionAttemptResult,
  Logger,
  StreamClientFactory,
} from "./types";
import { ConnectionState as ConnectionStateEnum } from "./types";

/**
 * Thrown when a runtime reconnection cycle exceeds the configured deadline.
 * Identified via instanceof in reconnect() to skip cycle counting.
 */
export class ReconnectDeadlineError extends Error {
  constructor(options?: ErrorOptions) {
    super("Reconnect deadline exceeded", options);
    this.name = "ReconnectDeadlineError";
  }
}

/**
 * ConnectionManager handles the robust connection lifecycle for DWClient
 */
export class ConnectionManager {
  private config: ConnectionManagerConfig;
  private log?: Logger;
  private accountId: string;

  // Connection state tracking
  private state: ConnectionState = ConnectionStateEnum.DISCONNECTED;
  private attemptCount: number = 0;
  private reconnectTimer?: NodeJS.Timeout;
  private stopped: boolean = false;
  private connectedAt?: number;
  private consecutiveUnhealthyChecks: number = 0;

  private static readonly HEALTH_CHECK_INTERVAL_MS = 60000;
  private static readonly HEALTH_CHECK_GRACE_MS = 30000;
  private static readonly HEALTH_CHECK_UNHEALTHY_THRESHOLD = 2;
  private static readonly DEFAULT_MAX_RECONNECT_CYCLES = 10;
  private static readonly MAX_CYCLE_BACKOFF_MS = 5000;
  private static readonly MAX_CONSECUTIVE_DEADLINE_TIMEOUTS = 5;
  private static readonly HEARTBEAT_INTERVAL_MS = 20_000;
  private static readonly HEARTBEAT_MISS_THRESHOLD = 2;
  private runtimeReconnectCycles: number = 0;
  private reconnectDeadline?: number;
  private consecutiveDeadlineTimeouts: number = 0;
  private lastSocketActivityAt?: number;
  private lastHeartbeatPingAt?: number;
  private consecutiveHeartbeatMisses: number = 0;
  private runtimeCounters = {
    healthUnhealthyChecks: 0,
    healthTriggeredReconnects: 0,
    heartbeatMisses: 0,
    heartbeatTriggeredReconnects: 0,
    serverDisconnectMessages: 0,
    socketCloseEvents: 0,
    runtimeDisconnects: 0,
    reconnectAttempts: 0,
    reconnectSuccess: 0,
    reconnectFailures: 0,
  };

  // Runtime monitoring resources
  private healthCheckInterval?: NodeJS.Timeout;
  private heartbeatInterval?: NodeJS.Timeout;
  private socketCloseHandler?: (code: number, reason: string) => void;
  private socketErrorHandler?: (error: Error) => void;
  private socketMessageHandler?: (data: any) => void;
  private socketPongHandler?: () => void;
  private monitoredSocket?: any;

  // Sleep abort control
  private sleepTimeout?: NodeJS.Timeout;
  private sleepResolve?: () => void;

  // Stop signal for waitForStop()
  private stopPromiseResolvers: Array<() => void> = [];

  // Client reference
  private client: DWClient;

  // Warm-reconnect: factory to create fresh DWClient instances with listeners
  // already registered so the new socket starts receiving immediately.
  private clientFactory?: StreamClientFactory;
  // Old client pending cleanup after a warm reconnect swap.
  private pendingOldClient?: DWClient;

  constructor(client: DWClient, accountId: string, config: ConnectionManagerConfig, log?: Logger, clientFactory?: StreamClientFactory) {
    this.client = client;
    this.accountId = accountId;
    this.config = config;
    this.log = log;
    this.clientFactory = clientFactory;
  }

  private notifyStateChange(error?: string): void {
    if (this.config.onStateChange) {
      this.config.onStateChange(this.state, error);
    }
  }

  private logRuntimeCounters(reason: string): void {
    const c = this.runtimeCounters;
    this.log?.info?.(
      `[${this.accountId}] Runtime counters (${reason}): healthUnhealthyChecks=${c.healthUnhealthyChecks}, healthTriggeredReconnects=${c.healthTriggeredReconnects}, heartbeatMisses=${c.heartbeatMisses}, heartbeatTriggeredReconnects=${c.heartbeatTriggeredReconnects}, serverDisconnectMessages=${c.serverDisconnectMessages}, socketCloseEvents=${c.socketCloseEvents}, runtimeDisconnects=${c.runtimeDisconnects}, reconnectAttempts=${c.reconnectAttempts}, reconnectSuccess=${c.reconnectSuccess}, reconnectFailures=${c.reconnectFailures}`,
    );
  }

  private recordSocketActivity(now: number = Date.now()): void {
    this.lastSocketActivityAt = now;
    this.consecutiveHeartbeatMisses = 0;
  }

  private setupHeartbeat(socket: any): void {
    this.cleanupHeartbeatInterval();
    this.heartbeatInterval = setInterval(() => {
      if (this.stopped || this.state !== ConnectionStateEnum.CONNECTED) {
        return;
      }

      if (socket.readyState !== 1) {
        return;
      }

      const now = Date.now();
      const idleMs = this.lastSocketActivityAt !== undefined
        ? now - this.lastSocketActivityAt
        : ConnectionManager.HEARTBEAT_INTERVAL_MS;
      if (idleMs >= ConnectionManager.HEARTBEAT_INTERVAL_MS) {
        this.consecutiveHeartbeatMisses += 1;
        this.runtimeCounters.heartbeatMisses += 1;

        if (this.consecutiveHeartbeatMisses >= ConnectionManager.HEARTBEAT_MISS_THRESHOLD) {
          const lastPingAgoMs = this.lastHeartbeatPingAt !== undefined
            ? now - this.lastHeartbeatPingAt
            : undefined;
          this.log?.warn?.(
            `[${this.accountId}] Connection heartbeat missed ${this.consecutiveHeartbeatMisses}/${ConnectionManager.HEARTBEAT_MISS_THRESHOLD} checks, triggering reconnection (lastPingAgoMs=${lastPingAgoMs ?? "n/a"})`,
          );
          this.runtimeCounters.heartbeatTriggeredReconnects += 1;
          this.logRuntimeCounters("heartbeat-triggered-reconnect");
          this.cleanupHeartbeatInterval();
          this.handleRuntimeDisconnection();
          return;
        }

        this.log?.debug?.(
          `[${this.accountId}] Connection heartbeat missed (${this.consecutiveHeartbeatMisses}/${ConnectionManager.HEARTBEAT_MISS_THRESHOLD})`,
        );
      }

      this.lastHeartbeatPingAt = now;
      try {
        socket.ping("", true);
      } catch (err: any) {
        this.log?.warn?.(
          `[${this.accountId}] Failed to send heartbeat ping: ${err.message}`,
        );
      }
    }, ConnectionManager.HEARTBEAT_INTERVAL_MS);
  }

  private cleanupHeartbeatInterval(): void {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = undefined;
    }
  }

  private clearClientHeartbeatInterval(client: DWClient): void {
    const clientAny = client as any;
    if (clientAny.heartbeatIntervallId !== undefined) {
      clearInterval(clientAny.heartbeatIntervallId);
      clientAny.heartbeatIntervallId = undefined;
    }
  }

  private async waitForSocketOpen(clientAny: any): Promise<void> {
    const socket = clientAny.socket;
    if (!socket) {
      throw new Error("Socket unavailable after connect");
    }

    if (socket.readyState === 1 || clientAny.connected) {
      return;
    }

    const defaultOpenTimeout = 10_000;
    const openTimeout = this.reconnectDeadline !== undefined
      ? Math.min(defaultOpenTimeout, Math.max(1000, this.reconnectDeadline - Date.now()))
      : defaultOpenTimeout;

    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        cleanup();
        reject(new Error("Socket open timeout"));
      }, openTimeout);
      const cleanup = () => {
        clearTimeout(timeout);
        socket.removeListener("open", onOpen);
        socket.removeListener("error", onError);
        socket.removeListener("close", onClose);
      };
      const onOpen = () => { cleanup(); resolve(); };
      const onError = (err: Error) => { cleanup(); reject(err); };
      const onClose = () => { cleanup(); reject(new Error("Socket closed before open")); };
      socket.once("open", onOpen);
      socket.once("error", onError);
      socket.once("close", onClose);
    });
  }

  /**
   * Calculate next reconnection delay with exponential backoff and jitter
   * Formula: delay = min(initialDelay * 2^attempt, maxDelay) * (1 ± jitter)
   * @param attempt Zero-based attempt number (0 for first retry, 1 for second, etc.)
   */
  private calculateNextDelay(attempt: number): number {
    const { initialDelay, maxDelay, jitter } = this.config;

    // Exponential backoff: initialDelay * 2^attempt
    // For attempt=0 (first retry), this gives initialDelay * 1 = initialDelay
    const exponentialDelay = initialDelay * Math.pow(2, attempt);

    // Cap at maxDelay
    const cappedDelay = Math.min(exponentialDelay, maxDelay);

    // Apply jitter: randomize ± jitter%
    const jitterAmount = cappedDelay * jitter;
    const randomJitter = (Math.random() * 2 - 1) * jitterAmount;
    const finalDelay = Math.max(100, cappedDelay + randomJitter); // Minimum 100ms

    return Math.floor(finalDelay);
  }

  /**
   * Attempt to connect with retry logic
   */
  private async attemptConnection(): Promise<ConnectionAttemptResult> {
    if (this.stopped) {
      return {
        success: false,
        attempt: this.attemptCount,
        error: new Error("Connection manager stopped"),
      };
    }

    this.attemptCount++;
    this.state = ConnectionStateEnum.CONNECTING;
    this.notifyStateChange();

    this.log?.info?.(
      `[${this.accountId}] Connection attempt ${this.attemptCount}/${this.config.maxAttempts}...`,
    );

    try {
      // Warm-reconnect path: create a fresh DWClient so the new WebSocket
      // can start receiving messages while the old zombie socket is cleaned
      // up asynchronously. This minimizes the message-loss window during
      // server-initiated disconnects (DingTalk robot msgs are fire-and-forget).
      if (this.clientFactory) {
        const oldClient = this.client;
        this.pendingOldClient = oldClient;
        try {
          this.client = this.clientFactory();
          this.log?.info?.(
            `[${this.accountId}] Warm reconnect: created fresh DWClient, connecting new socket while old socket is cleaned up`,
          );
        } catch (factoryErr: any) {
          this.log?.warn?.(
            `[${this.accountId}] Client factory failed, falling back to same-client reconnect: ${factoryErr.message}`,
          );
          this.client = oldClient;
          this.pendingOldClient = undefined;
        }
      }

      if (!this.pendingOldClient) {
        // Legacy single-client reconnect: disconnect before reconnecting.
        try {
          this.client.disconnect();
        } catch (disconnectErr: any) {
          this.log?.debug?.(
            `[${this.accountId}] pre-connect cleanup disconnect failed: ${disconnectErr.message}`,
          );
        }
      }

      // SDK _connect() resolves before socket "open" fires. If disconnect() runs
      // before "open", heartbeatIntervallId is still undefined and clearInterval
      // is a no-op. The deferred "open" handler then creates an interval that
      // outlives the old socket and can terminate the next connection.
      // For the race where interval is created AFTER this cleanup, the socket
      // open timeout below serves as the final safety net.
      // Field name "heartbeatIntervallId" (double-l typo) from dingtalk-stream
      // SDK DWClient._connect() open handler (verified in SDK v1.x).
      const clientAny = this.client as any;
      this.clearClientHeartbeatInterval(this.client);

      await this.client.connect();
      await this.waitForSocketOpen(clientAny);

      if (this.stopped) {
        this.log?.warn?.(
          `[${this.accountId}] Connection succeeded but manager was stopped during connect - disconnecting`,
        );
        try {
          this.client.disconnect();
        } catch (disconnectErr: any) {
          this.log?.debug?.(
            `[${this.accountId}] Error during post-connect disconnect: ${disconnectErr.message}`,
          );
        }
        return {
          success: false,
          attempt: this.attemptCount,
          error: new Error("Connection manager stopped during connect"),
        };
      }

      this.state = ConnectionStateEnum.CONNECTED;
      this.connectedAt = Date.now();
      this.recordSocketActivity(this.connectedAt);
      this.lastHeartbeatPingAt = undefined;
      this.reconnectDeadline = undefined;
      this.consecutiveUnhealthyChecks = 0;
      this.consecutiveHeartbeatMisses = 0;
      this.notifyStateChange();
      const successfulAttempt = this.attemptCount;
      this.attemptCount = 0;

      this.log?.info?.(`[${this.accountId}] DingTalk Stream client connected successfully`);

      this.runtimeReconnectCycles = 0;
      this.consecutiveDeadlineTimeouts = 0;
      // Setup monitoring BEFORE cleaning up old client: setupRuntimeReconnection
      // calls cleanupRuntimeMonitoring() which removes the old socket's event
      // listeners, preventing the old socket's close event (triggered by
      // disconnect below) from erroneously firing handleRuntimeDisconnection.
      this.setupRuntimeReconnection();
      this.cleanupPendingOldClient();

      return { success: true, attempt: successfulAttempt };
    } catch (err: any) {
      // Warm-reconnect failed with new client: revert to old client so
      // subsequent retry attempts don't keep creating throwaway instances.
      if (this.pendingOldClient) {
        try { this.client.disconnect(); } catch { /* best-effort cleanup of failed new client */ }
        this.client = this.pendingOldClient;
        this.pendingOldClient = undefined;
        this.log?.debug?.(
          `[${this.accountId}] Warm reconnect failed, reverted to previous client for next retry`,
        );
      }

      this.log?.error?.(
        `[${this.accountId}] Connection attempt ${this.attemptCount} failed: ${err.message}`,
      );

      // Check if we've exceeded max attempts
      if (this.attemptCount >= this.config.maxAttempts) {
        this.state = ConnectionStateEnum.FAILED;
        this.notifyStateChange("Max connection attempts reached");
        this.log?.error?.(
          `[${this.accountId}] Max connection attempts (${this.config.maxAttempts}) reached. Giving up.`,
        );
        return { success: false, attempt: this.attemptCount, error: err };
      }

      // Calculate next retry delay (use attemptCount-1 for zero-based exponent)
      // This ensures first retry uses 2^0 = 1x initialDelay
      const nextDelay = this.calculateNextDelay(this.attemptCount - 1);

      this.log?.warn?.(
        `[${this.accountId}] Will retry connection in ${(nextDelay / 1000).toFixed(2)}s (attempt ${this.attemptCount + 1}/${this.config.maxAttempts})`,
      );

      return { success: false, attempt: this.attemptCount, error: err, nextDelay };
    }
  }

  /**
   * Connect with robust retry logic
   */
  public async connect(): Promise<void> {
    if (this.stopped) {
      throw new Error("Cannot connect: connection manager is stopped");
    }

    // Clear any existing reconnect timer
    this.clearReconnectTimer();

    this.log?.info?.(
      `[${this.accountId}] Starting DingTalk Stream client with robust connection...`,
    );

    while (!this.stopped && this.state !== ConnectionStateEnum.CONNECTED) {
      if (this.reconnectDeadline !== undefined && Date.now() >= this.reconnectDeadline) {
        this.reconnectDeadline = undefined;
        throw new ReconnectDeadlineError();
      }

      const result = await this.attemptConnection();

      if (result.success) {
        return;
      }

      if (result.error?.message === "Connection manager stopped during connect") {
        this.log?.info?.(
          `[${this.accountId}] Connection cancelled: manager stopped during connect`,
        );
        throw new Error("Connection cancelled: connection manager stopped");
      }

      if (!result.nextDelay || this.attemptCount >= this.config.maxAttempts) {
        throw new Error(`Failed to connect after ${this.attemptCount} attempts`);
      }

      // Truncate sleep to remaining deadline budget
      let actualDelay = result.nextDelay;
      if (this.reconnectDeadline !== undefined) {
        const remaining = Math.max(0, this.reconnectDeadline - Date.now());
        actualDelay = Math.min(actualDelay, remaining);
      }

      await this.sleep(actualDelay);
    }
  }

  /**
   * Setup runtime reconnection handlers
   * Monitors DWClient connection state for automatic reconnection
   */
  private setupRuntimeReconnection(): void {
    this.log?.debug?.(`[${this.accountId}] Setting up runtime reconnection monitoring`);

    // Clean up any existing monitoring resources before setting up new ones
    this.cleanupRuntimeMonitoring();

    // Access DWClient internals to monitor connection state
    const client = this.client as any;

    // Monitor client's 'connected' property changes
    // We'll set up an interval to periodically check connection health
    this.healthCheckInterval = setInterval(() => {
      if (this.stopped) {
        if (this.healthCheckInterval) {
          clearInterval(this.healthCheckInterval);
        }
        return;
      }

      if (this.state !== ConnectionStateEnum.CONNECTED) {
        this.consecutiveUnhealthyChecks = 0;
        return;
      }

      const now = Date.now();
      const withinGraceWindow =
        this.connectedAt !== undefined &&
        now - this.connectedAt < ConnectionManager.HEALTH_CHECK_GRACE_MS;
      if (withinGraceWindow) {
        this.consecutiveUnhealthyChecks = 0;
        return;
      }

      const socketReadyState = (client.socket as { readyState?: number } | undefined)?.readyState;
      const socketOpen = socketReadyState === 1;
      const registered = client.registered as boolean | undefined;

      // Unhealthy if:
      // 1. Not connected AND socket not open (full disconnect)
      // 2. Socket is open, client.connected is false, AND not registered
      //    (server sent logical "disconnect" and the socket is now a zombie)
      const unhealthy =
        (!client.connected && !socketOpen) ||
        (socketOpen && !client.connected && registered === false);

      if (!unhealthy) {
        this.consecutiveUnhealthyChecks = 0;
        return;
      }

      this.consecutiveUnhealthyChecks += 1;
      this.runtimeCounters.healthUnhealthyChecks += 1;
      if (
        this.consecutiveUnhealthyChecks <
        ConnectionManager.HEALTH_CHECK_UNHEALTHY_THRESHOLD
      ) {
        this.log?.debug?.(
          `[${this.accountId}] Connection health check unhealthy (${this.consecutiveUnhealthyChecks}/${ConnectionManager.HEALTH_CHECK_UNHEALTHY_THRESHOLD}) connected=${String(client.connected)} registered=${String(registered)} socketReadyState=${socketReadyState ?? "unknown"}`,
        );
        return;
      }

      this.log?.warn?.(
        `[${this.accountId}] Connection health check failed - detected disconnection`,
      );
      this.runtimeCounters.healthTriggeredReconnects += 1;
      this.logRuntimeCounters("health-triggered-reconnect");
      if (this.healthCheckInterval) {
        clearInterval(this.healthCheckInterval);
      }
      this.handleRuntimeDisconnection();
    }, ConnectionManager.HEALTH_CHECK_INTERVAL_MS);

    // Additionally, if we have access to the socket, monitor its events
    // The DWClient uses 'ws' WebSocket library which extends EventEmitter
    if (client.socket) {
      const socket = client.socket;
      // Store the socket instance we're attaching listeners to
      this.monitoredSocket = socket;
      this.setupHeartbeat(socket);

      // Handler for socket close event
      this.socketCloseHandler = (code: number, reason: string) => {
        this.runtimeCounters.socketCloseEvents += 1;
        this.log?.warn?.(
          `[${this.accountId}] WebSocket closed event (code: ${code}, reason: ${reason || "none"})`,
        );
        this.logRuntimeCounters("socket-close");

        // Only trigger reconnection if we were previously connected and not stopping
        if (!this.stopped && this.state === ConnectionStateEnum.CONNECTED) {
          if (this.healthCheckInterval) {
            clearInterval(this.healthCheckInterval);
          }
          this.handleRuntimeDisconnection();
        }
      };

      // Handler for socket error event
      this.socketErrorHandler = (error: Error) => {
        this.log?.error?.(
          `[${this.accountId}] WebSocket error event: ${error?.message || "Unknown error"}`,
        );
      };

      socket.once("close", this.socketCloseHandler);
      socket.once("error", this.socketErrorHandler);

      // Monitor for server-side disconnect system messages and passive socket
      // activity.
      this.socketMessageHandler = (data: any) => {
        this.recordSocketActivity();
        try {
          const msg = JSON.parse(typeof data === "string" ? data : data.toString());
          if (msg?.type === "SYSTEM" && msg?.headers?.topic === "disconnect") {
            this.runtimeCounters.serverDisconnectMessages += 1;
            this.log?.warn?.(
              `[${this.accountId}] Server disconnect system message received, triggering immediate reconnection`,
            );
            this.logRuntimeCounters("server-disconnect");
            if (this.healthCheckInterval) {
              clearInterval(this.healthCheckInterval);
            }
            this.handleRuntimeDisconnection();
          }
        } catch {
          // Ignore parse errors — other handlers will process the message
        }
      };
      socket.on("message", this.socketMessageHandler);

      this.socketPongHandler = () => {
        this.recordSocketActivity();
      };
      socket.on("pong", this.socketPongHandler);
    }
  }

  /**
   * Disconnect and release the old client left over from a warm-reconnect swap.
   */
  private cleanupPendingOldClient(): void {
    if (!this.pendingOldClient) {
      return;
    }
    const old = this.pendingOldClient;
    this.pendingOldClient = undefined;
    try {
      // Clear stale heartbeat interval from old SDK instance.
      const oldAny = old as any;
      if (oldAny.heartbeatIntervallId !== undefined) {
        clearInterval(oldAny.heartbeatIntervallId);
        oldAny.heartbeatIntervallId = undefined;
      }
      old.disconnect();
      this.log?.debug?.(`[${this.accountId}] Warm reconnect: old client disconnected`);
    } catch (err: any) {
      this.log?.debug?.(
        `[${this.accountId}] Warm reconnect: old client cleanup failed: ${err.message}`,
      );
    }
  }

  /**
   * Clean up runtime monitoring resources (intervals and event listeners)
   */
  private cleanupRuntimeMonitoring(): void {
    // Clear health check interval
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = undefined;
      this.log?.debug?.(`[${this.accountId}] Health check interval cleared`);
    }
    this.cleanupHeartbeatInterval();

    // Remove socket event listeners from the stored socket instance
    if (this.monitoredSocket) {
      const socket = this.monitoredSocket;

      if (this.socketCloseHandler) {
        socket.removeListener("close", this.socketCloseHandler);
        this.socketCloseHandler = undefined;
      }
      if (this.socketErrorHandler) {
        socket.removeListener("error", this.socketErrorHandler);
        this.socketErrorHandler = undefined;
      }
      if (this.socketMessageHandler) {
        socket.removeListener("message", this.socketMessageHandler);
        this.socketMessageHandler = undefined;
      }
      if (this.socketPongHandler) {
        socket.removeListener("pong", this.socketPongHandler);
        this.socketPongHandler = undefined;
      }

      this.log?.debug?.(`[${this.accountId}] Socket event listeners removed from monitored socket`);
      this.monitoredSocket = undefined;
    }
  }

  /**
   * Handle runtime disconnection and trigger reconnection
   */
  private handleRuntimeDisconnection(): void {
    if (this.stopped || this.state !== ConnectionStateEnum.CONNECTED) {
      return;
    }

    this.state = ConnectionStateEnum.DISCONNECTED;

    this.log?.warn?.(
      `[${this.accountId}] Runtime disconnection detected, initiating reconnection...`,
    );
    this.runtimeCounters.runtimeDisconnects += 1;

    this.notifyStateChange("Runtime disconnection detected");
    this.attemptCount = 0;
    this.connectedAt = undefined;
    this.lastSocketActivityAt = undefined;
    this.lastHeartbeatPingAt = undefined;
    this.consecutiveUnhealthyChecks = 0;
    this.consecutiveHeartbeatMisses = 0;

    const deadlineMs = this.config.reconnectDeadlineMs ?? 50000;
    this.reconnectDeadline = Date.now() + deadlineMs;

    this.clearReconnectTimer();

    this.log?.info?.(`[${this.accountId}] Scheduling immediate reconnection`);

    this.reconnectTimer = setTimeout(() => {
      this.reconnect().catch((err) => {
        this.log?.error?.(`[${this.accountId}] Reconnection failed: ${err.message}`);
      });
    }, 0);
  }

  /**
   * Reconnect after runtime disconnection
   */
  private async reconnect(): Promise<void> {
    if (this.stopped) {
      return;
    }

    this.log?.info?.(`[${this.accountId}] Attempting to reconnect...`);
    this.runtimeCounters.reconnectAttempts += 1;

    try {
      await this.connect();
      this.log?.info?.(`[${this.accountId}] Reconnection successful`);
      this.runtimeCounters.reconnectSuccess += 1;
      this.logRuntimeCounters("reconnect-success");
    } catch (err: any) {
      if (this.stopped) {
        return;
      }

      this.log?.error?.(`[${this.accountId}] Reconnection failed: ${err.message}`);
      this.runtimeCounters.reconnectFailures += 1;
      this.logRuntimeCounters("reconnect-failed");

      if (err instanceof ReconnectDeadlineError) {
        this.consecutiveDeadlineTimeouts += 1;

        if (this.consecutiveDeadlineTimeouts >= ConnectionManager.MAX_CONSECUTIVE_DEADLINE_TIMEOUTS) {
          this.log?.error?.(
            `[${this.accountId}] Max consecutive deadline timeouts (${ConnectionManager.MAX_CONSECUTIVE_DEADLINE_TIMEOUTS}) reached. Giving up.`,
          );
          this.state = ConnectionStateEnum.FAILED;
          this.connectedAt = undefined;
          this.consecutiveUnhealthyChecks = 0;
          this.reconnectDeadline = undefined;
          this.notifyStateChange(
            `Max consecutive deadline timeouts (${ConnectionManager.MAX_CONSECUTIVE_DEADLINE_TIMEOUTS}) reached`,
          );
          return;
        }

        const deadlineMs = this.config.reconnectDeadlineMs ?? 50000;
        this.reconnectDeadline = Date.now() + deadlineMs;
        const delay = Math.min(
          this.calculateNextDelay(0),
          ConnectionManager.MAX_CYCLE_BACKOFF_MS,
        );
        this.attemptCount = 0;
        this.clearReconnectTimer();
        this.log?.warn?.(
          `[${this.accountId}] Reconnect deadline exceeded (${this.consecutiveDeadlineTimeouts}/${ConnectionManager.MAX_CONSECUTIVE_DEADLINE_TIMEOUTS}); scheduling next cycle in ${(delay / 1000).toFixed(2)}s`,
        );
        this.reconnectTimer = setTimeout(() => {
          void this.reconnect();
        }, delay);
        return;
      }

      this.runtimeReconnectCycles += 1;
      const maxCycles = this.config.maxReconnectCycles ?? ConnectionManager.DEFAULT_MAX_RECONNECT_CYCLES;

      if (this.runtimeReconnectCycles >= maxCycles) {
        this.log?.error?.(
          `[${this.accountId}] Max runtime reconnect cycles (${maxCycles}) reached. Giving up. ` +
          `Please check network connectivity or restart the gateway.`,
        );
        this.state = ConnectionStateEnum.FAILED;
        this.connectedAt = undefined;
        this.consecutiveUnhealthyChecks = 0;
        this.reconnectDeadline = undefined;
        this.notifyStateChange(`Max runtime reconnect cycles (${maxCycles}) reached`);
        return;
      }

      this.state = ConnectionStateEnum.FAILED;
      this.connectedAt = undefined;
      this.consecutiveUnhealthyChecks = 0;
      this.notifyStateChange(err.message);

      const rawDelay = this.calculateNextDelay(Math.min(this.runtimeReconnectCycles - 1, 6));
      const delay = Math.min(rawDelay, ConnectionManager.MAX_CYCLE_BACKOFF_MS);
      // Each cycle gets its own deadline so long-running retries don't block indefinitely
      const deadlineMs = this.config.reconnectDeadlineMs ?? 50000;
      this.reconnectDeadline = Date.now() + deadlineMs;
      this.attemptCount = 0;
      this.clearReconnectTimer();
      this.log?.warn?.(
        `[${this.accountId}] Reconnection cycle ${this.runtimeReconnectCycles}/${maxCycles} failed; scheduling next reconnect in ${(delay / 1000).toFixed(2)}s`,
      );
      this.reconnectTimer = setTimeout(() => {
        void this.reconnect();
      }, delay);
    }
  }

  /**
   * Stop the connection manager and cleanup resources
   */
  public stop(): void {
    if (this.stopped) {
      return;
    }

    this.log?.info?.(`[${this.accountId}] Stopping connection manager...`);

    this.stopped = true;
    this.state = ConnectionStateEnum.DISCONNECTING;
    this.connectedAt = undefined;
    this.reconnectDeadline = undefined;
    this.consecutiveDeadlineTimeouts = 0;
    this.consecutiveUnhealthyChecks = 0;
    this.lastSocketActivityAt = undefined;
    this.lastHeartbeatPingAt = undefined;
    this.consecutiveHeartbeatMisses = 0;

    // Clear reconnect timer
    this.clearReconnectTimer();

    // Cancel any in-flight sleep (retry delay)
    this.cancelSleep();

    // Clean up runtime monitoring resources
    this.cleanupRuntimeMonitoring();

    // Clean up any pending old client from warm-reconnect swap.
    this.cleanupPendingOldClient();

    // Disconnect client
    try {
      this.client.disconnect();
    } catch (err: any) {
      this.log?.warn?.(`[${this.accountId}] Error during disconnect: ${err.message}`);
    }

    this.state = ConnectionStateEnum.DISCONNECTED;
    this.log?.info?.(`[${this.accountId}] Connection manager stopped`);

    // Resolve all pending waitForStop() promises
    for (const resolve of this.stopPromiseResolvers) {
      resolve();
    }
    this.stopPromiseResolvers = [];
  }

  /**
   * Returns a Promise that resolves when the connection manager is stopped.
   * Useful for keeping a caller alive (e.g. startAccount) until the channel
   * is explicitly stopped via stop() or an abort signal handler that calls stop().
   * Safe to call concurrently; all pending callers are resolved when stop() is called.
   */
  public waitForStop(): Promise<void> {
    if (this.stopped) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      this.stopPromiseResolvers.push(resolve);
    });
  }

  /**
   * Clear reconnection timer
   */
  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = undefined;
      this.log?.debug?.(`[${this.accountId}] Reconnect timer cleared`);
    }
  }

  /**
   * Sleep utility for retry delays
   * Returns a promise that resolves after ms or can be cancelled via cancelSleep()
   */
  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      this.sleepResolve = resolve;
      this.sleepTimeout = setTimeout(() => {
        this.sleepTimeout = undefined;
        this.sleepResolve = undefined;
        resolve();
      }, ms);
    });
  }

  /**
   * Cancel any in-flight sleep operation
   * Resolves the pending promise immediately so await unblocks
   */
  private cancelSleep(): void {
    if (this.sleepTimeout) {
      clearTimeout(this.sleepTimeout);
      this.sleepTimeout = undefined;
      this.log?.debug?.(`[${this.accountId}] Sleep timeout cancelled`);
    }
    // Resolve the pending promise so await unblocks immediately
    if (this.sleepResolve) {
      this.sleepResolve();
      this.sleepResolve = undefined;
    }
  }

  /**
   * Get current connection state
   */
  public getState(): ConnectionState {
    return this.state;
  }

  /**
   * Check if connection is active
   */
  public isConnected(): boolean {
    return this.state === ConnectionStateEnum.CONNECTED;
  }

  /**
   * Check if connection manager is stopped
   */
  public isStopped(): boolean {
    return this.stopped;
  }
}
