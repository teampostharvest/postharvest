# deploy/ — host-side deployment artifacts

Everything **non-container** that lives outside the Docker stack.
Container jobs (images, compose files, Dockerfile, nginx configs) live in
`../docker/` — do not put container config here.

Planned contents (populated as the deployment matures):

| Path | Purpose | Status |
|---|---|---|
| `letsencrypt/` | Certbot renewal hooks / one-shot scripts for `postharvest.space` TLS (nginx in the stack terminates TLS; certbot runs on the host) | TODO (waits on DNS at the VPS) |
| `systemd/` | `postharvest.service` unit wrapping the compose command (`docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --profile prod up`) | OPEN (see DECISIONS.md O1) |
| `VPS.md` | Provisioning runbook: install Docker, clone, `.env` setup, first boot | TODO |
| `backup.md` | `pg_dump` cron + `data/` (cookies/exports) backup strategy | TODO |

See [DECISIONS.md](../docs/DECISIONS.md) D8 for the split rationale and O1/O4 for
the open choices before these get written.