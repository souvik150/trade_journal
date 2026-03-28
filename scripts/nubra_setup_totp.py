"""
One-time TOTP setup for Nubra.

Run this once after you've done an OTP login (auth_data.db must exist):

    python scripts/nubra_setup_totp.py

It will:
1. Generate a TOTP secret from Nubra
2. Render the QR code in the terminal — scan with any authenticator app
3. Ask you to confirm with a TOTP code to enable it
4. Write TOTP_SECRET to your .env automatically

After setup, use TOTP login for fully non-interactive sessions:
    python scripts/nubra_login.py --totp
"""

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pyotp
import qrcode
from dotenv import load_dotenv, set_key

load_dotenv()

from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

print("Connecting to Nubra (reusing existing session)...")
nubra = InitNubraSdk(NubraEnv.PROD, env_creds=True)

print("\nGenerating TOTP secret...")
raw = nubra.totp_generate_secret()

# Parse the secret key out of the JSON response
try:
    data = json.loads(raw) if isinstance(raw, str) else raw
    secret = data["data"]["secret_key"]
except Exception:
    print(f"Unexpected response format: {raw}")
    sys.exit(1)

# Build the standard otpauth URI
phone = os.getenv("PHONE_NO", "user")
uri = pyotp.totp.TOTP(secret).provisioning_uri(name=phone, issuer_name="Nubra")

# Render QR code in terminal
print("\nScan this QR code with Google Authenticator / Authy / any TOTP app:\n")
qr = qrcode.QRCode(border=1)
qr.add_data(uri)
qr.make(fit=True)
qr.print_ascii(invert=True)

print(f"\nSecret key : {secret}")
print(f"OTP URI    : {uri}\n")

# Write to .env automatically
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
set_key(env_path, "TOTP_SECRET", secret)
print(f"TOTP_SECRET written to .env\n")

enable = input("Enable TOTP now? Enter TOTP code from your app (or blank to skip): ").strip()
if enable:
    mpin = os.getenv("MPIN") or input("MPIN: ").strip()
    try:
        nubra._enable_totp(totp=enable, mpin=mpin)
        print("\nTOTP enabled.")
        print("From now on use:  make login-totp")
    except Exception as e:
        print(f"Failed to enable TOTP: {e}")
else:
    print("Skipped. Re-run this script when ready to enable.")
