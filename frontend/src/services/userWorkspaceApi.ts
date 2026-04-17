const API_BASE = '/api';

export interface UserWorkspaceFile {
  name: string;
  path: string;
  size: number;
}

export interface UserWorkspaceDirectory {
  name: string;
  path: string;
}

export interface UserWorkspaceListResponse {
  files: UserWorkspaceFile[];
  directories: UserWorkspaceDirectory[];
  current_path: string;
}

export interface UserWorkspaceUser {
  id: string;
  display_name: string;
  avatar_url: string | null;
}

export interface UserWorkspaceListUsersResponse {
  users: UserWorkspaceUser[];
}

export interface UserWorkspaceMemoryResponse {
  content: string;
}

export const userWorkspaceApi = {
  /**
   * List all users who have interacted with an agent
   */
  async listUsers(agentId: string): Promise<UserWorkspaceListUsersResponse> {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api/agents/${agentId}/user-workspaces/users`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to list users');
    }
    return res.json();
  },

  /**
   * List files in a user's workspace
   */
  async listFiles(
    agentId: string,
    userId: string,
    path: string = ''
  ): Promise<UserWorkspaceListResponse> {
    const token = localStorage.getItem('token');
    const queryParams = path ? `?path=${encodeURIComponent(path)}` : '';
    const res = await fetch(
      `/api/agents/${agentId}/user-workspaces/users/${userId}/files${queryParams}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    );
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to list files');
    }
    return res.json();
  },

  /**
   * Read a file in user's workspace
   */
  async readFile(
    agentId: string,
    userId: string,
    path: string
  ): Promise<string> {
    const token = localStorage.getItem('token');
    const res = await fetch(
      `/api/agents/${agentId}/user-workspaces/users/${userId}/files/read?path=${encodeURIComponent(path)}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    );
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to read file');
    }
    const data = await res.json();
    return data.content;
  },

  /**
   * Write a file in user's workspace
   */
  async writeFile(
    agentId: string,
    userId: string,
    path: string,
    content: string
  ): Promise<void> {
    const token = localStorage.getItem('token');
    const res = await fetch(
      `/api/agents/${agentId}/user-workspaces/users/${userId}/files/write?path=${encodeURIComponent(path)}`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to write file');
    }
  },

  /**
   * Delete a file in user's workspace
   */
  async deleteFile(
    agentId: string,
    userId: string,
    path: string
  ): Promise<void> {
    const token = localStorage.getItem('token');
    const res = await fetch(
      `/api/agents/${agentId}/user-workspaces/users/${userId}/files/delete?path=${encodeURIComponent(path)}`,
      {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    );
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to delete file');
    }
  },

  /**
   * Upload a file to user's workspace
   */
  async uploadFile(
    agentId: string,
    userId: string,
    file: File,
    path: string,
    onProgress?: (progress: number) => void
  ): Promise<void> {
    const token = localStorage.getItem('token');
    const formData = new FormData();
    formData.append('file', file);
    formData.append('path', path);

    const res = await fetch(
      `/api/agents/${agentId}/user-workspaces/users/${userId}/files/upload`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      }
    );
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to upload file');
    }
  },

  /**
   * Get download URL for a file
   */
  getDownloadUrl(
    agentId: string,
    userId: string,
    path: string
  ): string {
    const token = localStorage.getItem('token');
    return `/api/agents/${agentId}/user-workspaces/users/${userId}/files/download?path=${encodeURIComponent(path)}&token=${token}`;
  },

  /**
   * Get user's memory
   */
  async getMemory(
    agentId: string,
    userId: string
  ): Promise<UserWorkspaceMemoryResponse> {
    const token = localStorage.getItem('token');
    const res = await fetch(
      `/api/agents/${agentId}/user-workspaces/users/${userId}/memory`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    );
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to get memory');
    }
    return res.json();
  },

  /**
   * Update user's memory
   */
  async updateMemory(
    agentId: string,
    userId: string,
    content: string
  ): Promise<void> {
    const token = localStorage.getItem('token');
    const res = await fetch(
      `/api/agents/${agentId}/user-workspaces/users/${userId}/memory`,
      {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to update memory');
    }
  },
};
