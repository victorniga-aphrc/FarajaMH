# Nginx + Certbot in Docker Compose

This folder holds the reverse proxy config and certificates used by the `reverse-proxy` service in `docker-compose.yml`.

## Folder structure

```
deploy/nginx/
├── nginx.conf           # Nginx reverse proxy config
└── certs/               # SSL certificates (Let's Encrypt, etc.)
```

When using Certbot you can point its `--config-dir`, `--logs-dir`, and `--work-dir` to subfolders of `deploy/nginx/certs` so the nginx container can read the issued certificates.

Example layout after issuing certificates:
```
deploy/nginx/certs/
├── live/your-domain.com/fullchain.pem
├── live/your-domain.com/privkey.pem
├── archive/...
├── renewal/...
```
Update `nginx.conf` `ssl_certificate` paths accordingly.

## Using Certbot

1. Stop the reverse proxy to free ports 80/443 (if necessary).
2. Run Certbot in a separate container (or directly on the host) with webroot or standalone mode, writing into `deploy/nginx/certs`.
   - Example (standalone):
     ```bash
     docker run --rm \
       -p 80:80 -p 443:443 \
       -v $(pwd)/deploy/nginx/certs:/etc/letsencrypt \
       certbot/certbot certonly --standalone \
       -d your-domain.com -d www.your-domain.com
     ```
3. Update `nginx.conf` to point to the generated `fullchain.pem` and `privkey.pem`.
4. Restart `docker compose up -d` to reload nginx with the new certificates.

For automatic renewals, schedule Certbot to re-run monthly (same container invocation) and reload nginx afterwards, e.g. via `docker compose exec reverse-proxy nginx -s reload`.
