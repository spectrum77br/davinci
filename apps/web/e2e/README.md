# DaVinci E2E (Playwright)

Smoke specs for the four golden flows called out in PRD Phase 13:
- login (email-OTP)
- import product
- sync
- push de preço

Specs default to `pnpm dev` on `:3000`. Override via env:
- `E2E_BASE_URL=https://staging.example.com pnpm test:e2e` (skips webServer)
- `E2E_NO_WEBSERVER=1 pnpm test:e2e` (you bring the server)

## First-time setup

```bash
pnpm install
pnpm exec playwright install chromium
```

## Auth fixture

The `authed` fixture in `fixtures/auth.ts` reuses a pre-issued JWT cookie.
Set the env vars below — never commit them:

```
E2E_USER_EMAIL=test+e2e@example.com
E2E_SESSION_COOKIE=<JWT cookie value>
```

Use the `/api/auth/dev/issue` route on a non-prod build, or grab a real
session cookie after a manual login.
