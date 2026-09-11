import asyncio
import datetime
import io
import json
import logging
import os
import sys
import zipfile
from aiohttp import web
import discord
from discord import app_commands
from jinja2 import Environment, FileSystemLoader
from database import LicenseDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("FinxAuth")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
BASE_JAR_PATH = os.path.join(BASE_DIR, "client", "FinxClient-1.6.2.jar")

config = {
    "bot_token": os.getenv("BOT_TOKEN", ""),
    "client_id": os.getenv("CLIENT_ID", ""),
    "admin_role_id": os.getenv("ADMIN_ROLE_ID", ""),
    # Railway injects PORT; fallback to API_PORT config, then 3000 for local dev
    "api_port": int(os.getenv("PORT", os.getenv("API_PORT", 3000))),
    "api_host": os.getenv("API_HOST", "0.0.0.0"),
    "database_path": os.getenv("DATABASE_PATH", "licenses.db"),
    "dashboard_url": os.getenv("DASHBOARD_URL", "http://localhost:3000/dashboard")
}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            file_cfg = json.load(f)
            config.update(file_cfg)
    except Exception as e:
        logger.warning(f"Failed to parse config.json: {e}")

db = LicenseDB(config.get("database_path", "licenses.db"))
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)

def is_admin(member):
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    admin_role = str(config.get("admin_role_id", "")).strip()
    if admin_role and any(str(r.id) == admin_role for r in member.roles):
        return True
    return False

def format_expiry(expires_at):
    if not expires_at:
        return "Lifetime (Never expires)"
    try:
        exp_dt = datetime.datetime.fromisoformat(expires_at)
        now = datetime.datetime.now(datetime.timezone.utc)
        if now > exp_dt:
            return f"Expired on {exp_dt.strftime('%Y-%m-%d %H:%M UTC')}"
        diff = exp_dt - now
        days = diff.days
        hours = int(diff.seconds // 3600)
        return f"{exp_dt.strftime('%Y-%m-%d %H:%M UTC')} ({days}d {hours}h remaining)"
    except Exception:
        return str(expires_at)

def build_client_jar_with_key(key):
    """Injects 'finx_license.key' into the base JAR in memory."""
    jar_path = BASE_JAR_PATH
    # Fallback to source build dir if client/ directory does not have the jar
    if not os.path.exists(jar_path):
        fallback = os.path.join(BASE_DIR, "..", "67Client SourceByDexter", "build", "libs", "FinxClient-1.6.2.jar")
        if os.path.exists(fallback):
            jar_path = fallback

    # If still not found, try downloading from JAR_DOWNLOAD_URL (for Railway/cloud hosting)
    jar_download_url = os.getenv("JAR_DOWNLOAD_URL", "").strip()
    if not os.path.exists(jar_path) and jar_download_url:
        import urllib.request
        logger.info(f"JAR not found locally, downloading from JAR_DOWNLOAD_URL...")
        os.makedirs(os.path.dirname(jar_path), exist_ok=True)
        try:
            urllib.request.urlretrieve(jar_download_url, jar_path)
            logger.info(f"JAR downloaded to {jar_path}")
        except Exception as e:
            raise FileNotFoundError(f"Failed to download JAR from {jar_download_url}: {e}")

    if not os.path.exists(jar_path):
        raise FileNotFoundError(
            "Base FinxClient-1.6.2.jar not found!\n"
            "Either upload the JAR to client/FinxClient-1.6.2.jar, or set the "
            "JAR_DOWNLOAD_URL environment variable to a direct download link."
        )

    out_buf = io.BytesIO()
    with zipfile.ZipFile(jar_path, 'r') as in_zip:
        with zipfile.ZipFile(out_buf, 'w', compression=zipfile.ZIP_DEFLATED) as out_zip:
            for item in in_zip.infolist():
                if item.filename == "finx_license.key":
                    continue
                out_zip.writestr(item, in_zip.read(item.filename))
            out_zip.writestr("finx_license.key", key.strip().encode("utf-8"))

    out_buf.seek(0)
    return out_buf

# ==========================================
# 1. REST API & Web Dashboard Handlers
# ==========================================

discord_status = {"connected": False, "user": None, "last_error": None}

async def handle_status(request):
    token_val = os.getenv("BOT_TOKEN", config.get("bot_token", "")).strip()
    return web.json_response({
        "status": "online",
        "service": "FinxClient Auth Server & Portal",
        "version": "1.2.0",
        "discord": {
            "token_configured": bool(token_val and token_val != "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE"),
            "token_length": len(token_val),
            "connected": bot.is_ready(),
            "user": str(bot.user) if bot.user else None,
            "last_error": discord_status.get("last_error")
        }
    })

async def handle_dashboard(request):
    key_query = request.query.get("key", "").strip()
    template = jinja_env.get_template("dashboard.html")

    if not key_query:
        # Render clean login form
        html = template.render(license=None, error=None)
        return web.Response(text=html, content_type="text/html")

    # Look up by key or by Discord ID
    lic = db.get_by_key(key_query)
    if not lic:
        lic = db.get_by_discord_id(key_query)

    if not lic:
        html = template.render(license=None, error="No active license found for that Key or Discord ID.")
        return web.Response(text=html, content_type="text/html")

    if not lic.get("active"):
        html = template.render(license=None, error="This license key has been revoked.")
        return web.Response(text=html, content_type="text/html")

    expiry_text = format_expiry(lic.get("expires_at"))
    html = template.render(license=lic, expiry_text=expiry_text, error=None)
    return web.Response(text=html, content_type="text/html")

async def handle_download(request):
    key = request.query.get("key", "").strip()
    if not key:
        return web.json_response({"success": False, "message": "Missing key parameter."}, status=400)

    lic = db.get_by_key(key)
    if not lic:
        return web.json_response({"success": False, "message": "Invalid license key."}, status=401)

    if not lic["active"]:
        return web.json_response({"success": False, "message": "License key is deactivated."}, status=403)

    # Check expiration
    expires_at = lic.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.datetime.fromisoformat(expires_at)
            if datetime.datetime.now(datetime.timezone.utc) > exp_dt:
                return web.json_response({"success": False, "message": "License key has expired."}, status=403)
        except Exception:
            pass

    try:
        jar_buf = build_client_jar_with_key(lic["key"])
        filename = "FinxClient-1.6.2.jar"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/java-archive"
        }
        logger.info(f"Delivering pre-activated JAR to key {key[:8]}...")
        return web.Response(body=jar_buf.getvalue(), headers=headers)
    except Exception as e:
        logger.error(f"Error packaging client JAR: {e}")
        return web.json_response({"success": False, "message": f"Error packaging JAR: {e}"}, status=500)

