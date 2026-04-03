/**
 * Backend Client
 *
 * HTTP client for communicating with the Clawith Python backend.
 * Forwards WeChat messages to the backend for LLM processing.
 */

export interface MessagePayload {
  user_id: string;
  user_name: string;
  text: string;
  message_type: string;
  is_group: boolean;
  group_id: string;
  timestamp: string;
}

/**
 * File attachment from backend (relative path in agent workspace).
 * Gateway will read the file and send via WeChat SDK.
 */
export interface FileAttachment {
  path: string;           // Relative path in agent workspace, e.g., "workspace/report.pdf"
  file_name?: string;     // Display name (optional, defaults to path basename)
  type?: 'file' | 'image' | 'video';  // Media type (auto-detected if not specified)
  caption?: string;       // Optional caption for image/video
}

export interface MessageResponse {
  reply: string;
  /** File attachments to send along with or instead of text reply */
  files?: FileAttachment[];
}

export class BackendClient {
  private baseUrl: string;
  private apiPrefix: string;

  constructor(baseUrl: string, apiPrefix: string) {
    this.baseUrl = baseUrl;
    this.apiPrefix = apiPrefix;
  }

  /**
   * Send a message to the Python backend for processing.
   */
  async sendMessage(agentId: string, payload: MessagePayload): Promise<MessageResponse> {
    const url = `${this.baseUrl}${this.apiPrefix}/channel/wechat/${agentId}/message`;

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`Backend returned ${response.status}: ${await response.text()}`);
      }

      return await response.json() as MessageResponse;
    } catch (error) {
      console.error(`[BackendClient] Error sending message to ${url}:`, error);
      throw error;
    }
  }

  /**
   * Download a file from the backend (agent workspace).
   * Returns the file content as a Buffer.
   */
  async downloadFile(agentId: string, filePath: string): Promise<Buffer> {
    // Use the files API to download the file
    const url = `${this.baseUrl}${this.apiPrefix}/agents/${agentId}/files/${encodeURIComponent(filePath)}`;

    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Failed to download file: ${response.status}`);
      }

      // Get array buffer and convert to Node.js Buffer
      const arrayBuffer = await response.arrayBuffer();
      return Buffer.from(arrayBuffer);
    } catch (error) {
      console.error(`[BackendClient] Error downloading file ${filePath}:`, error);
      throw error;
    }
  }

  /**
   * Check backend health.
   */
  async healthCheck(): Promise<boolean> {
    try {
      const response = await fetch(`${this.baseUrl}/api/health`, {
        method: 'GET',
      });
      return response.ok;
    } catch {
      return false;
    }
  }
}
