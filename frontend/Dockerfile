FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --registry https://registry.npmmirror.com
COPY . .
RUN npm run build

FROM nginx:alpine
RUN apk add --no-cache openssl
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx.ssl.conf.template /etc/nginx/templates/nginx.ssl.conf.template
COPY entrypoint-nginx.sh /entrypoint-nginx.sh
RUN chmod +x /entrypoint-nginx.sh
EXPOSE 3000 80 443
ENTRYPOINT ["/entrypoint-nginx.sh"]
