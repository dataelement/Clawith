#!/bin/sh
# Obtain certificate if missing, then renew periodically. On renewal, touch reload-requested so nginx reloads.

set -e
DOMAIN="${DOMAIN:-}"
EMAIL="${LETSENCRYPT_EMAIL:-}"
WEBROOT="/var/www/certbot"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "[certbot] DOMAIN and LETSENCRYPT_EMAIL must be set. Exiting."
    exit 0
fi

obtain() {
    certbot certonly --webroot -w "$WEBROOT" \
        -d "$DOMAIN" \
        --email "$EMAIL" \
        --agree-tos \
        --non-interactive \
        --keep-until-expiring 2>/dev/null || true
}

renew() {
    certbot renew --webroot -w "$WEBROOT" \
        --deploy-hook "touch /etc/letsencrypt/reload-requested"
}

# Wait for nginx to be serving ACME challenge
echo "[certbot] Waiting for nginx..."
sleep 10

# Initial certificate (or refresh if self-signed was used)
echo "[certbot] Obtaining certificate for $DOMAIN..."
obtain
touch /etc/letsencrypt/reload-requested

# Renew every 12 hours; deploy-hook triggers nginx reload
echo "[certbot] Starting renewal loop (every 12h)..."
while true; do
    sleep 43200
    renew
done
