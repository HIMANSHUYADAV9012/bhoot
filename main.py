import os
import io
import uuid
import json
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
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

# ==================================================
# ENVIRONMENT SETUP - Production Ready
# ==================================================

# Try loading .env only in local development
try:
    from dotenv import load_dotenv
    if os.path.exists(".env"):
        load_dotenv()
        print("[INFO] Loaded .env file")
except ImportError:
    print("[INFO] python-dotenv not available, using system env")

# Get all environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Debug information (remove in production)
print("=" * 50)
print("ENVIRONMENT VARIABLES STATUS:")
print(f"SUPABASE_URL: {'✅ SET' if SUPABASE_URL else '❌ MISSING'}")
print(f"SUPABASE_SECRET_KEY: {'✅ SET' if SUPABASE_SECRET_KEY else '❌ MISSING'}")
print(f"TELEGRAM_BOT_TOKEN: {'✅ SET' if BOT_TOKEN else '❌ MISSING'}")
print(f"TELEGRAM_CHAT_ID: {'✅ SET' if CHAT_ID else '❌ MISSING'}")
print("=" * 50)

# ==================================================
# APPLICATION
# ==================================================

app = FastAPI(
    title="Horror Story Captures API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================================================
# GLOBAL EXCEPTION HANDLERS
# ==================================================

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "Validation error",
            "details": str(exc)
        }
    )

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    print(f"[GLOBAL ERROR] {type(exc).__name__}: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "message": str(exc) if os.getenv("DEBUG") else None
        }
    )

# ==================================================
# CONFIGURATION
# ==================================================

BUCKET_NAME = "captures"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
IST = ZoneInfo("Asia/Kolkata")

# ==================================================
# SUPABASE - Robust Initialization
# ==================================================

supabase = None
supabase_error = None

def init_supabase():
    """Initialize Supabase with proper error handling"""
    global supabase, supabase_error
    
    try:
        # Try importing supabase
        try:
            from supabase import create_client
        except ImportError as e:
            supabase_error = f"Supabase package not installed: {e}"
            print(f"[ERROR] {supabase_error}")
            return None
        
        # Validate credentials
        if not SUPABASE_URL:
            supabase_error = "SUPABASE_URL is empty or not set"
            print(f"[ERROR] {supabase_error}")
            return None
            
        if not SUPABASE_SECRET_KEY:
            supabase_error = "SUPABASE_SECRET_KEY is empty or not set"
            print(f"[ERROR] {supabase_error}")
            return None
        
        # Try different key variations
        keys_to_try = [
            SUPABASE_SECRET_KEY,
            SUPABASE_SERVICE_ROLE_KEY,
            os.getenv("SUPABASE_ANON_KEY", ""),
            os.getenv("SUPABASE_KEY", "")
        ]
        
        # Remove empty keys
        keys_to_try = [k for k in keys_to_try if k]
        
        last_error = None
        for key in keys_to_try:
            try:
                print(f"[INFO] Trying to initialize Supabase with key: {key[:10]}...")
                client = create_client(SUPABASE_URL, key)
                
                # Test connection with a simple query
                try:
                    # Try to list buckets to verify connection
                    test = client.storage.list_buckets()
                    print(f"[OK] Supabase connection successful with key: {key[:10]}...")
                    return client
                except Exception as test_error:
                    print(f"[WARNING] Connection test failed: {test_error}")
                    last_error = test_error
                    continue
                    
            except Exception as e:
                print(f"[WARNING] Failed with key {key[:10]}...: {e}")
                last_error = e
                continue
        
        supabase_error = f"All Supabase initialization attempts failed. Last error: {last_error}"
        print(f"[ERROR] {supabase_error}")
        return None
        
    except Exception as e:
        supabase_error = f"Unexpected error initializing Supabase: {e}"
        print(f"[ERROR] {supabase_error}")
        return None

# Initialize Supabase
print("[INFO] Initializing Supabase...")
supabase = init_supabase()
if supabase:
    print("[✅] Supabase initialized successfully")
else:
    print(f"[❌] Supabase initialization failed: {supabase_error}")

# ==================================================
# TELEGRAM CONFIGURATION
# ==================================================

TELEGRAM_CONFIGURED = bool(
    BOT_TOKEN and CHAT_ID and
    BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" and
    CHAT_ID != "YOUR_CHAT_ID_HERE" and
    len(BOT_TOKEN) > 10 and
    len(CHAT_ID) > 3
)

