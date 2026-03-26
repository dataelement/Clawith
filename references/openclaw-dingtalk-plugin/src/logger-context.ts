import type { Logger } from "./types";

let currentLogger: Logger | undefined;

/**
 * Persist current request logger for shared services invoked outside handler scope.
 */
export function setCurrentLogger(log?: Logger): void {
  currentLogger = log;
}

/**
 * Read current logger bound by inbound handler.
 */
export function getLogger(): Logger | undefined {
  return currentLogger;
}
