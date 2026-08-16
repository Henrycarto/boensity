# Build context is the repository root, not apps/web, because the app imports
# the workspace packages as source through transpilePackages.

FROM node:22-alpine AS deps
WORKDIR /repo

COPY package.json package-lock.json* ./
COPY apps/web/package.json ./apps/web/
COPY packages/shared-types/package.json ./packages/shared-types/
COPY packages/fhir-client/package.json ./packages/fhir-client/

RUN npm ci --workspaces --include-workspace-root


FROM node:22-alpine AS builder
WORKDIR /repo

ARG NEXT_PUBLIC_TIMELINE_API_URL
ARG NEXT_PUBLIC_INGEST_API_URL
ARG NEXT_PUBLIC_SYNC_API_URL

# Next inlines NEXT_PUBLIC_* into the client bundle at build time. Passing them
# at runtime instead would ship a browser bundle pointing at the defaults.
ENV NEXT_PUBLIC_TIMELINE_API_URL=$NEXT_PUBLIC_TIMELINE_API_URL \
    NEXT_PUBLIC_INGEST_API_URL=$NEXT_PUBLIC_INGEST_API_URL \
    NEXT_PUBLIC_SYNC_API_URL=$NEXT_PUBLIC_SYNC_API_URL \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /repo/node_modules ./node_modules
COPY package.json turbo.json ./
COPY packages ./packages
COPY apps/web ./apps/web

RUN npm run build --workspace @boensity/web


FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder /repo/apps/web/public ./public
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next ./.next
COPY --from=builder /repo/node_modules ./node_modules
COPY --from=builder /repo/apps/web/package.json ./package.json
COPY --from=builder /repo/packages ./packages

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"

CMD ["npx", "next", "start"]