print(f"[INFO] Telegram configured: {TELEGRAM_CONFIGURED}")

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

# ==================================================
# IMAGE COMPRESSION - With Fallback
# ==================================================

def compress_image(image_bytes: bytes, max_size=(1600, 1600), quality=82) -> bytes:
    """Compress image with multiple fallback options"""
    try:
        from PIL import Image
    except ImportError:
        print("[WARNING] PIL not available, returning original image")
        return image_bytes
    
    try:
        # Open and verify image
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
        
        # Re-open for processing
        img = Image.open(io.BytesIO(image_bytes))
        
        # Convert to RGB
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.getchannel("A"))
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
        
        # Resize maintaining aspect ratio
        img.thumbnail(max_size, Image.LANCZOS)
        
        # Save compressed
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        compressed = output.getvalue()
        
        # If compression actually increased size, return original
        if len(compressed) > len(image_bytes):
            print(f"[INFO] Compression increased size, returning original")
            return image_bytes
            
        return compressed
        
    except Exception as e:
        print(f"[IMAGE ERROR] {type(e).__name__}: {str(e)}")
        return image_bytes  # Return original on error

# ==================================================
# TELEGRAM NOTIFICATION
# ==================================================

async def send_photo_to_telegram(
    image_bytes: bytes,
    filename: str,
    capture_id: str,
    original_size: int,
    compressed_size: int
) -> bool:
    if not TELEGRAM_CONFIGURED:
        print("[INFO] Telegram not configured, skipping notification")
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
                print(f"[OK] Telegram notification sent for {capture_id}")
                return True
            else:
                print(f"[TELEGRAM API ERROR] {result.get('description')}")
                return False
        else:
            print(f"[TELEGRAM HTTP ERROR] {response.status_code}")
            return False

    except Exception as e:
        print(f"[TELEGRAM ERROR] {type(e).__name__}: {str(e)}")
        return False

# ==================================================
# API ENDPOINTS
# ==================================================

# Root endpoint
@app.get("/")
async def root():
    return {
        "service": "Horror Story Captures API",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "capture": "POST /capture",
            "images": "GET /images",
            "share": "GET /share/{file_id}",
            "health": "GET /health",
            "docs": "GET /docs"
        },
        "supabase_status": "✅ Connected" if supabase else f"❌ {supabase_error}",
        "telegram_status": "✅ Configured" if TELEGRAM_CONFIGURED else "❌ Not configured"
    }

# ==================================================
# CAPTURE IMAGE - Enhanced
# ==================================================

