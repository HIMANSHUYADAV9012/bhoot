import os
import io
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    UploadFile,
    HTTPException,
    Query,
    BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from PIL import Image
from supabase import create_client

# ==================================================

# ENVIRONMENT

# ==================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ==================================================

# CONFIGURATION

# ==================================================

BUCKET_NAME = "captures"

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

IST = ZoneInfo("Asia/Kolkata")

# ==================================================

# SUPABASE

# ==================================================

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL environment variable is missing")

if not SUPABASE_SECRET_KEY:
    raise RuntimeError(
        "SUPABASE_SECRET_KEY environment variable is missing"
    )

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)

print("[OK] Supabase configured successfully")

# ==================================================

# TELEGRAM CONFIGURATION

# ==================================================

TELEGRAM_CONFIGURED = bool(
    BOT_TOKEN
    and CHAT_ID
    and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE"
    and CHAT_ID != "YOUR_CHAT_ID_HERE"
)

if TELEGRAM_CONFIGURED:
    print("[OK] Telegram configured successfully")
else:
    print("[WARNING] Telegram configuration missing")

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

# IMAGE COMPRESSION

# ==================================================

def compress_image(
    image_bytes: bytes,
    max_size=(1600, 1600),
    quality=82
) -> bytes:
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Verify uploaded file is a valid image
        img.verify()

        # Re-open image after verify()
        img = Image.open(io.BytesIO(image_bytes))

        # Convert image to RGB because final output is JPEG
        if img.mode == "RGBA":
            background = Image.new(
                "RGB",
                img.size,
                (255, 255, 255)
            )

            background.paste(
                img,
                mask=img.getchannel("A")
            )

            img = background

        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize while keeping original aspect ratio
        img.thumbnail(
            max_size,
            Image.LANCZOS
        )

        output = io.BytesIO()

        img.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True
        )

        return output.getvalue()

    except Exception as e:
        print(
            f"[IMAGE ERROR] "
            f"{type(e).__name__}: {str(e)}"
        )

        raise HTTPException(
            status_code=400,
            detail="Invalid or corrupted image"
        )

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
        return False

    try:
        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendPhoto"
        )

        capture_time = get_ist_time()

        caption = (
            "📸 <b>NEW IMAGE CAPTURED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 <b>Capture ID:</b> <code>{capture_id}</code>\n"
            f"🕒 <b>Captured:</b> "
            f"{capture_time.strftime('%d %b %Y, %I:%M:%S %p')}\n"
            "🌍 <b>Timezone:</b> IST (India)\n\n"
            f"📁 <b>File:</b> <code>{filename}</code>\n"
            f"📦 <b>Original Size:</b> "
            f"{format_file_size(original_size)}\n"
            f"🗜️ <b>Optimized Size:</b> "
            f"{format_file_size(compressed_size)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "☁️ <b>Status:</b> Stored in Supabase\n"
            "⚡ <b>System:</b> Horror Story Captures"
        )

        data = {
            "chat_id": CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML"
        }

        files = {
            "photo": (
                filename,
                image_bytes,
                "image/jpeg"
            )
        }

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:

            response = await client.post(
                url,
                data=data,
                files=files
            )

        if response.status_code == 200:
            result = response.json()

            if result.get("ok"):
                print(
                    f"[OK] Telegram notification sent "
                    f"for capture {capture_id}"
                )
                return True

            print(
                "[TELEGRAM API ERROR]",
                result.get("description")
            )
            return False

        print(
            "[TELEGRAM HTTP ERROR]",
            response.status_code,
            response.text[:300]
        )

        return False

    except Exception as e:
        print(
            f"[TELEGRAM ERROR] "
            f"{type(e).__name__}: {str(e)}"
        )

        return False

# ==================================================

# CAPTURE IMAGE

# ==================================================

