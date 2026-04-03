/**
 * Mock for @wechatbot/wechatbot SDK
 *
 * Used for unit testing without actual WeChat connections.
 */

import { vi } from 'vitest';

export interface MockCredentials {
  userId: string;
  token: string;
}

export interface MockMessage {
  userId: string;
  text: string;
  type: string;
  timestamp: Date;
  reply: vi.Mock;
}

export interface MockBotOptions {
  storage: string;
  storageDir: string;
  logLevel: string;
}

export interface MockBotCallbacks {
  onMessage?: (msg: MockMessage) => void | Promise<void>;
  onError?: (error: Error) => void;
  onLogin?: (creds: MockCredentials) => void;
  onSessionExpired?: () => void;
}

// Store created bots for test assertions
export const createdBots: Map<string, MockBotInstance> = new Map();

export interface MockBotInstance {
  options: MockBotOptions;
  callbacks: MockBotCallbacks;
  isRunning: boolean;
  credentials: MockCredentials | null;
  loginCallbacks: {
    onQrUrl?: (url: string) => void;
    onScanned?: () => void;
  };
  login: vi.Mock;
  start: vi.Mock;
  stop: vi.Mock;
  send: vi.Mock;
  sendTyping: vi.Mock;
  reply: vi.Mock;
  getCredentials: vi.Mock;
  on: vi.Mock;
  onMessage: vi.Mock;
}

/**
 * Create a mock bot instance
 */
function createMockBot(options: MockBotOptions): MockBotInstance {
  const bot: MockBotInstance = {
    options,
    callbacks: {},
    isRunning: false,
    credentials: null,
    loginCallbacks: {},
    login: vi.fn(async (callbacks?: { callbacks: MockBotInstance['loginCallbacks'] }) => {
      if (callbacks?.callbacks) {
        bot.loginCallbacks = callbacks.callbacks;
      }
      // Simulate QR URL generation after a short delay
      if (bot.loginCallbacks.onQrUrl) {
        setTimeout(() => {
          bot.loginCallbacks.onQrUrl!('https://mock-qr-url.example.com/qr/test-qr-id');
        }, 10);
      }
      // Simulate successful login after QR scan
      return new Promise((resolve) => {
        setTimeout(() => {
          bot.credentials = { userId: 'mock-user-id', token: 'mock-token' };
          if (bot.callbacks.onLogin) {
            bot.callbacks.onLogin(bot.credentials);
          }
          resolve(bot.credentials);
        }, 100);
      });
    }),
    start: vi.fn(async () => {
      bot.isRunning = true;
      return Promise.resolve();
    }),
    stop: vi.fn(() => {
      bot.isRunning = false;
    }),
    send: vi.fn(async (_userId: string, _text: string) => {
      return Promise.resolve();
    }),
    sendTyping: vi.fn(async (_userId: string) => {
      return Promise.resolve();
    }),
    reply: vi.fn(async (_msg: MockMessage, _text: string) => {
      return Promise.resolve();
    }),
    getCredentials: vi.fn(() => bot.credentials),
    on: vi.fn((event: string, callback: () => void) => {
      if (event === 'error') bot.callbacks.onError = callback;
      if (event === 'login') bot.callbacks.onLogin = callback;
      if (event === 'session:expired') bot.callbacks.onSessionExpired = callback;
    }),
    onMessage: vi.fn((handler: (msg: MockMessage) => void | Promise<void>) => {
      bot.callbacks.onMessage = handler;
    }),
  };

  // Track created bots
  createdBots.set(options.storageDir, bot);

  return bot;
}

// Export the mock WeChatBot class
export const WeChatBot = vi.fn(createMockBot);

// Reset all mocks between tests
export function resetMockBots(): void {
  createdBots.clear();
  vi.clearAllMocks();
}

// Helper to simulate receiving a message
export function simulateMessage(botDir: string, msg: Partial<MockMessage>): void {
  const bot = createdBots.get(botDir);
  if (bot?.callbacks.onMessage) {
    const fullMsg: MockMessage = {
      userId: msg.userId || 'test-user-id',
      text: msg.text || 'test message',
      type: msg.type || 'text',
      timestamp: msg.timestamp || new Date(),
      reply: vi.fn(),
      ...msg,
    };
    bot.callbacks.onMessage(fullMsg);
  }
}

// Helper to simulate login completion
export function simulateLogin(botDir: string, creds: MockCredentials): void {
  const bot = createdBots.get(botDir);
  if (bot) {
    bot.credentials = creds;
    if (bot.callbacks.onLogin) {
      bot.callbacks.onLogin(creds);
    }
  }
}

// Helper to simulate session expiration
export function simulateSessionExpired(botDir: string): void {
  const bot = createdBots.get(botDir);
  if (bot?.callbacks.onSessionExpired) {
    bot.callbacks.onSessionExpired();
  }
}

// Export types for use in tests
export type { IncomingMessage, Credentials } from '@wechatbot/wechatbot';
