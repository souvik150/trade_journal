"""
One-time interactive Nubra login.

Run this once from a terminal to authenticate and cache the session:

    python scripts/nubra_login.py             # OTP login (default)
    python scripts/nubra_login.py --totp      # TOTP login (after setup)

Reads PHONE_NO and MPIN from .env so you don't retype them.
Session is saved to auth_data.db — the server reuses it without prompts.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

totp_mode = "--totp" in sys.argv

print(f"Nubra login — {'TOTP' if totp_mode else 'OTP'} mode")
print(f"Phone : {os.getenv('PHONE_NO', '(not set — will prompt)')}")
print(f"MPIN  : {'set' if os.getenv('MPIN') else '(not set — will prompt)'}")
print()

# If TOTP mode and pyotp secret is set, generate the code automatically
if totp_mode and os.getenv("TOTP_SECRET"):
    try:
        import pyotp
        totp_code = pyotp.TOTP(os.getenv("TOTP_SECRET")).now()
        print(f"Auto-generated TOTP: {totp_code}")
        # The SDK still calls input() for TOTP — patch it
        import builtins
        _real_input = builtins.input
        def _patched_input(prompt=""):
            if "TOTP" in prompt:
                print(f"{prompt}{totp_code}")
                return totp_code
            return _real_input(prompt)
        builtins.input = _patched_input
    except ImportError:
        print("pyotp not installed — you'll be prompted for TOTP manually")
        print("  pip install pyotp")

nubra = InitNubraSdk(NubraEnv.PROD, totp_login=totp_mode, env_creds=True)
print("\nSession cached in auth_data.db — server will reuse it without prompts.")
