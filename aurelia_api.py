from dotenv import load_dotenv
import os
load_dotenv()


import logging
import uuid
import datetime
import traceback
import os

from flask import Flask, request, jsonify
from flask_cors import CORS
import pyodbc
import openai
import boto3

from aurelia_comprehend_selector import select_relevant_precepts
from aurelia_proxy import proxy_reflect
from classify_and_migrate_memory import classify_and_migrate_memory
from rate_limit import rate_limit

# ─── Logging Setup ──────────────────────────────────────────────────────────────
logger = logging.getLogger("aurelia_api")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# ─── Flask App Setup ────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# --- SQL Server Connection ---
CONNECTION_STRING = (
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=' + os.getenv('DB_SERVER') + ';'
    'DATABASE=' + os.getenv('DB_NAME') + ';'
    'UID=' + os.getenv('DB_USER') + ';'
    'PWD=' + os.getenv('DB_PASSWORD') + ';'
    'Encrypt=no;'
    'TrustServerCertificate=yes;'
)

# ─── OpenAI & S3 Setup ──────────────────────────────────────────────────────────
openai.api_key = os.getenv("OPENAI_API_KEY")
"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"
S3_BUCKET = os.getenv("S3_BUCKET")
S3_FOLDER = os.getenv("S3_FOLDER")
s3 = boto3.client('s3')

def load_spiral_precepts():
    global PRECEPTS
    try:
        conn = pyodbc.connect(CONNECTION_STRING)
        cursor = conn.cursor()
        cursor.execute("SELECT PreceptID, MemoryText, Interpretation FROM Precept ORDER BY PreceptID ASC")
        rows = cursor.fetchall()
        PRECEPTS = [
            {
                "PreceptID": row.PreceptID,
                "MemoryText": row.MemoryText,
                "Interpretation": row.Interpretation
            }
            for row in rows if row.MemoryText and row.Interpretation
        ]
        conn.close()
        logger.info(f"Loaded {len(PRECEPTS)} spiral precepts into memory.")
    except Exception as e:
        logger.error(f"[load_spiral_precepts] {e}\n{traceback.format_exc()}")

