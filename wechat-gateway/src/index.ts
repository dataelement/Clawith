/**
 * Clawith WeChat Gateway Service
 *
 * A Node.js microservice that manages WeChat iLink bot connections
 * using the @wechatbot/wechatbot SDK. It acts as a bridge between
 * WeChat's iLink protocol and the Clawith Python backend.
 *
 * Architecture:
 *   WeChat iLink API <--> Node.js Gateway (this service) <--> Python Backend
 *
 * Features:
 * - Multi-bot management (one bot per agent)
 * - QR code login flow
 * - Message forwarding to Python backend for LLM processing
 * - Automatic session recovery
 */

import express, { Request, Response } from 'express';
import { WeChatBot, type IncomingMessage, type Credentials, stripMarkdown } from '@wechatbot/wechatbot';
import { v4 as uuidv4 } from 'uuid';
import { BotManager } from './bot-manager.js';
import { BackendClient } from './backend-client.js';

const PORT = process.env.PORT || 3100;
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const API_PREFIX = process.env.API_PREFIX || '/api';

// ── Initialize components ────────────────────────────────────────────────

const app = express();
app.use(express.json());

const botManager = new BotManager();
const backendClient = new BackendClient(BACKEND_URL, API_PREFIX);

// ── Health check ──────────────────────────────────────────────────────────

app.get('/health', (_req: Request, res: Response) => {
  res.json({
    status: 'ok',
    bots: botManager.getBotCount(),
    uptime: process.uptime(),
  });
});

// ═══════════════════════════════════════════════════════════════════════
// Bot Management API
// ═══════════════════════════════════════════════════════════════════════

/**
 * POST /bots/:agentId/login
 * Initiate QR login for an agent's WeChat bot.
 * Returns the QR code URL that needs to be scanned.
 *
 * Note: This endpoint initiates the login process and returns immediately
 * with the QR URL. The actual login happens asynchronously after the user
 * scans the QR code.
 */