@app.post("/capture")
async def capture_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    # Check Supabase configuration
    if supabase is None:
        error_msg = f"Supabase not available: {supabase_error or 'Unknown error'}"
        print(f"[ERROR] {error_msg}")
        raise HTTPException(
            status_code=503,
            detail=error_msg
        )
    
    storage_path = None
    try:
        # Validate file type
        if file.content_type not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Only JPEG, PNG and WEBP images allowed. Got: {file.content_type}"
            )

        # Read file with size limit
        contents = await file.read(MAX_FILE_SIZE + 1)
        
        if not contents:
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is empty"
            )
            
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Image too large. Max: 10 MB, Got: {format_file_size(len(contents))}"
            )

        original_size = len(contents)
        
        # Compress image
        compressed = compress_image(contents)
        compressed_size = len(compressed)
        
        # Generate IDs
        file_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc)
        filename = f"capture_{file_id}.jpg"
        storage_path = filename

        print(f"[INFO] Uploading image {file_id} to Supabase...")
        
        # Upload to Supabase Storage
        try:
            response = supabase.storage.from_(BUCKET_NAME).upload(
                path=storage_path,
                file=compressed,
                file_options={
                    "content-type": "image/jpeg",
                    "upsert": "false"
                }
            )
            print(f"[OK] Upload successful: {response}")
        except Exception as upload_error:
            print(f"[ERROR] Storage upload failed: {upload_error}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload image: {str(upload_error)}"
            )

        # Save metadata to database
        print(f"[INFO] Saving metadata for {file_id}...")
        try:
            db_response = supabase.table("images").insert({
                "id": file_id,
                "filename": filename,
                "storage_path": storage_path,
                "timestamp": timestamp.isoformat()
            }).execute()
            
            if not db_response.data:
                raise Exception("No data returned from insert")
                
            print(f"[OK] Metadata saved successfully")
            
        except Exception as db_error:
            print(f"[ERROR] Database insert failed: {db_error}")
            # Clean up storage
            try:
                supabase.storage.from_(BUCKET_NAME).remove([storage_path])
                print(f"[INFO] Cleaned up storage after database failure")
            except:
                pass
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save metadata: {str(db_error)}"
            )

        # Send Telegram notification (background)
        if TELEGRAM_CONFIGURED:
            background_tasks.add_task(
                send_photo_to_telegram,
                compressed,
                filename,
                file_id,
                original_size,
                compressed_size
            )
            print(f"[INFO] Telegram notification queued")
        else:
            print(f"[INFO] Telegram notification skipped (not configured)")

        print(f"[✅] Capture complete: {file_id}")

        return {
            "success": True,
            "message": "Image captured and stored successfully",
            "id": file_id,
            "filename": filename,
            "original_size": original_size,
            "compressed_size": compressed_size,
            "storage_path": storage_path,
            "timestamp": timestamp.isoformat(),
            "telegram_notification": TELEGRAM_CONFIGURED
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[CAPTURE ERROR] {type(e).__name__}: {str(e)}")
        
        # Clean up storage on error
        if storage_path and supabase:
            try:
                supabase.storage.from_(BUCKET_NAME).remove([storage_path])
                print(f"[INFO] Cleaned up storage after error")
            except:
                pass
                
        raise HTTPException(
            status_code=500,
            detail=f"Image capture failed: {str(e)}"
        )

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
    
    try:
        response = supabase.table("images").select(
            "id, filename, timestamp",
            count="exact"
        ).order("timestamp", desc=True).range(offset, offset + limit - 1).execute()

        images = []
        for image in response.data or []:
            images.append({
                "id": image["id"],
                "url": f"/share/{image['id']}",
                "timestamp": image["timestamp"],
                "filename": image.get("filename", "unknown.jpg")
            })

        total = response.count or 0
        return {
            "success": True,
            "images": images,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total
        }

    except Exception as e:
        print(f"[GET IMAGES ERROR] {type(e).__name__}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch images: {str(e)}"
        )

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
    
    try:
        response = supabase.table("images").select("storage_path").eq("id", file_id).limit(1).execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail=f"Image with id '{file_id}' not found"
            )

        storage_path = response.data[0]["storage_path"]
        
        try:
            public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(storage_path)
            return RedirectResponse(url=public_url, status_code=307)
        except Exception as url_error:
            print(f"[URL ERROR] {url_error}")
            raise HTTPException(
                status_code=500,
                detail="Failed to generate image URL"
            )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SHARE ERROR] {type(e).__name__}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load image: {str(e)}"
        )

# ==================================================
# DEBUG ENDPOINT - Check Supabase Connection
# ==================================================

@app.get("/debug/supabase")
async def debug_supabase():
    """Debug endpoint to check Supabase connection"""
    if supabase is None:
        return {
            "success": False,
            "error": supabase_error or "Supabase not initialized",
            "env_vars": {
                "SUPABASE_URL": bool(SUPABASE_URL),
                "SUPABASE_SECRET_KEY": bool(SUPABASE_SECRET_KEY)
            }
        }
    
    try:
        # Test connection
        buckets = supabase.storage.list_buckets()
        return {
            "success": True,
            "message": "Supabase connection successful",
            "buckets": [b.get("name") for b in buckets],
            "bucket_exists": BUCKET_NAME in [b.get("name") for b in buckets]
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": type(e).__name__
        }

# ==================================================
# HEALTH CHECK
# ==================================================

@app.get("/health")
async def health_check():
    # Test Supabase connection
    supabase_status = "disconnected"
    if supabase:
        try:
            supabase.storage.list_buckets()
            supabase_status = "connected"
        except:
            supabase_status = "error"
    
    return {
        "status": "healthy" if supabase_status == "connected" else "degraded",
        "service": "Horror Story Captures API",
        "version": "1.0.0",
        "timestamp": datetime.now(IST).isoformat(),
        "supabase": {
            "status": supabase_status,
            "error": supabase_error if supabase is None else None
        },
        "telegram": {
            "configured": TELEGRAM_CONFIGURED
        },
        "environment": {
            "vercel": bool(os.getenv("VERCEL")),
            "production": os.getenv("ENVIRONMENT", "development")
        }
    }

# ==================================================
# MAIN - For local development
# ==================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )
