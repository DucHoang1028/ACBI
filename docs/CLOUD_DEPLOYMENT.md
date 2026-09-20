# Oracle Cloud deployment

Status: configuration prepared; no cloud server or public demo has been deployed.

This profile follows proposal Sections 7 and 9.6: exactly three ACBI containers
(web, backend, application PostgreSQL), persistent application storage, an
internal-only application database, HTTPS, and an encrypted read-only connection
to the separately administered AdventureWorks warehouse. Caddy serves the same
React build and handles HTTPS in the web container. Groq remains external.

## Free hosting prerequisites

The owner must create an Oracle Cloud account and obtain an Always Free-eligible
VM. Verify the account's current free allowance and the console's estimated cost
before provisioning; trial credits are not permanent free hosting. Capacity can
be unavailable and idle free VMs can be reclaimed. Do not upgrade to a paid plan
or provision paid resources for this demo.

Use a supported Linux VM with Docker Engine and Compose 2.24.4 or later. A public
DNS hostname must resolve to the VM. A free DNS subdomain is sufficient if the
owner controls its record. Allow inbound TCP 80 and 443 for the web service and
restrict SSH to the administrator's address. Do not expose 5432 or 8000 publicly.

The existing Windows warehouse is not reachable from Oracle through
`host.docker.internal`. Before deployment, provision an independently managed
PostgreSQL warehouse reachable from the VM over a private connection. Restore an
admin-created AdventureWorks database dump if a cloud copy is needed; never copy
or modify the original reference folder and never commit the dump. Keep this
warehouse outside ACBI Compose. Configure TLS with a certificate whose hostname
matches `WAREHOUSE_HOST`, and provide the issuing CA certificate to ACBI.

An administrator must verify the database name with `\l`, install
`data/warehouse_extensions.sql`, and grant `acbi_ro` the required SELECT access.
Keep `default_transaction_read_only=on` and `statement_timeout=15s`. Application
configuration must never use the warehouse administrator account.

## Start the cloud profile

Copy `deploy/.env.example` to private `deploy/.env` on the server, assign new
random database passwords, and configure the actual external warehouse address.
Add `PUBLIC_HOST` (hostname only) and `WAREHOUSE_CA_FILE` (absolute certificate
path). Set `WAREHOUSE_SSLMODE=verify-full`. Set the Groq key only in this private
file. Enable only the approved metadata context; keep result-row export disabled.
Protect this file with `chmod 600 deploy/.env`.

```sh
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.cloud.yml up -d --build --wait
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.cloud.yml exec -T backend python scripts/seed_users.py
umask 077
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.cloud.yml cp backend:/tmp/acbi-seed-credentials.txt deploy/seed-credentials.txt
```

The seed script writes passwords only for newly created accounts to a private
container file; the copy command saves them privately on the host. Give demo credentials to
intended viewers privately. Never publish the administrator password in GitHub.

The cloud override must be used without the Windows-only local network override.
It forces certificate verification even if a copied environment disables TLS.
Only the web server publishes ports. The backend trusts forwarded HTTPS headers
from this private web proxy, enabling secure refresh cookies and origin checks.
Do not attach untrusted containers to its network or publish the backend port.

## Verify before announcing a demo URL

1. Check all three ACBI containers are healthy and the warehouse remains external.
2. Verify the browser's certificate, HTTP-to-HTTPS redirect, login, Secure refresh
   cookie, session refresh, and logout at `https://PUBLIC_HOST`.
3. Run the existing verification scripts against the restored data with FakeLLM,
   then a bounded live Groq question. Confirm startup MIN/MAX, row counts, and
   DATA_AS_OF match the intended dataset.
4. Confirm permission denials, history ownership, and private database ports.
5. Configure separate protected backups of application PostgreSQL and warehouse
   data; certificate state persists in the Caddy volumes. Test restoration before
   relying on saved history.
6. Add the verified HTTPS URL to GitHub's website field and README only after
   these checks pass.

References: [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[Caddy HTTPS](https://caddyserver.com/docs/automatic-https).