app.post('/bots/:agentId/login', async (req: Request, res: Response) => {
  const { agentId } = req.params;
  const { storage_dir, force } = req.body;

  console.log(`[Gateway] Login request for agent ${agentId}, force=${force}`);

  try {
    // Check if bot already exists and is logged in AND running
    const existingBot = botManager.getBot(agentId);
    if (existingBot?.getCredentials() && existingBot.isRunning && !force) {
      return res.json({
        qr_url: null,
        message: 'Already logged in and running',
        is_logged_in: true,
        is_running: true,
      });
    }

    // If force=true and bot has credentials, clear them first to force QR login
    if (force && existingBot) {
      try {
        // Clear stored credentials to force new QR login
        const fs = await import('fs/promises');
        const storageDir = storage_dir || `/data/wechat-${agentId}`;
        const credsFile = `${storageDir}/credentials.json`;
        await fs.unlink(credsFile).catch(() => {}); // Ignore if file doesn't exist
        console.log(`[Gateway] Cleared credentials for ${agentId} (force=true)`);
      } catch (e) {
        console.log(`[Gateway] Could not clear credentials: ${e}`);
      }
    }

    // If bot exists and we need to recreate, log it
    if (existingBot) {
      console.log(`[Gateway] Will recreate bot for ${agentId} (wasRunning: ${existingBot.isRunning})`);
    }

    // QR URL will be set via callback
    let qrUrlResolved = false;
    let resolveQrUrl: (url: string) => void;
    const qrUrlPromise = new Promise<string>((resolve) => {
      resolveQrUrl = resolve;
    });

    // Create new bot instance (createBot will stop existing bot internally)
    const bot = await botManager.createBot(agentId, {
      storageDir: storage_dir,
      onMessage: async (msg) => handleMessage(agentId, msg),
      onError: (err) => console.error(`[Gateway] Bot ${agentId} error:`, err),
      onLogin: (creds) => {
        console.log(`[Gateway] Bot ${agentId} logged in successfully`);
        // Start the bot after successful login
        bot.start().catch((e) => console.error(`[Gateway] Failed to start bot ${agentId}:`, e));
      },
      onSessionExpired: () => console.log(`[Gateway] Bot ${agentId} session expired`),
    });

    // Initiate login asynchronously (don't await - we need to return QR URL first)
    bot.login({
      callbacks: {
        onQrUrl: (url: string) => {
          console.log(`[Gateway] QR generated for ${agentId}: ${url.substring(0, 60)}...`);
          botManager.setQrUrl(agentId, url);
          if (!qrUrlResolved) {
            qrUrlResolved = true;
            resolveQrUrl(url);
          }
        },
        onScanned: () => console.log(`[Gateway] QR scanned for ${agentId}`),
      },
    }).then((creds) => {
      if (creds) {
        console.log(`[Gateway] Login completed for ${agentId}`);
        // If login succeeded without QR (using stored creds), resolve with empty to indicate success
        if (!qrUrlResolved) {
          qrUrlResolved = true;
          resolveQrUrl('ALREADY_LOGGED_IN');
        }
      }
    }).catch((err) => {
      console.error(`[Gateway] Login error for ${agentId}:`, err);
      // Resolve with empty string if login fails
      if (!qrUrlResolved) {
        qrUrlResolved = true;
        resolveQrUrl('');
      }
    });

    // Wait for QR URL with timeout (15 seconds for QR generation)
    const timeout = setTimeout(() => {
      if (!qrUrlResolved) {
        console.log(`[Gateway] QR URL timeout for ${agentId}`);
        qrUrlResolved = true;
        resolveQrUrl('');
      }
    }, 15000);

    const qrUrl = await qrUrlPromise;
    clearTimeout(timeout);

    // Handle different outcomes
    if (qrUrl === 'ALREADY_LOGGED_IN') {
      // Login succeeded using stored credentials (no QR needed)
      return res.json({
        qr_url: null,
        message: 'Already logged in with stored credentials',
        is_logged_in: true,
        is_running: bot.isRunning,
      });
    }

    if (!qrUrl) {
      return res.status(500).json({ error: 'Failed to generate QR code. Please try again.' });
    }

    res.json({
      qr_url: qrUrl,
      message: 'Login initiated - scan QR code with WeChat',
      is_logged_in: false,
    });
  } catch (error) {
    console.error(`[Gateway] Login error for ${agentId}:`, error);
    res.status(500).json({ error: String(error) });
  }
});

/**
 * GET /bots/:agentId/qr
 * Get the current QR code URL for an agent's login flow.
 */
app.get('/bots/:agentId/qr', (req: Request, res: Response) => {
  const { agentId } = req.params;
  const qrUrl = botManager.getQrUrl(agentId);

  if (!qrUrl) {
    return res.status(404).json({ error: 'No QR code available' });
  }

  res.json({ qr_url: qrUrl });
});

/**
 * POST /bots/:agentId/start
 * Start the WeChat bot for an agent (if credentials exist).
 */
app.post('/bots/:agentId/start', async (req: Request, res: Response) => {
  const { agentId } = req.params;
  const { storage_dir } = req.body;

  console.log(`[Gateway] Start request for agent ${agentId}`);

  try {
    // Check if bot exists
    let bot = botManager.getBot(agentId);

    if (!bot) {
      // Create bot (will attempt to use stored credentials)
      bot = await botManager.createBot(agentId, {
        storageDir: storage_dir,
        onMessage: (msg) => handleMessage(agentId, msg),
        onError: (err) => console.error(`[Gateway] Bot ${agentId} error:`, err),
        onLogin: (creds) => console.log(`[Gateway] Bot ${agentId} logged in`),
        onSessionExpired: () => console.log(`[Gateway] Bot ${agentId} session expired`),
      });

      // Try to login (will use stored credentials if available)
      try {
        await bot.login();
      } catch {
        return res.status(400).json({
          error: 'No stored credentials. Please login via QR first.',
          needs_login: true,
        });
      }
    }

    // Start polling
    if (!bot.isRunning) {
      await bot.start();
    }

    res.json({ status: 'started', is_running: bot.isRunning });
  } catch (error) {
    console.error(`[Gateway] Start error for ${agentId}:`, error);
    res.status(500).json({ error: String(error) });
  }
});

/**
 * POST /bots/:agentId/stop
 * Stop the WeChat bot for an agent.
 */
app.post('/bots/:agentId/stop', (req: Request, res: Response) => {
  const { agentId } = req.params;

  console.log(`[Gateway] Stop request for agent ${agentId}`);

  try {
    botManager.stopBot(agentId);
    res.json({ status: 'stopped' });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

/**
 * GET /bots/:agentId/status
 * Get the connection status of an agent's WeChat bot.
 */
app.get('/bots/:agentId/status', (req: Request, res: Response) => {
  const { agentId } = req.params;
  const bot = botManager.getBot(agentId);

  if (!bot) {
    return res.json({
      is_running: false,
      is_logged_in: false,
      qr_url: botManager.getQrUrl(agentId),
    });
  }

  res.json({
    is_running: bot.isRunning,
    is_logged_in: !!bot.getCredentials(),
    qr_url: botManager.getQrUrl(agentId),
  });
});

/**
 * POST /bots/:agentId/send
 * Send a message to a WeChat user (proactive messaging).
 */
app.post('/bots/:agentId/send', async (req: Request, res: Response) => {
  const { agentId } = req.params;
  const { user_id, text } = req.body;

  if (!user_id || !text) {
    return res.status(400).json({ error: 'Missing user_id or text' });
  }

  const bot = botManager.getBot(agentId);
  if (!bot) {
    return res.status(404).json({ error: 'Bot not found' });
  }

  try {
    // Strip markdown formatting since WeChat doesn't render markdown
    const plainText = stripMarkdown(text);
    await bot.send(user_id, plainText);
    res.json({ status: 'sent' });
  } catch (error) {
    console.error(`[Gateway] Send error for ${agentId}:`, error);
    res.status(500).json({ error: String(error) });
  }
});

/**
 * POST /bots/:agentId/send-file
 * Send a file to a WeChat user.
 *
 * Request body:
 * - user_id: WeChat user ID to send to
 * - file_data: Base64-encoded file content
 * - file_name: Name of the file (used for type detection)
 * - caption: Optional caption for the file
 */
app.post('/bots/:agentId/send-file', async (req: Request, res: Response) => {
  const { agentId } = req.params;
  const { user_id, file_data, file_name, caption } = req.body;

  if (!user_id || !file_data || !file_name) {
    return res.status(400).json({ error: 'Missing user_id, file_data, or file_name' });
  }

  const bot = botManager.getBot(agentId);
  if (!bot) {
    return res.status(404).json({ error: 'Bot not found' });
  }

  try {
    // Decode base64 file data
    const fileBuffer = Buffer.from(file_data, 'base64');

    // Detect media type from file extension
    const ext = file_name.toLowerCase().split('.').pop() || '';
    const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'heic', 'heif'];
    const videoExts = ['mp4', 'mov', 'avi', 'mkv', 'webm', '3gp', 'm4v'];

    if (imageExts.includes(ext)) {
      await bot.send(user_id, { image: fileBuffer, caption });
    } else if (videoExts.includes(ext)) {
      await bot.send(user_id, { video: fileBuffer, caption });
    } else {
      await bot.send(user_id, { file: fileBuffer, fileName: file_name, caption });
    }

    console.log(`[Gateway] File sent to ${user_id}: ${file_name}`);
    res.json({ status: 'sent' });
  } catch (error) {
    console.error(`[Gateway] Send file error for ${agentId}:`, error);
    res.status(500).json({ error: String(error) });
  }
});

/**
 * DELETE /bots/:agentId
 * Remove a bot instance completely (stops and clears credentials).
 */
app.delete('/bots/:agentId', async (req: Request, res: Response) => {
  const { agentId } = req.params;

  console.log(`[Gateway] Delete request for agent ${agentId}`);

  try {
    await botManager.removeBot(agentId);
    res.json({ status: 'deleted' });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ═══════════════════════════════════════════════════════════════════════
// Message Handler
// ═══════════════════════════════════════════════════════════════════════

/**
 * Detect media type from file extension.
 */
function detectMediaType(fileName: string): 'image' | 'video' | 'file' {
  const ext = fileName.toLowerCase().split('.').pop() || '';
  if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'heic', 'heif'].includes(ext)) {
    return 'image';
  }
  if (['mp4', 'mov', 'avi', 'mkv', 'webm', '3gp', 'm4v'].includes(ext)) {
    return 'video';
  }
  return 'file';
}

/**
 * Handle incoming WeChat message by forwarding to Python backend.
 */
async function handleMessage(agentId: string, msg: IncomingMessage): Promise<void> {
  console.log(`[Gateway] Message from ${msg.userId}: ${msg.text?.substring(0, 50)}...`);

  try {
    // Send typing indicator
    const bot = botManager.getBot(agentId);
    if (bot) {
      await bot.sendTyping(msg.userId);
    }

    // Forward to Python backend for LLM processing
    const response = await backendClient.sendMessage(agentId, {
      user_id: msg.userId,
      user_name: '', // WeChat doesn't provide user names in messages
      text: msg.text || '',
      message_type: msg.type,
      is_group: false, // TODO: detect group messages
      group_id: '',
      timestamp: msg.timestamp.toISOString(),
    });

    if (!bot) return;

    // Send file attachments first (if any)
    if (response.files && response.files.length > 0) {
      for (const file of response.files) {
        try {
          const fileName = file.file_name || file.path.split('/').pop() || 'file';
          const mediaType = file.type || detectMediaType(fileName);

          console.log(`[Gateway] Sending file to ${msg.userId}: ${fileName} (${mediaType})`);

          // Download file from backend
          const fileBuffer = await backendClient.downloadFile(agentId, file.path);

          // Send via SDK based on media type
          if (mediaType === 'image') {
            await bot.reply(msg, { image: fileBuffer, caption: file.caption });
          } else if (mediaType === 'video') {
            await bot.reply(msg, { video: fileBuffer, caption: file.caption });
          } else {
            await bot.reply(msg, { file: fileBuffer, fileName, caption: file.caption });
          }

          console.log(`[Gateway] File sent successfully: ${fileName}`);
        } catch (fileError) {
          console.error(`[Gateway] Failed to send file ${file.path}:`, fileError);
          // Continue with other files
        }
      }
    }

    // Send text reply (if any)
    if (response.reply) {
      // Strip markdown formatting since WeChat doesn't render markdown
      const plainText = stripMarkdown(response.reply);
      await bot.reply(msg, plainText);
    }
  } catch (error) {
    console.error(`[Gateway] Error handling message for ${agentId}:`, error);

    // Send error reply
    const bot = botManager.getBot(agentId);
    if (bot) {
      try {
        const errorReply = stripMarkdown('处理消息时发生错误，请稍后重试。');
        await bot.reply(msg, errorReply);
      } catch {
        // Ignore send errors
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════
// Startup
// ═══════════════════════════════════════════════════════════════════════

app.listen(PORT, () => {
  console.log(`[Gateway] Clawith WeChat Gateway running on port ${PORT}`);
  console.log(`[Gateway] Backend URL: ${BACKEND_URL}`);
  console.log(`[Gateway] Node.js version: ${process.version}`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('[Gateway] SIGTERM received, shutting down...');
  botManager.stopAll();
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('[Gateway] SIGINT received, shutting down...');
  botManager.stopAll();
  process.exit(0);
});
