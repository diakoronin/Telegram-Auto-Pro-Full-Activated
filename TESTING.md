# Testing

## Run

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

## Coverage

Mock tests (no live panels): API purchase saga, 3x-ui path without config, location migration, subscription HTTP API, card invoice, wallet double-approval, manual import/delivery, quota math, traffic sync, simple CSV flow, support ticket model.

## Live panel tests

**Needs real credentials** — Marzban / 3x-ui need real `DATABASE_URL`, panel URL, and credentials; not run in CI.

## Server install smoke test

Use `install.sh` from GitHub on Ubuntu 22.04/24.04 with `sudo` (see `README.md`).
