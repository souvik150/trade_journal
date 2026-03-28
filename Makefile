PYTHON ?= python3
PORT   ?= 8000

.PHONY: run install fix-data login login-totp setup-totp run-monitor

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
	$(PYTHON) scripts/nubra_login.py

# Run once after `make login` to switch to TOTP (fully non-interactive).
setup-totp:
	$(PYTHON) scripts/nubra_setup_totp.py

# Use after TOTP is set up — generates TOTP from TOTP_SECRET in .env automatically.
login-totp:
	$(PYTHON) scripts/nubra_login.py --totp

run-monitor:
	$(PYTHON) scripts/backend_monitor.py
