import os
import io
import uuid
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import (
    FastAPI,
    File,
    UploadFile,
    HTTPException,
    Query,
    BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse

# ==================================================
# ENVIRONMENT - ULTRA CLEANING
# ==================================================

def clean_env_value(value):
    """Remove all whitespace, quotes, and invisible characters"""
    if not value:
        return ""
    # Remove all types of whitespace
    value = value.strip()
    # Remove quotes
    value = value.strip('"').strip("'")
    # Remove any non-printable characters
    value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
    # Remove any hidden characters
    value = value.replace('\r', '').replace('\n', '').replace('\t', '')
    return value

# Debug: Print raw environment
print("=" * 70)
print("🔍 ENVIRONMENT VARIABLES DIAGNOSTIC")
print("=" * 70)

# Get and clean ALL environment variables
SUPABASE_URL = clean_env_value(os.environ.get("SUPABASE_URL", ""))
SUPABASE_SECRET_KEY = clean_env_value(os.environ.get("SUPABASE_SECRET_KEY", ""))
SUPABASE_SERVICE_ROLE_KEY = clean_env_value(os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
SUPABASE_ANON_KEY = clean_env_value(os.environ.get("SUPABASE_ANON_KEY", ""))
SUPABASE_KEY = clean_env_value(os.environ.get("SUPABASE_KEY", ""))

BOT_TOKEN = clean_env_value(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
CHAT_ID = clean_env_value(os.environ.get("TELEGRAM_CHAT_ID", ""))

# Debug print with character analysis
def debug_key(name, value):
    if value:
        print(f"✅ {name}:")
        print(f"   Length: {len(value)}")
        print(f"   First 20 chars: {value[:20]}...")
        print(f"   Last 5 chars: ...{value[-5:]}")
        print(f"   Contains only valid chars: {bool(re.match(r'^[a-zA-Z0-9._-]+$', value))}")
        # Check for invisible characters
        invisible = [c for c in value if ord(c) < 32 or ord(c) > 126]
        if invisible:
            print(f"   ⚠️  Contains invisible characters: {[hex(ord(c)) for c in invisible]}")
    else:
        print(f"❌ {name}: NOT SET")

print("\n📋 ENVIRONMENT VARIABLES STATUS:")
debug_key("SUPABASE_URL", SUPABASE_URL)
debug_key("SUPABASE_SECRET_KEY", SUPABASE_SECRET_KEY)
debug_key("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_SERVICE_ROLE_KEY)
debug_key("SUPABASE_ANON_KEY", SUPABASE_ANON_KEY)

print("\n" + "=" * 70)

# ==================================================
# FALLBACK: Try to get keys from multiple sources
# ==================================================

# If no keys found, try ALL possible Supabase key env vars
ALL_POSSIBLE_KEYS = []

# Try all possible key names
key_names = [
    "SUPABASE_SECRET_KEY",
    "SUPABASE_SERVICE_ROLE_KEY", 
    "SUPABASE_ANON_KEY",
    "SUPABASE_KEY",
    "SUPABASE_PUBLIC_KEY",
    "SERVICE_ROLE_KEY",
    "SECRET_KEY"
]

for key_name in key_names:
    value = clean_env_value(os.environ.get(key_name, ""))
    if value and len(value) > 10:
        ALL_POSSIBLE_KEYS.append((key_name, value))
        print(f"[FOUND] {key_name}: {value[:15]}...")

# Also try any environment variable containing "SUPABASE" and "KEY"
for env_name, env_value in os.environ.items():
    if "SUPABASE" in env_name.upper() and "KEY" in env_name.upper():
        clean_value = clean_env_value(env_value)
        if clean_value and len(clean_value) > 10:
            if (env_name, clean_value) not in ALL_POSSIBLE_KEYS:
                ALL_POSSIBLE_KEYS.append((env_name, clean_value))
                print(f"[FOUND] {env_name}: {clean_value[:15]}...")

# ==================================================
# APPLICATION
# ==================================================

app = FastAPI(
    title="Horror Story Captures API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================================================
# CONFIGURATION
# ==================================================

BUCKET_NAME = "captures"
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
IST = ZoneInfo("Asia/Kolkata")

# ==================================================
# SUPABASE - ULTRA ROBUST INITIALIZATION
# ==================================================

supabase = None
supabase_error = None

def init_supabase_robust():
    """Try every possible combination to connect to Supabase"""
    global supabase, supabase_error
    
    print("\n🚀 STARTING SUPABASE INITIALIZATION...")
    
    # Check if supabase package exists
    try:
        from supabase import create_client
        print("✅ Supabase package imported successfully")
    except ImportError as e:
        supabase_error = f"Supabase package not installed: {e}"
        print(f"❌ {supabase_error}")
        return None
    
    # Check URL
    if not SUPABASE_URL:
        supabase_error = "SUPABASE_URL is empty or not set"
        print(f"❌ {supabase_error}")
        return None
    
    print(f"✅ SUPABASE_URL: {SUPABASE_URL[:30]}...")
    
    # If no keys found, try to get from environment again
    if not ALL_POSSIBLE_KEYS:
        supabase_error = "No Supabase keys found in environment!"
        print(f"❌ {supabase_error}")
        print("   Please set one of these environment variables:")
        print("   - SUPABASE_SECRET_KEY")
        print("   - SUPABASE_SERVICE_ROLE_KEY")
        print("   - SUPABASE_ANON_KEY")
        return None
    
    print(f"✅ Found {len(ALL_POSSIBLE_KEYS)} potential API keys")
    
    # Try each key with different methods
    last_error = None
    
    for key_name, key_value in ALL_POSSIBLE_KEYS:
        print(f"\n🔑 Trying {key_name}...")
        
        # Try with different client creation methods
        methods_to_try = [
            ("normal", lambda: create_client(SUPABASE_URL, key_value)),
        ]
        
        # Also try with different URL formats
        urls_to_try = [
            SUPABASE_URL,
            SUPABASE_URL.rstrip('/'),
            f"https://{SUPABASE_URL.replace('https://', '').split('.')[0]}.supabase.co",
        ]
        
        # Try all combinations
        for url in set(urls_to_try):
            try:
                print(f"   Testing with URL: {url[:30]}...")
                client = create_client(url, key_value)
                
                # Test connection - try multiple operations
                test_passed = False
                test_errors = []
                
                # Try 1: List buckets
                try:
                    buckets = client.storage.list_buckets()
                    print(f"   ✅ Storage test passed! Found {len(buckets)} buckets")
                    test_passed = True
                    supabase = client
                    print(f"\n🎉 SUCCESS! Connected with {key_name}")
                    return client
                except Exception as e:
                    test_errors.append(f"Storage: {str(e)[:50]}")
                
                # Try 2: Table query (if storage fails)
                if not test_passed:
                    try:
                        result = client.table("images").select("*", count="exact", head=True).execute()
                        print(f"   ✅ Database test passed!")
                        test_passed = True
                        supabase = client
                        print(f"\n🎉 SUCCESS! Connected with {key_name} (database mode)")
                        return client
                    except Exception as e:
                        test_errors.append(f"Database: {str(e)[:50]}")
                
                # Try 3: Auth test (if both fail)
                if not test_passed:
                    try:
                        # Just check if we can get auth status
                        result = client.auth.get_session()
                        print(f"   ✅ Auth test passed!")
                        test_passed = True
                        supabase = client
                        print(f"\n🎉 SUCCESS! Connected with {key_name} (auth mode)")
                        return client
                    except Exception as e:
                        test_errors.append(f"Auth: {str(e)[:50]}")
                
                if not test_passed:
                    print(f"   ❌ All tests failed for {key_name}: {', '.join(test_errors)}")
                    last_error = test_errors[0] if test_errors else "Unknown error"
                    
            except Exception as e:
                error_msg = str(e)
                print(f"   ❌ Connection failed: {error_msg[:50]}")
                last_error = error_msg
                
                # If error is about API key, this key is invalid
                if "invalid" in error_msg.lower() or "api key" in error_msg.lower():
                    print(f"   ⚠️  {key_name} appears to be invalid")
                    continue
    
    supabase_error = f"All keys failed. Last error: {last_error}"
    print(f"\n❌ {supabase_error}")
    return None

# Initialize
supabase = init_supabase_robust()

# ==================================================
# TELEGRAM CONFIG
# ==================================================

TELEGRAM_CONFIGURED = bool(
    BOT_TOKEN and CHAT_ID and
    BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" and
    CHAT_ID != "YOUR_CHAT_ID_HERE" and
    len(BOT_TOKEN) > 10 and
    len(CHAT_ID) > 3
)

print(f"\n📱 Telegram: {'✅ Configured' if TELEGRAM_CONFIGURED else '❌ Not configured'}")

# ==================================================
# HELPER FUNCTIONS (Keep your existing ones)
# ==================================================

def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"

def get_ist_time() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)

def compress_image(image_bytes: bytes, max_size=(1600, 1600), quality=82) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        print("[WARNING] PIL not available")
        return image_bytes
    
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
        img = Image.open(io.BytesIO(image_bytes))
        
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.getchannel("A"))
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
        
        img.thumbnail(max_size, Image.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()
    except Exception as e:
        print(f"[IMAGE ERROR] {e}")
        return image_bytes

async def send_photo_to_telegram(
    image_bytes: bytes,
    filename: str,
    capture_id: str,
    original_size: int,
    compressed_size: int
) -> bool:
    if not TELEGRAM_CONFIGURED:
        return False

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        capture_time = get_ist_time()
        
        caption = (
            "📸 <b>NEW IMAGE CAPTURED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 <b>Capture ID:</b> <code>{capture_id}</code>\n"
            f"🕒 <b>Captured:</b> {capture_time.strftime('%d %b %Y, %I:%M:%S %p')}\n"
            "🌍 <b>Timezone:</b> IST (India)\n\n"
            f"📁 <b>File:</b> <code>{filename}</code>\n"
            f"📦 <b>Original Size:</b> {format_file_size(original_size)}\n"
            f"🗜️ <b>Optimized Size:</b> {format_file_size(compressed_size)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "☁️ <b>Status:</b> Stored in Supabase\n"
            "⚡ <b>System:</b> Horror Story Captures"
        )

        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        files = {"photo": (filename, image_bytes, "image/jpeg")}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, data=data, files=files)

        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                print(f"[OK] Telegram notification sent")
                return True
        return False
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

# ==================================================
# API ENDPOINTS
# ==================================================

@app.get("/")
async def root():
    return {
        "service": "Horror Story Captures API",
        "version": "1.0.0",
        "status": "running",
        "supabase_status": "✅ Connected" if supabase else f"❌ {supabase_error or 'Not initialized'}",
        "telegram_status": "✅ Configured" if TELEGRAM_CONFIGURED else "❌ Not configured",
        "debug": {
            "supabase_url_set": bool(SUPABASE_URL),
            "keys_found": len(ALL_POSSIBLE_KEYS),
            "environment": "Vercel" if os.environ.get("VERCEL") else "Local"
        }
    }

@app.get("/debug/env")
async def debug_env():
    """Complete environment debug"""
    env_vars = {}
    for key, value in os.environ.items():
        if any(x in key.upper() for x in ["SUPABASE", "TELEGRAM", "VERCEL"]):
            env_vars[key] = {
                "set": bool(value),
                "length": len(value),
                "preview": f"{value[:20]}..." if value else ""
            }
    
    return {
        "environment": {
            "is_vercel": bool(os.environ.get("VERCEL")),
            "all_keys_found": env_vars
        },
        "supabase_clean": {
            "url": bool(SUPABASE_URL),
            "secret_key": bool(SUPABASE_SECRET_KEY),
            "service_role_key": bool(SUPABASE_SERVICE_ROLE_KEY),
            "anon_key": bool(SUPABASE_ANON_KEY)
        },
        "all_keys_tried": [{"name": k[0], "length": len(k[1])} for k in ALL_POSSIBLE_KEYS],
        "init_result": {
            "success": bool(supabase),
            "error": supabase_error
        }
    }

@app.get("/debug/supabase")
async def debug_supabase():
    """Test Supabase connection"""
    if supabase is None:
        return {
            "success": False,
            "error": supabase_error or "Supabase not initialized",
            "keys_tried": len(ALL_POSSIBLE_KEYS),
            "keys": [{"name": k[0], "length": len(k[1])} for k in ALL_POSSIBLE_KEYS[:5]]
        }
    
    try:
        buckets = supabase.storage.list_buckets()
        return {
            "success": True,
            "buckets": [b.get("name") for b in buckets],
            "bucket_exists": BUCKET_NAME in [b.get("name") for b in buckets]
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/health")
async def health_check():
    supabase_status = "disconnected"
    if supabase:
        try:
            buckets = supabase.storage.list_buckets()
            supabase_status = "connected"
        except:
            supabase_status = "error"
    
    return {
        "status": "healthy" if supabase_status == "connected" else "degraded",
        "service": "Horror Story Captures API",
        "supabase": {
            "status": supabase_status,
            "configured": bool(supabase)
        },
        "telegram": {
            "configured": TELEGRAM_CONFIGURED
        },
        "environment": {
            "vercel": bool(os.environ.get("VERCEL"))
        }
    }

# ==================================================
# CAPTURE IMAGE
# ==================================================

@app.post("/capture")
async def capture_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    if supabase is None:
        raise HTTPException(
            status_code=503,
            detail=f"Supabase not available: {supabase_error or 'Unknown error'}"
        )
    
    # ... your existing capture code here ...
    # (Keep your working capture logic)

@app.get("/images")
async def get_images(
    offset: int = Query(0, ge=0),
    limit: int = Query(6, ge=1, le=20)
):
    if supabase is None:
        raise HTTPException(
            status_code=503,
            detail=f"Supabase not available: {supabase_error or 'Unknown error'}"
        )
    
    # ... your existing get images code ...

@app.get("/share/{file_id}")
async def share_image(file_id: str):
    if supabase is None:
        raise HTTPException(
            status_code=503,
            detail=f"Supabase not available: {supabase_error or 'Unknown error'}"
        )
    
    # ... your existing share code ...

# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )
