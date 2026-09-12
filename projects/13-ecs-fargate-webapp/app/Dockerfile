# syntax=docker/dockerfile:1

FROM node:20-alpine

# Build metadata surfaces on /version, so you can see which fleet answered
# during a blue/green traffic shift.
ARG IMAGE_TAG=local
ARG GIT_SHA=unknown
ARG BUILT_AT=unknown
ENV IMAGE_TAG=$IMAGE_TAG GIT_SHA=$GIT_SHA BUILT_AT=$BUILT_AT NODE_ENV=production PORT=8080

WORKDIR /app

RUN apk add --no-cache tini && addgroup -S app && adduser -S app -G app

COPY --chown=app:app package*.json ./
# No runtime dependencies — the app uses only the Node standard library.
COPY --chown=app:app src ./src

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "require('http').get('http://127.0.0.1:'+(process.env.PORT||8080)+'/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"

# tini makes PID 1 forward SIGTERM, so graceful shutdown actually runs.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "src/server.js"]
