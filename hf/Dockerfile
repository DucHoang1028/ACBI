# Hugging Face Space image: web, backend and two PostgreSQL databases in one container.
# This file (with README.md) is what the Space repository holds; the code is fetched
# from GitHub at the commit named by REF, so the Space never carries the source twice.
ARG REF=main

FROM alpine/git AS repo
ARG REF
RUN git clone https://github.com/DucHoang1028/ACBI.git /repo \
 && cd /repo && git checkout "$REF"

FROM node:22-alpine AS web
WORKDIR /web
COPY --from=repo /repo/frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY --from=repo /repo/frontend ./
RUN npm run build

FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg nginx \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-18 \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --from=repo /repo/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=repo --chown=user /repo/backend/app ./app
COPY --from=repo --chown=user /repo/data ./data
COPY --from=repo --chown=user /repo/scripts ./scripts
COPY --from=repo --chown=user /repo/hf/nginx.conf ./nginx.conf
COPY --from=repo --chown=user /repo/hf/entrypoint.sh ./entrypoint.sh
COPY --from=web --chown=user /web/dist ./web
RUN chmod +x entrypoint.sh
USER user
ENV HOME=/home/user PORT=7860
EXPOSE 7860
CMD ["./entrypoint.sh"]
