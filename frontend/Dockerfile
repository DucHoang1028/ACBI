FROM node:22-alpine AS build
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend ./
RUN npm run build
FROM caddy:2-alpine AS cloud
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv

FROM nginx:alpine AS local
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
