FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV SCRAPER_API_URL=http://api:8765
RUN npm run build
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
