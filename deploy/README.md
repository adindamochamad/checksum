# Deploying Checksum to a VPS

Everything runs in two containers: the app, and Caddy in front of it for TLS.

## What the box needs

- Docker with the Compose plugin
- Ports 80 and 443 open
- ~1 GB RAM (measured: 181 MiB idle, headroom is for concurrent verifications)
- Ideally near `ap-southeast-1`. Each verification makes nine round trips to
  ClickHouse, so distance is paid nine times over.

## Steps

```bash
# 1. Get the code onto the box
git clone <repo-url> && cd checksum/deploy

# 2. Credentials. Never commit this file.
cp ../.env.example .env
$EDITOR .env          # GOOGLE_API_KEY, CLICKHOUSE_HOST/USER/PASSWORD

# 3. Say where it lives.
#    With a domain pointed at this box:
echo "DOMAIN=checksum.example.com" >> .env
#    Without a domain, sslip.io resolves a dashed IP to itself, which is enough
#    for Let's Encrypt to issue a real certificate:
echo "DOMAIN=203-0-113-7.sslip.io" >> .env

# 4. Up
docker compose up -d --build
docker compose logs -f app        # first boot resolves the catalog, ~20s
```

Then confirm from your laptop:

```bash
curl https://$DOMAIN/api/health
```

`"ready": true` means the MCP session opened, the policy loaded and the catalog
resolved.

## Warm the cache before judging

The free Gemini tier allows twenty requests per day. Judging runs for two weeks on
a public link, so preset questions are answered from a verification that already
ran. Warm them once:

```bash
docker compose exec app python -c "
import asyncio
from checksum.api import lifespan, warm, app
async def main():
    async with lifespan(app):
        await warm()
asyncio.run(main())
"
```

The cache lives in a named volume, so it survives a rebuild. `/api/health` lists
what is cached.

## Verify the deployment the same way everything else here was verified

From your laptop, against the live URL:

```bash
CHECKSUM_QA_URL=https://$DOMAIN .venv/bin/python scripts/qa.py web
```

Twelve checks, driving the real page in Chromium.

## Keeping it alive through judging

Judging runs to 7 October. `restart: unless-stopped` covers reboots. What it does
not cover is the ClickHouse Cloud service idling — check that idling is disabled
there before 23 September, or the first judge pays a cold start.
