# HTTPS Deployment Guide

This guide covers how to enable HTTPS for a self-hosted Clawith deployment. We recommend using a reverse proxy with automatic certificate management rather than modifying Clawith's Docker setup directly.

## Option A: Caddy (Recommended — Simplest)

[Caddy](https://caddyserver.com/) provides automatic HTTPS with zero configuration.

### 1. Install Caddy

```bash
# Ubuntu/Debian
sudo apt install -y caddy

# Or via Docker
docker pull caddy:2
```

### 2. Create a Caddyfile

```
your-domain.com {
    # Frontend
    reverse_proxy localhost:3008

    # Backend API
    handle /api/* {
        reverse_proxy localhost:8000
    }

    # WebSocket
    handle /ws/* {
        reverse_proxy localhost:8000
    }
}
```

### 3. Start Caddy

```bash
sudo caddy start
```

Caddy will automatically obtain and renew Let's Encrypt certificates. No additional configuration needed.

---

## Option B: Traefik (Best for Docker-native setups)

[Traefik](https://traefik.io/) integrates directly with Docker and handles certificates automatically.

### 1. Add Traefik to your `docker-compose.override.yml`

```yaml
services:
  traefik:
    image: traefik:v3.0
    command:
      - "--providers.docker=true"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.tlschallenge=true"
      - "--certificatesresolvers.letsencrypt.acme.email=your-email@example.com"
      - "--certificatesresolvers.letsencrypt.acme.storage=/acme/acme.json"
      - "--entrypoints.web.http.redirections.entryPoint.to=websecure"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik-certs:/acme

  frontend:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.clawith.rule=Host(`your-domain.com`)"
      - "traefik.http.routers.clawith.entrypoints=websecure"
      - "traefik.http.routers.clawith.tls.certresolver=letsencrypt"
      - "traefik.http.services.clawith.loadbalancer.server.port=80"

volumes:
  traefik-certs:
```

### 2. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

---

## Option C: Nginx + Certbot (Traditional)

If you prefer a traditional Nginx setup with Let's Encrypt.

### 1. Install Nginx and Certbot

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 2. Create Nginx config

```nginx
# /etc/nginx/sites-available/clawith
server {
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:3008;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
```

### 3. Enable site and obtain certificate

```bash
sudo ln -s /etc/nginx/sites-available/clawith /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your-domain.com
```

Certbot will automatically modify your Nginx config to add SSL and set up auto-renewal.

---

## Environment Variables

When running behind HTTPS, set these in your `.env`:

```bash
# Tell the backend it's behind a reverse proxy
FORWARDED_ALLOW_IPS=*

# If your Feishu/Slack/Discord webhooks need the public URL
PUBLIC_URL=https://your-domain.com
```

## Notes

- **Do NOT** expose ports 8000 (backend) or 3008 (frontend) directly to the internet when using a reverse proxy. Bind them to `127.0.0.1` only.
- All three options above handle automatic certificate renewal. No manual intervention needed.
- For Cloudflare users: simply point your DNS to the server and enable the Cloudflare proxy — SSL is handled automatically at the edge.
