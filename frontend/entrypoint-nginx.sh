#!/bin/sh
set -e

# When DOMAIN and SSL are set: use SSL template and ensure certs exist (self-signed until certbot runs).
# Otherwise: use default HTTP-only config.

if [ -n "$DOMAIN" ] && [ "$ENABLE_SSL" = "true" ]; then
    CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
    if [ ! -f "$CERT_DIR/fullchain.pem" ] || [ ! -f "$CERT_DIR/privkey.pem" ]; then
        echo "[nginx] Creating self-signed cert for $DOMAIN (replace with Certbot for production)"
        mkdir -p "$CERT_DIR"
        openssl req -x509 -nodes -days 7 -newkey rsa:2048 \
            -keyout "$CERT_DIR/privkey.pem" \
            -out "$CERT_DIR/fullchain.pem" \
            -subj "/CN=$DOMAIN"
    fi
    sed "s/__DOMAIN__/$DOMAIN/g" /etc/nginx/templates/nginx.ssl.conf.template > /etc/nginx/conf.d/default.conf
# else: default.conf is already the HTTP-only config from the image
fi

# Start nginx in background so we can run reload watcher
nginx -g "daemon off;" &
NGINX_PID=$!

# When using SSL, watch for certbot renewal reload signal
if [ -n "$DOMAIN" ] && [ "$ENABLE_SSL" = "true" ]; then
    while true; do
        sleep 60
        if [ -f "/etc/letsencrypt/reload-requested" ]; then
            echo "[nginx] Reloading after certificate renewal"
            nginx -s reload 2>/dev/null || true
            rm -f /etc/letsencrypt/reload-requested
        fi
    done
fi
wait $NGINX_PID
