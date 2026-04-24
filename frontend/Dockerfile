# Development target
FROM node:20-alpine AS development
WORKDIR /app
COPY package*.json ./
RUN npm ci --registry https://registry.npmmirror.com
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# Production build target
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --registry https://registry.npmmirror.com
COPY . .
RUN npm run build

FROM nginx:alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
