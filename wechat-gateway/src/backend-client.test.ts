/**
 * BackendClient Unit Tests
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { BackendClient, type MessagePayload } from './backend-client.js';

// Mock global fetch
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe('BackendClient', () => {
  let client: BackendClient;

  beforeEach(() => {
    client = new BackendClient('http://localhost:8000', '/api');
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('sendMessage', () => {
    it('should send message to backend and return response', async () => {
      const mockResponse = { reply: 'Hello from AI!' };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockResponse),
      });

      const payload: MessagePayload = {
        user_id: 'test-user-id',
        user_name: 'Test User',
        text: 'Hello',
        message_type: 'text',
        is_group: false,
        group_id: '',
        timestamp: new Date().toISOString(),
      };

      const result = await client.sendMessage('agent-123', payload);

      expect(mockFetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/channel/wechat/agent-123/message',
        expect.objectContaining({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
      );
      expect(result).toEqual(mockResponse);
    });

    it('should throw error when backend returns non-OK status', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: () => Promise.resolve('Internal Server Error'),
      });

      const payload: MessagePayload = {
        user_id: 'test-user-id',
        user_name: 'Test User',
        text: 'Hello',
        message_type: 'text',
        is_group: false,
        group_id: '',
        timestamp: new Date().toISOString(),
      };

      await expect(client.sendMessage('agent-123', payload)).rejects.toThrow();
    });

    it('should throw error when fetch fails', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      const payload: MessagePayload = {
        user_id: 'test-user-id',
        user_name: 'Test User',
        text: 'Hello',
        message_type: 'text',
        is_group: false,
        group_id: '',
        timestamp: new Date().toISOString(),
      };

      await expect(client.sendMessage('agent-123', payload)).rejects.toThrow('Network error');
    });

    it('should use correct API prefix', async () => {
      const customClient = new BackendClient('http://backend:8000', '/v1/api');
      const mockResponse = { reply: 'OK' };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockResponse),
      });

      const payload: MessagePayload = {
        user_id: 'user-id',
        user_name: 'User',
        text: 'Test',
        message_type: 'text',
        is_group: false,
        group_id: '',
        timestamp: new Date().toISOString(),
      };

      await customClient.sendMessage('agent-456', payload);

      expect(mockFetch).toHaveBeenCalledWith(
        'http://backend:8000/v1/api/channel/wechat/agent-456/message',
        expect.any(Object)
      );
    });
  });

  describe('healthCheck', () => {
    it('should return true when backend is healthy', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
      });

      const result = await client.healthCheck();

      expect(result).toBe(true);
      expect(mockFetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/health',
        expect.objectContaining({ method: 'GET' })
      );
    });

    it('should return false when backend is unhealthy', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
      });

      const result = await client.healthCheck();

      expect(result).toBe(false);
    });

    it('should return false when fetch fails', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Connection refused'));

      const result = await client.healthCheck();

      expect(result).toBe(false);
    });
  });
});