async def handle_api_resethwid(request):
    key = request.query.get("key", "").strip()
    if not key and request.can_read_body:
        try:
            data = await request.json()
            key = data.get("key", "").strip()
        except Exception:
            pass

    if not key:
        return web.json_response({"success": False, "message": "Missing key."}, status=400)

    success = db.reset_hwid_by_key(key)
    if success:
        return web.json_response({"success": True, "message": "HWID reset successfully."})
    return web.json_response({"success": False, "message": "License key not found."}, status=404)

async def handle_verify(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "message": "Invalid JSON body."}, status=400)

    key = data.get("key", "").strip()
    hwid = data.get("hwid", "").strip()

    if not key or not hwid:
        return web.json_response({"success": False, "message": "Missing key or hwid field."}, status=400)

    success, message = db.verify(key, hwid)
    status_code = 200 if success else (403 if "mismatch" in message.lower() else 401)

    logger.info(f"Auth attempt for key {key[:8]}... -> success={success} ({message})")
    return web.json_response({
        "success": success,
        "message": message
    }, status=status_code)

def create_api_app():
    app = web.Application()
    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/download", handle_download)
    app.router.add_post("/api/resethwid", handle_api_resethwid)
    app.router.add_post("/api/verify", handle_verify)
    return app

# ==========================================
# 2. Discord Bot
# ==========================================

class FinxAuthBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Discord slash commands synchronized globally.")

    async def on_ready(self):
        logger.info(f"FinxAuthBot logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(activity=discord.Game(name="FinxClient | /dashboard"))

bot = FinxAuthBot()

# --- COMMAND: /dashboard ---
@bot.tree.command(name="dashboard", description="Get your personal 1-click link to the FinxClient download portal.")
async def cmd_dashboard(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    lic = db.get_by_discord_id(user_id)

    if not lic:
        embed = discord.Embed(
            title="No Active License",
            color=0xFF5555,
            description="❌ You do not have an active FinxClient license.\nPlease contact an administrator or purchase access."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    base_url = config.get("dashboard_url", "http://localhost:3000/dashboard").rstrip("/")
    # In case the config didn't include /dashboard in URL
    if not base_url.endswith("/dashboard"):
        base_url = f"{base_url}/dashboard"

    dash_link = f"{base_url}?key={lic['key']}"
    expiry_text = format_expiry(lic.get("expires_at"))

    embed = discord.Embed(
        title="FinxClient Member Portal",
        color=0xBE5AFF,
        description="Click the link below to access your personal dashboard, reset your HWID, or download the pre-activated client:"
    )
    embed.add_field(name="Dashboard Link", value=f"[👉 Open Your FinxClient Dashboard]({dash_link})", inline=False)
    embed.add_field(name="Plan", value=expiry_text, inline=True)
    embed.add_field(name="Key", value=f"{lic['key']}", inline=True)
    embed.set_footer(text="Keep your link private! Do not share it with others.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- ADMIN COMMAND: /assignkey ---
@bot.tree.command(name="assignkey", description="[Admin] Assign a license key to a user with custom duration or lifetime.")
@app_commands.describe(
    user="The Discord user to give the license to",
    duration="Select duration or Lifetime",
    custom_days="Optional: exact number of days (if you selected Custom)",
    notes="Optional note or reason"
)
@app_commands.choices(duration=[
    app_commands.Choice(name="Lifetime (Never expires)", value=0),
    app_commands.Choice(name="1 Day", value=1),
    app_commands.Choice(name="3 Days", value=3),
    app_commands.Choice(name="7 Days (1 Week)", value=7),
    app_commands.Choice(name="14 Days (2 Weeks)", value=14),
    app_commands.Choice(name="30 Days (1 Month)", value=30),
    app_commands.Choice(name="60 Days (2 Months)", value=60),
    app_commands.Choice(name="90 Days (3 Months)", value=90),
    app_commands.Choice(name="180 Days (6 Months)", value=180),
    app_commands.Choice(name="365 Days (1 Year)", value=365),
    app_commands.Choice(name="Custom (enter number in custom_days)", value=-1),
])
async def cmd_assignkey(
    interaction: discord.Interaction,
    user: discord.User,
    duration: app_commands.Choice[int],
    custom_days: int = None,
    notes: str = ""
):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ You do not have permission to assign keys.", ephemeral=True)
        return

    if duration.value == -1:
        if custom_days is None or custom_days <= 0:
            await interaction.response.send_message("❌ Please provide a valid number in custom_days when selecting Custom.", ephemeral=True)
            return
        days = custom_days
    elif custom_days is not None and custom_days > 0:
        days = custom_days
    else:
        days = duration.value

    key, is_new, expires_at, duration_type = db.assign_key(user.id, str(user), days=days, notes=notes)
    duration_text = format_expiry(expires_at)
    
    base_url = config.get("dashboard_url", "http://localhost:3000/dashboard").rstrip("/")
    if not base_url.endswith("/dashboard"):
        base_url = f"{base_url}/dashboard"
    dash_link = f"{base_url}?key={key}"

    # Try DMing user the key and dashboard link
    dm_sent = False
    try:
        dm_embed = discord.Embed(
            title="🎉 You have been granted a FinxClient License!",
            color=0xBE5AFF,
            description=f"An administrator has assigned you a **{duration_type.upper()}** license for FinxClient."
        )
        dm_embed.add_field(name="Your License Key", value=f"`{key}`", inline=False)
        dm_embed.add_field(name="Duration", value=duration_text, inline=False)
        dm_embed.add_field(
            name="1-Click Download Portal",
            value=f"[👉 Click here to download your pre-activated client]({dash_link})",
            inline=False
        )
        dm_embed.set_footer(text="Keep this key private! It binds to your PC on first launch.")
        await user.send(embed=dm_embed)
        dm_sent = True
    except Exception:
        dm_sent = False

    # Reply to admin
    admin_embed = discord.Embed(
        title="✅ License Assigned Successfully",
        color=0x55FF55
    )
    admin_embed.add_field(name="User", value=f"{user.mention} ({user})", inline=True)
    admin_embed.add_field(name="Action", value="Created new key" if is_new else "Updated existing key", inline=True)
    admin_embed.add_field(name="Duration", value=duration_text, inline=False)
    admin_embed.add_field(name="License Key", value=f"`{key}`", inline=False)
    admin_embed.add_field(name="Dashboard Link", value=f"[Portal Link]({dash_link})", inline=False)
    admin_embed.add_field(name="DM Status", value="✅ Key sent to user's DMs" if dm_sent else "⚠️ User's DMs are closed — please send them the link manually!", inline=False)

    await interaction.response.send_message(embed=admin_embed, ephemeral=True)
    logger.info(f"Admin {interaction.user} assigned {duration_type} key {key[:8]}... to {user}")

# --- USER COMMAND: /getkey ---
@bot.tree.command(name="getkey", description="View your assigned FinxClient license key.")
async def cmd_getkey(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    lic = db.get_by_discord_id(user_id)

    if not lic:
        embed = discord.Embed(
            title="No Assigned License",
            color=0xFF5555,
            description="❌ You do not have an assigned FinxClient license.\nPlease contact an administrator or purchase access to receive a key."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    key = lic["key"]
    duration_text = format_expiry(lic.get("expires_at"))
    base_url = config.get("dashboard_url", "http://localhost:3000/dashboard").rstrip("/")
    if not base_url.endswith("/dashboard"):
        base_url = f"{base_url}/dashboard"
    dash_link = f"{base_url}?key={key}"

    embed = discord.Embed(
        title="Your FinxClient License",
        color=0xBE5AFF,
        description="Here are the details for your assigned license key:"
    )
    embed.add_field(name="License Key", value=f"`{key}`", inline=False)
    embed.add_field(name="Duration / Expiry", value=duration_text, inline=False)
    embed.add_field(name="Download Portal", value=f"[👉 Open Portal & Download Client]({dash_link})", inline=False)
    embed.set_footer(text="Keep this key private! It binds to your PC on first launch.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- USER COMMAND: /mykey ---
@bot.tree.command(name="mykey", description="Check details, duration, and HWID status of your license key.")
async def cmd_mykey(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    lic = db.get_by_discord_id(user_id)

    if not lic:
        await interaction.response.send_message("❌ You don't have an active license key. Please contact an admin.", ephemeral=True)
        return

    hwid_status = "✅ Linked (" + lic["hwid"][:12] + "...)" if lic.get("hwid") else "⚠️ Not linked yet (will bind on first launch)"
    duration_text = format_expiry(lic.get("expires_at"))

    embed = discord.Embed(title="Your FinxClient License", color=0xBE5AFF)
    embed.add_field(name="Key", value=f"{lic['key']}", inline=False)
    embed.add_field(name="Duration", value=duration_text, inline=False)
    embed.add_field(name="HWID Status", value=hwid_status, inline=True)
    embed.add_field(name="Created", value=str(lic["created_at"])[:10], inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- USER COMMAND: /resethwid ---
@bot.tree.command(name="resethwid", description="Reset your bound Hardware ID (e.g. after changing PC or parts).")
async def cmd_resethwid(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    success = db.reset_hwid_by_discord_id(user_id)

    if success:
        embed = discord.Embed(
            title="HWID Reset Successful",
            color=0x55FF55,
            description="✅ Your bound HWID has been cleared. The next PC you launch FinxClient from will automatically bind to your key."
        )
    else:
        embed = discord.Embed(
            title="No Active License",
            color=0xFF5555,
            description="❌ You do not have an active FinxClient key to reset."
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- ADMIN COMMAND: /revokekey ---
@bot.tree.command(name="revokekey", description="[Admin] Revoke a license key.")
@app_commands.describe(key="The license key to revoke")
async def cmd_revokekey(interaction: discord.Interaction, key: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
        return

    success = db.revoke_key(key)
    if success:
        await interaction.response.send_message(f"✅ Key {key} has been revoked.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Key {key} was not found.", ephemeral=True)

# ==========================================
# 3. Main Runner
# ==========================================

async def run_discord(token):
    global discord_status
    while True:
        try:
            logger.info("Connecting Discord bot...")
            discord_status["last_error"] = None
            await bot.start(token)
        except Exception as e:
            discord_status["connected"] = False
            discord_status["last_error"] = f"{type(e).__name__}: {e}"
            logger.error(f"Discord bot disconnected or encountered an error: {e}")
            logger.info("Retrying Discord connection in 15 seconds...")
            await asyncio.sleep(15)

async def main():
    token = os.getenv("BOT_TOKEN", config.get("bot_token", "")).strip()
    port = int(os.getenv("PORT", os.getenv("API_PORT", config.get("api_port", 3000))))
    host = os.getenv("API_HOST", config.get("api_host", "0.0.0.0"))

    # 1. Start web server (ALWAYS RUNS)
    app = create_api_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"Auth REST API & Portal listening on port {port} (host: {host})")

    # 2. Start Discord bot as non-fatal background task
    if token and token != "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE":
        asyncio.create_task(run_discord(token))
    else:
        logger.warning("No valid Discord BOT_TOKEN provided; web portal is running in standalone mode.")

    # 3. Keep web server running forever
    stop_event = asyncio.Event()
    await stop_event.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Server stopped.")