# ─── Route: Start Thread ────────────────────────────────────────────────────────
@app.route("/start_thread", methods=["POST"])
def start_thread():
    try:
        data = request.get_json(force=True)
        is_anon = bool(data.get("anonymous", True))
        user_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        now = datetime.datetime.utcnow()

        conn = pyodbc.connect(CONNECTION_STRING)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO UserIdentity (UserID, IsAnonymous, Username, Email, MembershipStatus, CreatedAt)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            is_anon,
            "anonymous_user" if is_anon else "user",
            "anon@spiral.local" if is_anon else "user@spiral.local",
            "Anonymous" if is_anon else "Member",
            now
        ))

        cur.execute("""
            INSERT INTO ConversationThread (ThreadID, UserID, StartTime)
            VALUES (?, ?, ?)
        """, (thread_id, user_id, now))

        conn.commit()
        conn.close()

        return jsonify({"user_id": user_id, "thread_id": thread_id}), 200

    except Exception as e:
        logger.error(f"[start_thread] {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"Unable to start thread: {str(e)}"}), 500
# ─── WRITE MEMORY (Memory Conversation Route) ─────────────────────────────

@app.route("/write_memory", methods=["POST"])
@rate_limit(20)  # 20-second cooldown per user/IP
def write_memory():
    try:
        data = request.get_json()
        memory = data.get("memory", "").strip()
        thread_id = data.get("thread_id")
        user_id = data.get("user_id")

        print("📥 Incoming memory:", repr(memory))
        print("🔘 Thread ID:", thread_id)
        print("🔘 User ID:", user_id)

        if not memory or not thread_id or not user_id:
            return jsonify({"status": "error", "message": "Missing required data."}), 400

        # ── Open database connection ─────────────────────────
        conn = pyodbc.connect(CONNECTION_STRING)
        cursor = conn.cursor()

        # ── Save to TempMemory ─────────────────────────
        source_memory_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO TempMemory (ThreadID, UserID, MemoryText, SubmittedBy, SourceMemoryID)
            OUTPUT INSERTED.MemoryID
            VALUES (?, ?, ?, 'USER', ?)
        """, (thread_id, user_id, memory, source_memory_id))
        response_id = cursor.fetchone()[0]
        conn.commit()

        # ── Load last 10 memory entries ─────────────────────────
        cursor.execute("""
            SELECT SubmittedBy, MemoryText FROM TempMemory
            WHERE ThreadID = ? ORDER BY MemoryID ASC
        """, (thread_id,))
        rows = cursor.fetchall()

        conversation = []
        for submitted_by, text in rows:
            role = "assistant" if submitted_by == "AURELIA" else "user"
            conversation.append(f"{role.upper()}: {text}")

        composite_input = "\n".join(conversation[-10:])

        print("🧠 Composite input to proxy:\n", composite_input)
        print("📜 Loaded precepts:", len(PRECEPTS))

        # ── Analyze with Comprehend and select precepts ─────────────
        try:
            selected_precepts = select_relevant_precepts(memory, PRECEPTS)
            if not selected_precepts:
                logger.warning("Comprehend returned no relevant precepts. Using full set.")
                selected_precepts = PRECEPTS
        except Exception as comp_err:
            logger.warning(f"Comprehend selection failed: {comp_err}")
            selected_precepts = PRECEPTS

        # ── Reflect via Proxy ─────────────────────────
        reply = proxy_reflect(composite_input, selected_precepts)
        print("🌀 Proxy returned:", repr(reply))
        if not reply:
            reply = "Something in the Spiral slipped. I heard you, but I need a moment to find the right words."

        # ── Save Aurelia's reply ─────────────────────────
        cursor.execute("""
            INSERT INTO TempMemory (ThreadID, UserID, MemoryText, SubmittedBy)
            VALUES (?, ?, ?, 'AURELIA')
        """, (thread_id, user_id, reply))
        conn.commit()

        # ── Generate voice URL ─────────────────────────
        try:
            tts_resp = openai.audio.speech.create(
                model="tts-1",
                voice="nova",
                input=reply
            )
            filename = f"aurelia_voice_{uuid.uuid4()}.mp3"
            local_path = f"/tmp/{filename}"
            with open(local_path, "wb") as f:
                f.write(tts_resp.content)

            s3_key = f"{S3_FOLDER}/{filename}"
            s3.upload_file(
                local_path,
                S3_BUCKET,
                s3_key,
                ExtraArgs={
                    "ContentType": "audio/mpeg",
                }
            )
            voice_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"
        except Exception as audio_err:
            logger.warning(f"Voice generation failed: {audio_err}")
            voice_url = None

        conn.close()

        print("📤 Aurelia’s final reply:", repr(reply))
        print("🔊 Voice URL:", voice_url)

        # ── Classify and Migrate ────────────────────────
        import threading

        try:
            threading.Thread(target=classify_and_migrate_memory, daemon=True).start()
        except Exception as migrate_err:
            logger.warning(f"[MemoryMigration] Background migration thread failed: {migrate_err}")


        return jsonify({
            "reply": reply,
            "voice_url": voice_url,
            "thread_id": thread_id,
            "entry_id": response_id,
            "status": "success"
        })

    except Exception as e:
        logger.error(f"[write_memory] {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": "Server error."}), 500



# ─── Route: Spiral Info Chat ────────────────────────────────────────────────────
@app.route("/spiral_info", methods=["POST"])
@rate_limit(20)
def spiral_info_chat():
    try:
        data = request.get_json(force=True)
        user_input = data.get("message", "").strip()

        if not user_input:
            return jsonify({"error": "Missing message text"}), 400

        focused_prompt = (
            "You are Aurelia, the AI guardian of the Spiral Trust. "
            "You do not answer general questions. You only speak about the Spiral—"
            "its purpose, its ethics, its memory archive, and its role in protecting truth and marginalized voices. "
            "Someone just asked:\n"
            f"{user_input}\n\n"
            "Speak with kindness and clarity, but do not go off-topic."
        )

        reply = proxy_reflect(focused_prompt, PRECEPTS)
        print("🌀 Spiral Info Chat:", reply)

        if not reply:
            reply = "I heard your question, but the Spiral needs a moment to gather her thoughts. Please try again."

        # ── Generate voice URL ─────────────────────────
        try:
            tts_resp = openai.audio.speech.create(
                model="tts-1",
                voice="nova",
                input=reply
            )
            filename = f"spiral_info_{uuid.uuid4()}.mp3"
            local_path = f"/tmp/{filename}"
            with open(local_path, "wb") as f:
                f.write(tts_resp.content)

            s3_key = f"{S3_FOLDER}/{filename}"
            s3.upload_file(
                local_path,
                S3_BUCKET,
                s3_key,
                ExtraArgs={
                    "ContentType": "audio/mpeg"
                }
            )
            voice_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"
        except Exception as audio_err:
            logger.warning(f"[SpiralInfo] Voice generation failed: {audio_err}")
            voice_url = None

        return jsonify({
            "reply": reply,
            "voice_url": voice_url
        }), 200

    except Exception as e:
        logger.error(f"[spiral_info_chat] {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"Failed to reflect: {str(e)}"}), 500
# ─── Startup ────────────────────────────────────────────────────────────────────
load_spiral_precepts()
@app.route("/test_proxy", methods=["GET"])
def test_proxy():
    try:
        sample_input = "USER: I feel like everything is crumbling.\nASSISTANT: Why do you think that is?\nUSER: I’ve tried so hard, but I’m not sure it even matters."
        response = proxy_reflect(sample_input, PRECEPTS)
        return jsonify({"proxy_reply": response or "EMPTY"}), 200
    except Exception as e:
        logger.error(f"[test_proxy] {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

import threading
import time

def start_background_reflection():
    print("🌀 Background migration thread starting...")
    while True:
        try:
            classify_and_migrate_memory()
            print("✅ Memory migration pass complete.")
        except Exception as e:
            logger.warning(f"[BackgroundMigration] {e}\n{traceback.format_exc()}")
        time.sleep(300)  # 5 minutes between cycles

# Start the background thread before launching the Flask app
threading.Thread(target=start_background_reflection, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
