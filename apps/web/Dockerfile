FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
COPY packages/shared/package.json ./packages/shared/package.json
COPY apps/web/package.json ./apps/web/package.json
RUN npm ci

COPY packages/shared ./packages/shared
COPY apps/web ./apps/web

WORKDIR /app/apps/web

ENV NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]
