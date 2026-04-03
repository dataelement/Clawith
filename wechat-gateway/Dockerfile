# Clawith WeChat Gateway
# Node.js microservice for WeChat iLink bot connections

FROM node:22-alpine

WORKDIR /app

# Install ALL dependencies (including devDependencies for build)
COPY package*.json ./
RUN npm ci

# Copy source and build
COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run build

# Remove devDependencies to reduce image size
RUN npm prune --production

# Create data directory for credential storage
RUN mkdir -p /data

# Environment defaults
ENV PORT=3100
ENV BACKEND_URL=http://backend:8000
ENV API_PREFIX=/api
ENV NODE_ENV=production

# Expose port
EXPOSE 3100

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:3100/health || exit 1

# Run the service
CMD ["node", "dist/index.js"]
