PYTHON ?= python3
PORT   ?= 8000

.PHONY: run install fix-data login login-totp setup-totp run-monitor lint

run:
	$(PYTHON) -m app.main

install:
	pip install -r requirements.txt

fix-data:
	$(PYTHON) fix_data.py

# ── Nubra auth ────────────────────────────────────────────────────────────────
# Run once to authenticate and cache the session (enters OTP interactively).
# After this the server reuses the session silently via MPIN from .env.
login:
	$(PYTHON) -m scripts.nubra_login

# Run once after `make login` to switch to TOTP (fully non-interactive).
setup-totp:
	$(PYTHON) -m scripts.nubra_setup_totp

# Use after TOTP is set up — generates TOTP from TOTP_SECRET in .env automatically.
login-totp:
	$(PYTHON) -m scripts.nubra_login --totp

run-monitor:
	$(PYTHON) -m scripts.backend_monitor

lint:
	$(PYTHON) -m pylint app scripts fetch_historical.py
