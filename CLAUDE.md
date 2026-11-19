# Front Desk monorepo

- `apps/api/`: TypeScript API for the AI receptionist product (see `README.md` for run/test commands).
- `knowledge/niches/`: per-niche knowledge used by the receptionist and sales calls.
- `leads/`: **Frontdesk leads engine** (Python 3.12). Self-contained, with its own toolchain, database and rules.
  Before working on it, read `leads/CLAUDE.md` and `leads/docs/MASTER-SPEC.md`.
