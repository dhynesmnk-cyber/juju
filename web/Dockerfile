FROM node:22-slim AS build
WORKDIR /app
ARG NEXT_PUBLIC_WORLDS=on
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_WORLDS=$NEXT_PUBLIC_WORLDS
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