@app.post("/capture")
async def capture_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    storage_path = None

    try:
        # Validate file content type
        if file.content_type not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=415,
                detail=(
                    "Only JPEG, PNG and WEBP "
                    "images are allowed"
                )
            )

        # Read image with size protection
        contents = await file.read(
            MAX_FILE_SIZE + 1
        )

        if not contents:
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is empty"
            )

        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Image is too large. "
                    "Maximum allowed size is 10 MB."
                )
            )

        original_size = len(contents)

        # Compress image
        compressed = compress_image(contents)

        compressed_size = len(compressed)

        # Generate unique capture ID
        file_id = str(uuid.uuid4())

        timestamp = datetime.now(timezone.utc)

        filename = f"capture_{file_id}.jpg"
        storage_path = filename

        # Upload image to Supabase Storage
        supabase.storage.from_(
            BUCKET_NAME
        ).upload(
            path=storage_path,
            file=compressed,
            file_options={
                "content-type": "image/jpeg",
                "upsert": "false"
            }
        )

        # Save image metadata in Supabase Database
        db_response = supabase.table(
            "images"
        ).insert({
            "id": file_id,
            "filename": filename,
            "storage_path": storage_path,
            "timestamp": timestamp.isoformat()
        }).execute()

        # Verify database insert
        if not db_response.data:
            try:
                supabase.storage.from_(
                    BUCKET_NAME
                ).remove([storage_path])
            except Exception:
                pass

            raise HTTPException(
                status_code=500,
                detail="Failed to save image metadata"
            )

        # Send image to Telegram
        if TELEGRAM_CONFIGURED:
            background_tasks.add_task(
                send_photo_to_telegram,
                compressed,
                filename,
                file_id,
                original_size,
                compressed_size
            )

        print(
            f"[OK] Capture saved successfully: {file_id}"
        )

        return {
            "success": True,
            "message": "Image captured and stored successfully",
            "id": file_id,
            "filename": filename,
            "original_size": original_size,
            "compressed_size": compressed_size,
            "telegram_configured": TELEGRAM_CONFIGURED
        }

    except HTTPException:
        raise

    except Exception as e:
        print(
            f"[CAPTURE ERROR] "
            f"{type(e).__name__}: {str(e)}"
        )

        # Remove uploaded image if database operation fails
        if storage_path:
            try:
                supabase.storage.from_(
                    BUCKET_NAME
                ).remove([storage_path])
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail="Image capture failed"
        )

# ==================================================

# GET IMAGES

# ==================================================

@app.get("/images")
async def get_images(
    offset: int = Query(0, ge=0),
    limit: int = Query(6, ge=1, le=20)
):
    try:
        start = offset
        end = offset + limit - 1

        response = (
            supabase
            .table("images")
            .select(
                "id, filename, timestamp",
                count="exact"
            )
            .order(
                "timestamp",
                desc=True
            )
            .range(start, end)
            .execute()
        )

        images = []

        for image in response.data or []:
            images.append({
                "id": image["id"],
                "url": f"/share/{image['id']}",
                "timestamp": image["timestamp"]
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
        print(
            f"[GET IMAGES ERROR] "
            f"{type(e).__name__}: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to fetch images"
        )

# ==================================================

# SHARE IMAGE

# ==================================================

@app.get("/share/{file_id}")
async def share_image(file_id: str):
    try:
        response = (
            supabase
            .table("images")
            .select("storage_path")
            .eq("id", file_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Image not found"
            )

        storage_path = response.data[0]["storage_path"]

        public_url = (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .get_public_url(storage_path)
        )

        return RedirectResponse(
            url=public_url,
            status_code=307
        )

    except HTTPException:
        raise

    except Exception as e:
        print(
            f"[SHARE ERROR] "
            f"{type(e).__name__}: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to load image"
        )

# ==================================================

# HEALTH CHECK

# ==================================================

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "Horror Story Captures API",
        "storage": "supabase",
        "telegram_configured": TELEGRAM_CONFIGURED,
        "supabase_configured": bool(
            SUPABASE_URL
            and SUPABASE_SECRET_KEY
        )
    }
