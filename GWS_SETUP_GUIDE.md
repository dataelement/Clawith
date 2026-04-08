# Google Workspace Integration Setup Guide

## Prerequisites

1. A [Google Cloud project](https://console.cloud.google.com/) with billing enabled
2. The following APIs enabled in your GCP project:
   - **Google Workspace Events API**
   - **Google Calendar API**
   - **Google Gmail API**
   - **Google Drive API**
3. A running Clawith instance

---

## Option A: Local Development (Desktop App Client)

Google requires HTTPS for "Web application" OAuth clients, which breaks local development. Use a **"Desktop app"** client type instead — it accepts `http://localhost` redirect URIs.

### Step 1: Create OAuth Client in Google Cloud Console

1. Go to [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **+ CREATE CREDENTIALS** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `Clawith Local Dev`
5. Click **Create**
6. Copy the **Client ID** and **Client Secret**

### Step 2: Configure Clawith

In your `.env` file:

```bash
GWS_OAUTH_REDIRECT_URI=http://localhost:8008/api/gws/auth/callback
```

### Step 3: Enter Credentials in Clawith UI

1. Open Clawith → **Enterprise Settings** → **Google Workspace**
2. Paste the **Client ID** and **Client Secret**
3. Save

### Step 4: Connect an Agent

1. Go to an agent's profile
2. Click **Connect Google Workspace**
3. Authorize in the Google popup
4. The agent can now use Google Calendar, Gmail, and Drive

---

## Option B: Production (Web Application Client)

### Step 1: Create OAuth Client in Google Cloud Console

1. Go to [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **+ CREATE CREDENTIALS** → **OAuth client ID**
3. Application type: **Web application**
4. Name: `Clawith Production`
5. **Authorized redirect URIs**: add `https://your-domain.com/api/gws/auth/callback`
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

### Step 2: Configure Clawith

Leave `GWS_OAUTH_REDIRECT_URI` **empty** in `.env`. The redirect URI is auto-generated from `PUBLIC_BASE_URL` + `GWS_OAUTH_CALLBACK_PATH`.

```bash
PUBLIC_BASE_URL=https://your-domain.com
# GWS_OAUTH_REDIRECT_URI=   (leave empty)
```

### Step 3: Enter Credentials in Clawith UI

Same as Option A — paste Client ID and Client Secret in **Enterprise Settings** → **Google Workspace**.

---

## OAuth Flow

```
User clicks "Connect Google Workspace"
        │
        ▼
Clawith redirects to Google's OAuth consent screen
        │
        ▼
User grants permissions (Calendar, Gmail, Drive)
        │
        ▼
Google redirects back to /api/gws/auth/callback with an auth code
        │
        ▼
Clawith exchanges the code for access + refresh tokens (stored encrypted)
        │
        ▼
Agent can now call Google APIs on behalf of the user
```

---

## Import Skills

After connecting Google Workspace, agents need skills to use it:

1. Go to the agent's **Skills** tab
2. Click **Import Skill**
3. Upload the GWS skill files (or use the built-in skill discovery)

---

## Troubleshooting

### Error 400: `invalid_request`

**Cause**: The `redirect_uri` sent to Google does not match any authorized URI in your OAuth client configuration.

**Fix**:
- **Local dev**: Ensure `GWS_OAUTH_REDIRECT_URI=http://localhost:8008/api/gws/auth/callback` is set in `.env` and you're using a **Desktop app** client.
- **Production**: Ensure `PUBLIC_BASE_URL` matches your domain and the redirect URI is added in Google Cloud Console under **Authorized redirect URIs**.

### Error 400: `redirect_uri_mismatch`

**Cause**: The redirect URI in the OAuth request differs from what's registered in Google Cloud Console.

**Fix**: Compare the `redirect_uri` parameter in the OAuth URL (check browser network tab) with the one in Google Cloud Console. They must match exactly, including protocol (`http` vs `https`) and port.

### Token refresh fails after deployment

**Cause**: Tokens were obtained with a different redirect URI than what's currently configured.

**Fix**: Disconnect and reconnect the Google account to obtain fresh tokens with the correct redirect URI.

### Google shows "This app isn't verified"

**Cause**: Your OAuth consent screen is set to "Testing".

**Fix**: Either add test users in the OAuth consent screen settings, or submit the app for verification (required for apps with >100 users).
