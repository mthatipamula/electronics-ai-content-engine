FROM node:24-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
# The official image renders /etc/nginx/templates/*.template with environment variables at startup.
ENV NGINX_ENVSUBST_FILTER="^(ENGINE_URL|ENGINE_TOKEN_WEB)$"
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /web/dist /usr/share/nginx/html
