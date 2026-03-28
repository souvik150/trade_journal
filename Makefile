PYTHON ?= python3
PORT   ?= 8000

.PHONY: run install fix-data load-data login login-totp setup-totp

run:
	$(PYTHON) main.py

install:
	pip install -r requirements.txt

fix-data:
	$(PYTHON) fix_data.py

load-data:
	curl -s -X POST http://localhost:$(PORT)/data/load | python3 -m json.tool

# Manual rebuild hook; the server now bootstraps data automatically on startup.

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
