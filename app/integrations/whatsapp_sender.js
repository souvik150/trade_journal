/**
 * WhatsApp file sender using whatsapp-web.js (QR-based auth).
 *
 * Usage:
 *   node whatsapp_sender.js --to +919876543210 --file /tmp/report.pdf \
 *        --filename report.pdf --caption "Trade Journal Report"
 *
 * First run: a QR code is printed — scan it with WhatsApp on your phone.
 * Session is saved locally (.wwebjs_auth/) so subsequent runs skip the QR.
 *
 * Install deps once:  npm install  (inside app/integrations/)
 */

const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const path = require("path");
const fs = require("fs");

// ── Parse CLI args ────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const get = (flag) => {
  const idx = args.indexOf(flag);
  return idx !== -1 ? args[idx + 1] : null;
};

const to      = get("--to");
const file    = get("--file");
const caption = get("--caption") || "";
const fname   = get("--filename") || path.basename(file || "file");

if (!to || !file) {
  console.error(
    "Usage: node whatsapp_sender.js --to <number> --file <path> " +
    "[--filename <name>] [--caption <text>]"
  );
  process.exit(1);
}

console.log(`[whatsapp] args OK — to=${to} file=${file} filename=${fname}`);

const AUTH_DIR = path.join(__dirname, ".wwebjs_auth");
console.log(`[whatsapp] auth dir: ${AUTH_DIR}`);

// Remove stale Chromium lock so crashed/killed runs never block the next one
const lockFile = path.join(AUTH_DIR, "session", "SingletonLock");
try {
  fs.unlinkSync(lockFile);
  console.log("[whatsapp] Removed stale SingletonLock");
} catch (_) { /* no lock file — that's fine */ }

// ── Client ────────────────────────────────────────────────────────────────────
const client = new Client({
  authStrategy: new LocalAuth({
    dataPath: path.join(__dirname, ".wwebjs_auth"),
  }),
  puppeteer: {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--no-first-run",
      "--no-zygote",
      "--single-process",
      "--disable-gpu",
    ],
  },
});

client.on("qr", (qr) => {
  console.log("Scan the QR code below with WhatsApp on your phone:");
  qrcode.generate(qr, { small: true });
});

client.on("ready", async () => {
  console.log("WhatsApp client ready — sending file...");
  try {
    // Normalise number → chatId: strip leading '+', append '@c.us'
    const chatId = to.replace(/^\+/, "") + "@c.us";
    console.log(`[whatsapp] Sending to chatId: ${chatId}`);
    const media = MessageMedia.fromFilePath(file);
    media.filename = fname;
    const msg = await client.sendMessage(chatId, media, {
      caption,
      sendMediaAsDocument: true,
    });
    console.log(`[whatsapp] Queued — waiting for server ack...`);

    // Wait until WhatsApp server confirms receipt (ack 0→1) before destroying
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("ACK timeout after 30s")), 30000);
      client.on("message_ack", (ackedMsg, ack) => {
        if (ackedMsg.id._serialized === msg.id._serialized && ack >= 1) {
          clearTimeout(timer);
          console.log(`[whatsapp] Delivered — ack: ${ack}`);
          resolve();
        }
      });
    });

    console.log(`Sent "${fname}" to ${to}`);
  } catch (err) {
    console.error("Send failed:", err.message || err);
    process.exitCode = 1;
  } finally {
    await client.destroy();
    process.exit(process.exitCode || 0);
  }
});

client.on("auth_failure", (msg) => {
  console.error("[whatsapp] Auth failure:", msg);
  process.exit(1);
});

client.on("loading_screen", (percent, message) => {
  console.log(`[whatsapp] Loading: ${percent}% — ${message}`);
});

console.log("[whatsapp] Initializing client...");
client.initialize();
