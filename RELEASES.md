# Releases

## 2026-05-18 - Portable server launch cleanup

- Added Linux `run.sh` and `run_server.sh` launchers that create and reuse a project-local `.venv`.
- Updated the Windows launcher so dependencies install inside `.venv` instead of the user/global Python.
- Added Waitress server mode for shared Linux workspaces.
- Kept `run_wsl.sh` as a compatibility wrapper around the standard Linux launcher.
- Removed the non-venv WSL launcher so all normal runs use isolated dependencies.

## Validation

- Run `python -m pytest` from the project virtual environment.
- Run `python -m compileall app.py converter.py services.py wsgi.py`.
