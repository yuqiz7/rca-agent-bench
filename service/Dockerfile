# api image for docker-compose.service.yml.
#
# NO SECRETS ENTER THIS IMAGE. There is no `ARG` in this file at all, and no
# `COPY .env` -- a build arg holding a key survives in the image layer history
# and `docker history` prints it back out (design §5). Both the Anthropic key and
# the Postgres password arrive at RUN time: the key as an environment variable
# compose reads from the VM shell, the password as a docker secret file. Neither
# is ever a build input.
#
# Python 3.12 to match .github/workflows/ci.yml and the repo's venv: from step 3
# this image imports scripts/agent/, and the service must produce digit-for-digit
# the same numbers as the CLI (§4). A different interpreter is not the place to
# find out that it does not.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /repo/service

# Requirements first so the dependency layer survives edits to app/.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations

# The bind mounts land at /repo/{evidence,scenarios,artifacts}; /repo/service is
# the package root, so `app.main:app` resolves from WORKDIR.
EXPOSE 8000
# --log-config puts uvicorn's own lines through app.logs.JsonFormatter and
# leaves uvicorn.access without a handler; app/main.py's access_log
# middleware prints the request line instead, with latency and run_id.
# Every line on this container's stdout is then a JSON object (design §5).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--log-config", "app/log_config.json"]
