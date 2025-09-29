from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

import os
import logging
from dotenv import load_dotenv

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    FollowEvent,
    UnfollowEvent
)
from pathlib import Path
from typing import List, Dict
from fastapi import UploadFile, File
from fastapi.staticfiles import StaticFiles
from uuid import uuid4
import shutil


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise ValueError("Missing LINE credentials in environment variables")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


app = FastAPI()

USERS_FILE = "users.txt"


def ensure_users_file_exists():
    """สร้างไฟล์ users.txt ถ้ายังไม่มี"""
    if not Path(USERS_FILE).exists():
        try:
            Path(USERS_FILE).touch()
            logger.info(f"Created {USERS_FILE}")
        except Exception as e:
            logger.error(f"Error creating {USERS_FILE}: {e}")

def read_user_ids() -> List[str]:
    """อ่าน user_id ทั้งหมดจากไฟล์"""
    ensure_users_file_exists()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = [line.strip() for line in f if line.strip()]
        return users
    except Exception as e:
        logger.error(f"Error reading users: {e}")
        return []

def save_user_id(user_id: str) -> bool:
    """บันทึก user_id ลงไฟล์ (ไม่ซ้ำ)"""
    try:
        existing_users = set(read_user_ids())
        
        if user_id not in existing_users:
            with open(USERS_FILE, "a", encoding="utf-8") as f:
                f.write(user_id + "\n")
            logger.info(f"Saved new user_id: {user_id}")
            return True
        else:
            logger.info(f"User_id already exists: {user_id}")
            return False
    except Exception as e:
        logger.error(f"Error saving user_id: {e}")
        return False

def remove_user_id(user_id: str) -> bool:
    """ลบ user_id จากไฟล์"""
    try:
        users = read_user_ids()
        if user_id in users:
            users.remove(user_id)
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                for user in users:
                    f.write(user + "\n")
            logger.info(f"Removed user_id: {user_id}")
            return True
        else:
            logger.info(f"User_id not found: {user_id}")
            return False
    except Exception as e:
        logger.error(f"Error removing user_id: {e}")
        return False

def send_reply_message(reply_token: str, text: str) -> bool:
    """ส่งข้อความตอบกลับ"""
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text)]
                )
            )
        return True
    except Exception as e:
        logger.error(f"Error sending reply message: {e}")
        return False

def send_push_message(user_id: str, text: str) -> bool:
    """ส่งข้อความ push"""
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=text)]
                )
            )
        return True
    except Exception as e:
        logger.error(f"Error sending push message to {user_id}: {e}")
        return False

# --- API Endpoints ---

@app.get("/")
def home():
    user_count = len(read_user_ids())
    return {
        "message": "LINE Bot is running",
        "total_users": user_count,
        "status": "active"
    }

@app.get("/check-config")
def check_config():
    if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
        return {"error": "Configuration incomplete"}
    
    users_file_exists = Path(USERS_FILE).exists()
    user_count = len(read_user_ids())
    
    return {
        "message": "Configuration OK",
        "users_file_exists": users_file_exists,
        "total_users": user_count
    }

@app.get("/users")
def get_users():
    """ดึงรายการ user_id ทั้งหมด"""
    users = read_user_ids()
    return {
        "total_users": len(users),
        "users": users
    }

@app.get("/users/count")
def get_user_count():
    """ดึงจำนวน user_id ทั้งหมด"""
    user_count = len(read_user_ids())
    return {"total_users": user_count}

@app.post("/broadcast")
async def broadcast_message(request: Dict):
    """ส่งข้อความไปยัง user ทั้งหมด"""
    try:
        text = request.get("text", "")
        if not text:
            raise HTTPException(status_code=400, detail="Message text is required")
        
        user_ids = read_user_ids()
        if not user_ids:
            return {"message": "No users to send message to", "total_users": 0}
        
        success_count = 0
        failed_users = []
        
        for user_id in user_ids:
            if send_push_message(user_id, text):
                success_count += 1
            else:
                failed_users.append(user_id)
        
        return {
            "message": f"Broadcast completed",
            "total_users": len(user_ids),
            "success_count": success_count,
            "failed_count": len(failed_users),
            "failed_users": failed_users
        }
    
    except Exception as e:
        logger.error(f"Error in broadcast: {e}")
        raise HTTPException(status_code=500, detail=f"Error broadcasting: {e}")


@app.delete("/users/{user_id}")
def delete_user(user_id: str):
    """ลบ user_id"""
    if remove_user_id(user_id):
        return {"message": f"User {user_id} removed successfully"}
    else:
        raise HTTPException(status_code=404, detail="User not found")

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        logger.error("Missing X-Line-Signature header")
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature")

    body = await request.body()
    body_text = body.decode("utf-8")
    
    logger.info(f"Received webhook: {body_text}")
    
    reply_text = 'Thank you!'

    try:
        handler.handle(body_text, signature)
        logger.info("Webhook handled successfully")
        reply_text = "Webhook handled successfully"
    except InvalidSignatureError:
        logger.error("Invalid signature error")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Unexpected error handling webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

    return JSONResponse(content={"message": reply_text})

# --- LINE Webhook Handlers ---

@handler.add(FollowEvent)
def handle_follow(event):
    """จัดการเมื่อมีคนเพิ่มเพื่อน"""
    user_id = event.source.user_id
    logger.info(f"New follower: {user_id}")
    
    # เก็บ user_id
    is_new_user = save_user_id(user_id)
    
    # ส่งข้อความต้อนรับ
    if is_new_user:
        welcome_text = "🎉 สวัสดีครับ! ขอบคุณที่เพิ่มเพื่อน\n\nคุณสามารถส่งข้อความมาคุยกับเราได้เลยนะครับ!"
    else:
        welcome_text = "ยินดีต้อนรับกลับครับ! 😊"
    
    send_reply_message(event.reply_token, welcome_text)

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    """จัดการเมื่อมีคนยกเลิกเพิ่มเพื่อน"""
    user_id = event.source.user_id
    logger.info(f"User unfollowed: {user_id}")
    remove_user_id(user_id)

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """จัดการข้อความ"""
    user_id = event.source.user_id
    text = event.message.text.strip()
    logger.info(f"Message from {user_id}: {text}")
    
    # เก็บ user_id (กรณีที่เคยลบออกแล้วมาส่งข้อความ)
    save_user_id(user_id)
    
    # ตอบกลับตามข้อความ
    if text.lower() in ["สวัสดี", "hello", "hi", "หวัดดี"]:
        reply_text = f"สวัสดีครับ! 😊\nUser ID: {user_id}"
    elif text.lower() in ["ขอบคุณ", "thank you", "thanks"]:
        reply_text = "ยินดีครับ! มีอะไรให้ช่วยอีกไหมครับ? 🙏"
    elif "user id" in text.lower():
        reply_text = f"User ID ของคุณคือ: {user_id}"

    
    send_reply_message(event.reply_token, reply_text)

if __name__ == "__main__":
    import uvicorn
    
    ensure_users_file_exists()
    
    uvicorn.run("main:app", host="0.0.0.0", port=3000, reload=True)