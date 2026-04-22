import { describe, expect, it } from 'vitest';

import { mockJsonResponse } from '../helpers/api';
import { authApi } from '../../src/services/api';

describe('authApi', () => {
  it('sends login requests through the API service layer', async () => {
    const fetchSpy = mockJsonResponse({
      access_token: 'token',
      needs_company_setup: false,
      user: {
        id: 'user-1',
        username: 'demo',
      },
    });

    const response = await authApi.login({
      username: 'demo',
      password: 'password123',
    });

    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
        }),
      }),
    );
    expect(response.access_token).toBe('token');
  });
});
