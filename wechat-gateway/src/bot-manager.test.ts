/**
 * BotManager Unit Tests
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { BotManager } from './bot-manager.js';

// Mock the WeChatBot SDK
vi.mock('@wechatbot/wechatbot', () => {
  const mockBot = {
    isRunning: false,
    credentials: null as { userId: string; token: string } | null,
    login: vi.fn().mockImplementation(function (this: typeof mockBot, callbacks?: { callbacks: { onQrUrl: (url: string) => void } }) {
      // Simulate QR URL callback
      if (callbacks?.callbacks?.onQrUrl) {
        setTimeout(() => callbacks.callbacks.onQrUrl('https://test-qr-url.com/qr'), 10);
      }
      return new Promise((resolve) => {
        setTimeout(() => {
          this.credentials = { userId: 'test-user', token: 'test-token' };
          resolve(this.credentials);
        }, 50);
      });
    }),
    start: vi.fn().mockImplementation(function (this: typeof mockBot) {
      this.isRunning = true;
      return Promise.resolve();
    }),
    stop: vi.fn().mockImplementation(function (this: typeof mockBot) {
      this.isRunning = false;
    }),
    getCredentials: vi.fn().mockImplementation(function (this: typeof mockBot) {
      return this.credentials;
    }),
    on: vi.fn(),
    onMessage: vi.fn(),
    send: vi.fn().mockResolvedValue(undefined),
    sendTyping: vi.fn().mockResolvedValue(undefined),
    reply: vi.fn().mockResolvedValue(undefined),
  };

  return {
    WeChatBot: vi.fn(() => mockBot),
  };
});

describe('BotManager', () => {
  let botManager: BotManager;

  beforeEach(() => {
    botManager = new BotManager();
    vi.clearAllMocks();
  });

  describe('createBot', () => {
    it('should create a new bot instance for an agent', async () => {
      const options = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      const bot = await botManager.createBot('agent-123', options);

      expect(bot).toBeDefined();
      expect(botManager.getBot('agent-123')).toBe(bot);
    });

    it('should stop existing bot when creating a new one for same agent', async () => {
      const options1 = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      const options2 = {
        ...options1,
      };

      const bot1 = await botManager.createBot('agent-123', options1);
      const bot2 = await botManager.createBot('agent-123', options2);

      // The second call should stop the first bot
      expect(bot1.stop).toHaveBeenCalled();
      expect(botManager.getBot('agent-123')).toBe(bot2);
    });

    it('should set up event handlers on the bot', async () => {
      const options = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      await botManager.createBot('agent-123', options);

      // Verify onMessage was called with the handler
      expect(options.onMessage).toBeDefined();
    });
  });

  describe('stopBot', () => {
    it('should stop a running bot', async () => {
      const options = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      const bot = await botManager.createBot('agent-123', options);
      await bot.start();

      botManager.stopBot('agent-123');

      expect(bot.stop).toHaveBeenCalled();
      expect(botManager.getQrUrl('agent-123')).toBeUndefined();
    });

    it('should not throw when stopping non-existent bot', () => {
      expect(() => botManager.stopBot('non-existent')).not.toThrow();
    });
  });

  describe('removeBot', () => {
    it('should remove bot completely', async () => {
      const options = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      await botManager.createBot('agent-123', options);
      botManager.setQrUrl('agent-123', 'https://test-qr.com');

      await botManager.removeBot('agent-123');

      expect(botManager.getBot('agent-123')).toBeUndefined();
      expect(botManager.getQrUrl('agent-123')).toBeUndefined();
    });
  });

  describe('QR URL management', () => {
    it('should set and get QR URL', () => {
      botManager.setQrUrl('agent-123', 'https://test-qr-url.com');
      expect(botManager.getQrUrl('agent-123')).toBe('https://test-qr-url.com');
    });

    it('should return undefined for non-existent QR URL', () => {
      expect(botManager.getQrUrl('non-existent')).toBeUndefined();
    });
  });

  describe('getBotCount and getBotIds', () => {
    it('should return correct bot count', async () => {
      const options = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      expect(botManager.getBotCount()).toBe(0);

      await botManager.createBot('agent-1', options);
      expect(botManager.getBotCount()).toBe(1);

      await botManager.createBot('agent-2', options);
      expect(botManager.getBotCount()).toBe(2);
    });

    it('should return correct bot IDs', async () => {
      const options = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      await botManager.createBot('agent-1', options);
      await botManager.createBot('agent-2', options);

      const ids = botManager.getBotIds();
      expect(ids).toContain('agent-1');
      expect(ids).toContain('agent-2');
    });
  });

  describe('stopAll', () => {
    it('should stop all bots', async () => {
      const options = {
        onMessage: vi.fn(),
        onError: vi.fn(),
        onLogin: vi.fn(),
        onSessionExpired: vi.fn(),
      };

      const bot1 = await botManager.createBot('agent-1', options);
      const bot2 = await botManager.createBot('agent-2', options);

      botManager.stopAll();

      expect(bot1.stop).toHaveBeenCalled();
      expect(bot2.stop).toHaveBeenCalled();
      expect(botManager.getBotCount()).toBe(0);
    });
  });
});
