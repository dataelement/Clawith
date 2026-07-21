FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --registry https://registry.npmmirror.com
COPY . .
RUN npm run build

# nginx 1.31.3 uses a pid-file pwrite path that the CentOS 7 / Docker seccomp
# profile on the 3010 host rejects. Pin the last verified compatible image
# instead of silently widening the container's syscall policy.
FROM nginx:1.31.2-alpine@sha256:54f2a904c251d5a34adf545a72d32515a15e08418dae0266e23be2e18c66fefa
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
