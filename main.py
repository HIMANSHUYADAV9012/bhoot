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
    BackgroundTasks,
    Request
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# ==================================================
# ENVIRONMENT - ULTRA CLEANING
# ==================================================

def clean_env_value(value):
    """Remove all whitespace, quotes, and invisible characters"""
    if not value:
        return ""
    value = value.strip()
    value = value.strip('"').strip("'")
    value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
    value = value.replace('\r', '').replace('\n', '').replace('\t', '')
    return value


print("=" * 70)
print("🔍 ENVIRONMENT VARIABLES DIAGNOSTIC")
print("=" * 70)

SUPABASE_URL = clean_env_value(os.environ.get("SUPABASE_URL", ""))
SUPABASE_SECRET_KEY = clean_env_value(os.environ.get("SUPABASE_SECRET_KEY", ""))
SUPABASE_SERVICE_ROLE_KEY = clean_env_value(os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
SUPABASE_ANON_KEY = clean_env_value(os.environ.get("SUPABASE_ANON_KEY", ""))
SUPABASE_KEY = clean_env_value(os.environ.get("SUPABASE_KEY", ""))

BOT_TOKEN = clean_env_value(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
CHAT_ID = clean_env_value(os.environ.get("TELEGRAM_CHAT_ID", ""))


def debug_key(name, value):
    if value:
        print(f"✅ {name}:")
        print(f"   Length: {len(value)}")
        print(f"   First 20 chars: {value[:20]}...")
        print(f"   Last 5 chars: ...{value[-5:]}")
        print(f"   Contains only valid chars: {bool(re.match(r'^[a-zA-Z0-9._-]+$', value))}")
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

ALL_POSSIBLE_KEYS = []

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


# ==================================================
# ORIGIN LOCK - SIRF FRONTEND ALLOWED
# ==================================================

ALLOWED_ORIGINS = [
    "https://payal-six-sandy.vercel.app",
]

# Local testing ke liye (Vercel pe automatically skip)
if not os.environ.get("VERCEL"):
    ALLOWED_ORIGINS += [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

# Public endpoints (origin check nahi hoga)
PUBLIC_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}


class OriginLockMiddleware(BaseHTTPMiddleware):
    """
    - Sirf allowed frontend se API calls accept karta hai.
    - /share/* ko <img> tag se load hone deta hai (Sec-Fetch-Dest: image).
    - Direct browser/curl/Postman sab block.
    """
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Preflight hamesha allow
        if request.method == "OPTIONS":
            return await call_next(request)

        # Public paths allow
        if path in PUBLIC_PATHS:
            return await call_next(request)

        origin = request.headers.get("origin", "")
        referer = request.headers.get("referer", "")
        sec_fetch_site = request.headers.get("sec-fetch-site", "")
        sec_fetch_mode = request.headers.get("sec-fetch-mode", "")
        sec_fetch_dest = request.headers.get("sec-fetch-dest", "")

        origin_ok = any(origin == o for o in ALLOWED_ORIGINS)
        referer_ok = any(referer.startswith(o + "/") or referer == o for o in ALLOWED_ORIGINS)

        # Browser requests me Sec-Fetch-* hote hain, curl/Postman me nahi
        is_browser_request = bool(sec_fetch_site or sec_fetch_mode)

        # ---- SPECIAL CASE: /share/* images ----
        # <img src="..."> se aane wali request me:
        #   Sec-Fetch-Dest: image
        # Direct browser open me: Sec-Fetch-Dest: document  -> BLOCK
        # Curl/Postman me: Sec-Fetch-Dest missing -> BLOCK
        if path.startswith("/share/"):
            if sec_fetch_dest == "image" and (
                referer_ok or sec_fetch_site in ("cross-site", "same-origin", "same-site")
            ):
                return await call_next(request)
            # warna neeche wale normal checks pe chala jayega -> block

        if not (origin_ok or referer_ok):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Forbidden: ye API sirf authorized frontend ke liye hai."
                }
            )

        if not is_browser_request:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Forbidden: direct access not allowed."
                }
            )

        return await call_next(request)


app.add_middleware(OriginLockMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
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
    global supabase, supabase_error

    print("\n🚀 STARTING SUPABASE INITIALIZATION...")

    try:
        from supabase import create_client
        print("✅ Supabase package imported successfully")
    except ImportError as e:
        supabase_error = f"Supabase package not installed: {e}"
        print(f"❌ {supabase_error}")
        return None

    if not SUPABASE_URL:
        supabase_error = "SUPABASE_URL is empty or not set"
        print(f"❌ {supabase_error}")
        return None

    print(f"✅ SUPABASE_URL: {SUPABASE_URL[:30]}...")

    if not ALL_POSSIBLE_KEYS:
        supabase_error = "No Supabase keys found in environment!"
        print(f"❌ {supabase_error}")
        return None

    print(f"✅ Found {len(ALL_POSSIBLE_KEYS)} potential API keys")

    last_error = None

    for key_name, key_value in ALL_POSSIBLE_KEYS:
        print(f"\n🔑 Trying {key_name}...")

        urls_to_try = [
            SUPABASE_URL,
            SUPABASE_URL.rstrip('/'),
            f"https://{SUPABASE_URL.replace('https://', '').split('.')[0]}.supabase.co",
        ]

        for url in set(urls_to_try):
            try:
                print(f"   Testing with URL: {url[:30]}...")
                client = create_client(url, key_value)

                test_passed = False
                test_errors = []

                try:
                    buckets = client.storage.list_buckets()
                    print(f"   ✅ Storage test passed! Found {len(buckets)} buckets")
                    test_passed = True
                    supabase = client
                    print(f"\n🎉 SUCCESS! Connected with {key_name}")
                    return client
                except Exception as e:
                    test_errors.append(f"Storage: {str(e)[:50]}")

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

                if not test_passed:
                    try:
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

                if "invalid" in error_msg.lower() or "api key" in error_msg.lower():
                    print(f"   ⚠️  {key_name} appears to be invalid")
                    continue

    supabase_error = f"All keys failed. Last error: {last_error}"
    print(f"\n❌ {supabase_error}")
    return None


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
# HELPER FUNCTIONS
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
        "supabase_status": "✅ Connected" if supabase else "❌ Not initialized",
        "telegram_status": "✅ Configured" if TELEGRAM_CONFIGURED else "❌ Not configured",
    }


@app.get("/health")
async def health_check():
    supabase_status = "disconnected"
    if supabase:
        try:
            supabase.storage.list_buckets()
            supabase_status = "connected"
        except Exception:
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


# ==================================================
# GET IMAGES
# ==================================================

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


# ==================================================
# SHARE IMAGE
# ==================================================

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
