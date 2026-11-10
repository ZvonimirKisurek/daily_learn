# One image, three roles: the API (default CMD), a Celery worker, and Celery beat. Override the command per service
# (Render: dockerCommand in render.yaml). Build context is backend/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/srv ENABLE_DOCS=false
WORKDIR /srv

# Layer caching: dependencies change rarely, code often. Install ONLY the dependency list from pyproject.toml
# first (the project itself is run from /srv, never pip-installed), so a code edit doesn't re-download wheels.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# curl: used once below to install Claude Code (and handy for debugging inside the container)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY sources ./sources
COPY curriculum ./curriculum

# Never run as root in a container. The user gets a home directory because Claude Code keeps its files there.
RUN useradd --system --uid 10001 --create-home app && chown -R app /srv
USER app

# Claude Code (the official program), so LLM_PROVIDER=claude_code can run the site's AI on your Claude subscription
# instead of an API key. It only starts when that setting is on. Skip with: --build-arg INSTALL_CLAUDE_CODE=0
ARG INSTALL_CLAUDE_CODE=1
ENV PATH="/home/app/.local/bin:${PATH}" DISABLE_AUTOUPDATER=1
RUN if [ "$INSTALL_CLAUDE_CODE" = "1" ]; then curl -fsSL https://claude.ai/install.sh | bash && claude --version; fi

EXPOSE 8000
# Platforms like Render/Railway/Fly inject $PORT; fall back to 8000 elsewhere. `exec` makes uvicorn PID 1 so it
# receives SIGTERM and drains in-flight requests on deploys.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
# No HEALTHCHECK here: this image also runs the Celery worker and beat (no HTTP port). Health is checked per service:
# Render uses healthCheckPath: /health (render.yaml); docker-compose.prod.yml defines its own check on the api service.
