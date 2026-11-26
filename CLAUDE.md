# Front Desk monorepo

- `apps/api/`: TypeScript API for the AI receptionist product (see `README.md` for run/test commands).
- `knowledge/niches/`: per-niche knowledge used by the receptionist and sales calls.
- `leads/`: **Frontdesk leads engine** (Python 3.12). Self-contained, with its own toolchain, database and rules.
  Before working on it, read `leads/CLAUDE.md` and `leads/docs/MASTER-SPEC.md`.

## Where the lead list is (tell the user when they ask)
- USA lead list page (private to the owner; share it from the page's Share menu):
  https://claude.ai/artifact/JvbTF3fXVsjukmnZmMDYZG ("Frontdesk Lead List", pinned in the claude.ai sidebar).
  USA final, 2026-11-22: 83,503 gated leads (export abee15fb).
- Rebuild it with `leads/scripts/build_lead_list.py` from a gated export, then publish to the same URL.
