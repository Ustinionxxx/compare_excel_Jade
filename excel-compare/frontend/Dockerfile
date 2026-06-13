FROM node:20-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
# 使用 npmmirror 镜像源加速 npm 下载
RUN npm config set registry https://registry.npmmirror.com && \
    npm ci

COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
