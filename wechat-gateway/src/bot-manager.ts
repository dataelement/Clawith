/**
 * Bot Manager
 *
 * Manages multiple WeChatBot instances, one per agent.
 * Handles bot lifecycle, credential storage, and QR code tracking.
 */

import { WeChatBot, type IncomingMessage, type Credentials } from '@wechatbot/wechatbot';

export interface BotOptions {
  storageDir?: string;
  onMessage: (msg: IncomingMessage) => void | Promise<void>;
  onError: (error: unknown) => void;
  onLogin: (creds: Credentials) => void;
  onSessionExpired: () => void;
}

export class BotManager {
  private bots: Map<string, WeChatBot> = new Map();
  private qrUrls: Map<string, string> = new Map();
  private options: Map<string, BotOptions> = new Map();

  /**
   * Create a new bot instance for an agent.
   */
  async createBot(agentId: string, options: BotOptions): Promise<WeChatBot> {
    // Stop existing bot if any
    this.stopBot(agentId);

    const bot = new WeChatBot({
      storage: 'file',
      storageDir: options.storageDir || `/data/wechat-${agentId}`,
      logLevel: 'info',
    });

    // Set up event handlers (use onMessage for message handling per SDK docs)
    bot.onMessage(options.onMessage);
    bot.on('error', options.onError);
    bot.on('login', options.onLogin);
    bot.on('session:expired', options.onSessionExpired);

    this.bots.set(agentId, bot);
    this.options.set(agentId, options);

    console.log(`[BotManager] Created bot for agent ${agentId}`);
    return bot;
  }

  /**
   * Get an existing bot instance.
   */
  getBot(agentId: string): WeChatBot | undefined {
    return this.bots.get(agentId);
  }

  /**
   * Stop a bot instance (doesn't remove credentials).
   */
  stopBot(agentId: string): void {
    const bot = this.bots.get(agentId);
    if (bot) {
      try {
        bot.stop();
        console.log(`[BotManager] Stopped bot for agent ${agentId}`);
      } catch (e) {
        console.error(`[BotManager] Error stopping bot ${agentId}:`, e);
      }
      // Remove from map to allow fresh bot creation
      this.bots.delete(agentId);
      this.options.delete(agentId);
    }
    this.qrUrls.delete(agentId);
  }

  /**
   * Remove a bot instance completely (stops and clears).
   */
  async removeBot(agentId: string): Promise<void> {
    const bot = this.bots.get(agentId);
    if (bot) {
      try {
        // Stop the bot first
        bot.stop();
      } catch (e) {
        console.error(`[BotManager] Error stopping bot ${agentId}:`, e);
      }
    }

    // Clear all state
    this.bots.delete(agentId);
    this.options.delete(agentId);
    this.qrUrls.delete(agentId);
    console.log(`[BotManager] Removed bot for agent ${agentId}`);
  }

  /**
   * Set QR URL for an agent (during login flow).
   */
  setQrUrl(agentId: string, url: string): void {
    this.qrUrls.set(agentId, url);
  }

  /**
   * Get QR URL for an agent.
   */
  getQrUrl(agentId: string): string | undefined {
    return this.qrUrls.get(agentId);
  }

  /**
   * Get number of active bots.
   */
  getBotCount(): number {
    return this.bots.size;
  }

  /**
   * Get all bot agent IDs.
   */
  getBotIds(): string[] {
    return Array.from(this.bots.keys());
  }

  /**
   * Stop all bots (for graceful shutdown).
   */
  stopAll(): void {
    for (const [agentId, bot] of this.bots) {
      try {
        bot.stop();
        console.log(`[BotManager] Stopped bot for agent ${agentId}`);
      } catch (error) {
        console.error(`[BotManager] Error stopping bot ${agentId}:`, error);
      }
    }
    this.bots.clear();
    this.options.clear();
    this.qrUrls.clear();
  }
}
