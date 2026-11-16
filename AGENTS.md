# Repository Agent Instructions

If the user asks another AI agent to install, deploy, test, or use this repository, read [docs/AGENT-INSTALL.md](docs/AGENT-INSTALL.md) first and follow its acceptance checks.

Key boundaries:

- Never commit .env, API keys, passwords, real student names, or uploaded essays.
- Prefer Docker Compose; fall back to the Python installer only when Docker is unavailable.
- Treat AI results as drafts until a teacher finalizes them.
- Do not send real student data to a model or third-party service without the deployment owner's authorization.
- After changing code, run the repository tests and report actual results.
