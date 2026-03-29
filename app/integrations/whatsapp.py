"""
WhatsApp delivery via whatsapp-web.js (QR-based auth, no Business API needed).

On first run a QR code is printed to the terminal — scan it with WhatsApp.
The session is persisted in app/integrations/.wwebjs_auth/ so subsequent
runs skip the QR entirely.

Required env var:
  WHATSAPP_TO   — recipient phone number with country code, e.g. +919876543210

Node.js setup (one-time):
  cd app/integrations && npm install
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_SENDER_JS = Path(__file__).parent / "whatsapp_sender.js"


def send_file(
    *,
    data: bytes,
    mime_type: str,
    filename: str,
    caption: str = "",
) -> None:
    """
    Send `data` as a WhatsApp document via whatsapp-web.js.
    Silently skips if WHATSAPP_TO is not set.
    Meant to be called from a FastAPI BackgroundTask.
    """
    to = os.getenv("WHATSAPP_TO", "").strip()
    if not to:
        logger.warning("WhatsApp: WHATSAPP_TO not set — skipping delivery of %s", filename)
        return

    logger.info("WhatsApp: starting delivery of %s to %s", filename, to)

    suffix = Path(filename).suffix or ".bin"
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        proc = subprocess.Popen(
            [
                "node", str(_SENDER_JS),
                "--to",       to,
                "--file",     tmp_path,
                "--filename", filename,
                "--caption",  caption,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge stderr into stdout so we see everything
            text=True,
        )
        # Stream output line-by-line so QR code appears in real time in server logs
        for line in proc.stdout:
            print(line, end="", flush=True)

        proc.wait(timeout=120)
        if proc.returncode != 0:
            logger.warning("WhatsApp delivery failed for %s (exit %s)", filename, proc.returncode)
        else:
            logger.info("WhatsApp: sent %s to %s", filename, to)
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning("WhatsApp delivery timed out for %s", filename)
    except FileNotFoundError:
        logger.warning(
            "WhatsApp: 'node' not found — install Node.js and run "
            "'npm install' inside app/integrations/"
        )
    except Exception as exc:
        logger.warning("WhatsApp delivery failed for %s: %s", filename, exc)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
