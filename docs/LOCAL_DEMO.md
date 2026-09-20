# Free live demo on the owner's PC

The current demo uses a Cloudflare Quick Tunnel to the real ACBI application.
It requires no cloud server or payment card. The PC, Docker Desktop, warehouse,
three ACBI containers, and tunnel process must stay running. A new tunnel gets a
new URL; this is a temporary demonstration, not an uptime-guaranteed deployment.

## Architecture

Cloudflare terminates browser HTTPS and carries requests through an encrypted
tunnel to `cloudflared` on Windows. The tunnel forwards only to loopback port
8080. The existing web container serves React and proxies API requests to the
unpublished FastAPI container. The application PostgreSQL container has no host
port. The AdventureWorks warehouse remains outside ACBI Compose and its reference
folder is unchanged. Groq keys and login passwords stay out of GitHub.

The demo override forces verified warehouse TLS. On 2026-09-20, the existing
warehouse was configured by its administrator with a certificate for
`adventureworks-for-postgres-db-1`, stored in its Docker data volume under
`/var/lib/postgresql/18/docker/acbi-tls/`. The private key stays there with mode
0600. Only the public certificate is copied to ignored
`deploy/certs/warehouse-ca.crt`. PostgreSQL's `ssl`, `ssl_cert_file`, and
`ssl_key_file` were set using `ALTER SYSTEM` and reloaded; no reference-folder
files were edited. The self-signed certificate expires after 365 days and must be
renewed in the warehouse and copied to ACBI before expiry. A different hostname
requires a matching certificate. Recreate the backend after renewal.

Nginx explicitly supplies the HTTPS scheme to the unpublished backend so origin
validation and Secure refresh cookies work. Authentication endpoints have a
shared demo rate limit in addition to account lockout. Do not publish backend
port 8000 or attach untrusted containers to its private network. Access the demo
through its HTTPS URL; localhost HTTP login is intentionally incompatible with
the demo profile's secure cookies.

## Start, inspect, and stop

The official Windows client is stored outside source control at
`.tools/cloudflared/cloudflared.exe`. Version 2026.9.1 was downloaded from
Cloudflare's GitHub release and matched its published SHA-256 digest.

Run from the ACBI project folder:

```powershell
./scripts/demo.ps1 start
./scripts/demo.ps1 status
./scripts/demo.ps1 stop
```

Start applies the base, local, and demo Compose profiles. It requires the existing
private environment file, warehouse certificate, installed tunnel client, and
running Docker Desktop. The tunnel starts hidden; status prints its URL once
available. Stop closes public access without deleting data or stopping Docker.
Private logs and the process ID live in `.tools/demo/`.

After restarting, replace the temporary link in README and GitHub's website field
if present. To return to ordinary localhost HTTP use, stop the tunnel and run
`./scripts/acbi.ps1 up`. Do not run that local command while the public demo is in
use: it removes the demo HTTPS proxy and TLS override.

Login credentials are in private `deploy/seed-credentials.txt`. Share a suitable
non-administrator account only with intended viewers; never publish passwords in
README or GitHub. Saved results remain scoped to their account.

## Verification on 2026-09-20

- All three ACBI containers healthy; warehouse remains external.
- Warehouse session: `acbi_ro`, TLS 1.3, certificate verification enabled.
- Sales dates: 2022-05-30 through 2025-06-29; DATA_AS_OF 2025-06-29.
- HTTPS page, login, Secure/HttpOnly cookie, refresh, and logout passed.
- Cross-origin refresh was denied with HTTP 403.
- A real revenue-by-territory question returned `ok`, used two Groq calls, and
  saved its result. No result rows or credentials were printed in verification.

Reference: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
