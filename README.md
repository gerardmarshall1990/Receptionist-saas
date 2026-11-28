# Front Desk

AI outbound sales engine + AI receptionist product. See the build spec for the full vision; this repo is built in the spec's Part 3 order.

> **Also in this repo:** [`granny-site-finder/`](granny-site-finder/README.md), a separate Python tool for screening Auckland house + 2-unit sites. It is unrelated to Front Desk.

## Status

| Area | State |
|---|---|
| Multi-tenant schema + row-level security | Done, tested |
| Passwordless login (one-time code, sessions, logout) | Done, tested. Code delivery is a **dev console stub**; SMS/email delivery is not built |
| Niche knowledge base (swappable JSON in `knowledge/niches`) | Done: `plumber`, `hvac` only |
| Account-creation API `POST /v1/accounts` | Done, tested with a billing/voice **test double** |
| Receptionist read/edit, calls list, test-call endpoint | Done, tested |
| Stripe trial (`apps/api/src/providers/stripe.ts`) | Written, **never run against real Stripe**. Webhook handling not built |
| Retell voice (`providers/retell.ts`) | **Not implemented.** Throws, so accounts report `voice: "pending"` |
| Everything else (outbound call engine, optimization loop, Command Center, mobile app, marketing site, calendar, phone porting, Message Manager) | Not started |

## Run

```
cp .env.example .env   # then export the vars, or set them in your shell
npm install
npm run migrate
npm run dev
npm test               # needs Postgres; see TEST_DATABASE_URL
```

## Security notes

- Tenant id comes only from the verified session. Request schemas are `.strict()`, so a client-sent `organizationId` is a 400.
- Postgres RLS (`FORCE`) backs up app-level scoping. The app DB role must not be a superuser or it bypasses RLS.
- Card data never reaches the API: only a Stripe `pm_...` id is accepted.
- Needs a second review before real customer data: RLS policies, the `app.system` bypass, and the Stripe flow.

## Open questions from the spec (unresolved)

Carrier forwarding automation, DNC/consent per country, call-recording consent, retention, lead dedup, SMS consent, grading taxonomy.
