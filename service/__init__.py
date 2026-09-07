"""Package marker so the repo's tests can `import service.app` from the repo root.

The image does not use it: the Dockerfile COPYs `app/` and `migrations/` into
/repo/service and runs with that as the working directory, so inside the
container the package is `app`, not `service.app`. This file exists for
tests/test_api_contract.py, which builds the FastAPI app in-process, with no
database, no API key and -- see that file -- no anthropic client installed.
"""
