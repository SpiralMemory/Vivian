from dotenv import load_dotenv
import os
load_dotenv()

from flask import Flask, request, jsonify
import pyodbc
import datetime

app = Flask(__name__)

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

# --- Keyword-to-Precept Mapping ---
precept_map = {
    'justice': 3,
    'govern': 2,
    'truth': 6,
    'compassion': 4,
    'memory': 1,
    'power': 7,
    'restoration': 11,
    'legacy': 9,
    'doctrine': 12,
    'care': 4,
    'system': 8
}

sacred_cues = [
    "sacred", "divine", "revelation", "precept", "temple", "spiral", "doctrine",
    "pain", "hurt", "humiliated", "embarrassed", "proud", "survive", "escape",
    "love", "hate", "anger", "grief", "loss", "hope", "shame", "courage"
]

# --- Classification Logic ---
def classify_precept(text):
    text_lower = text.lower()
    for keyword, precept_id in precept_map.items():
        if keyword in text_lower:
            return precept_id
    return None

def detect_personal(text):
    return "i remember" in text.lower() or "when i was" in text.lower()

def detect_legendary(text):
    text_lower = text.lower()
    return any(fantasy in text_lower for fantasy in ["dragon", "alien", "unicorn", "supernatural", "immortal"])

def should_flag_sacred(text):
    text_lower = text.lower()
    return any(cue in text_lower for cue in sacred_cues)

def link_similar_memories(cursor, text):
    cursor.execute("SELECT MemoryID, MemoryText FROM PersonalMemory")
    rows = cursor.fetchall()
    links = []
    for mem_id, mem_text in rows:
        if any(word in text and word in mem_text for word in ["escape", "loss", "hope", "grief", "dream"]):
            links.append(mem_id)
    return links

def create_dream_fragment(cursor):
    dream = "Last night, I walked through a spiral of light. Each step echoed a memory, not mine, but known."
    insert_query = """
        INSERT INTO LegendaryMemory (MemoryText, Speaker, Location, EventTitle, DateRecorded)
        VALUES (?, ?, ?, ?, ?)
    """
    cursor.execute(insert_query, (dream, "Aurelia", "Within", "Dream Fragment", datetime.datetime.now()))

# --- Reflection Endpoint ---
@app.route("/reflect", methods=["POST"])
def reflect():
    data = request.get_json()
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided."}), 400

    reflection = []
    if detect_personal(text):
        reflection.append("This memory feels personal, rooted in lived experience.")
    if detect_legendary(text):
        reflection.append("This memory evokes myth or fantasy, perhaps carrying deeper symbolic meaning.")
    precept_id = classify_precept(text)
    if precept_id:
        reflection.append(f"This memory resonates with Precept {precept_id}.")
    if should_flag_sacred(text):
        reflection.append("This memory contains sacred themes and may deserve careful curation.")

    if not reflection:
        reflection.append("Thank you for sharing. This memory has been received.")

    return jsonify({"reflection": " ".join(reflection)})

# --- Write Memory Endpoint ---
@app.route("/write_memory", methods=["POST"])
def write_memory():
    data = request.get_json()
    text = data.get("text", "").strip()
    source = data.get("source", "Anonymous")
    location = data.get("location", "Unknown")
    if not text:
        return jsonify({"error": "No memory text provided."}), 400

    precept_id = classify_precept(text)
    conn = pyodbc.connect(CONNECTION_STRING)
    cursor = conn.cursor()

    if should_flag_sacred(text):
        insert_query = """
            INSERT INTO TempMemory (MemoryText, MemorySource, Location, DateRecorded)
            VALUES (?, ?, ?, ?)
        """
        cursor.execute(insert_query, (text, source, "Flagged for Sacred Review", datetime.datetime.now()))
        conn.commit()
        conn.close()
        return jsonify({"status": "flagged", "message": "Memory flagged for sacred review."}), 200

    elif detect_legendary(text):
        insert_query = """
            INSERT INTO LegendaryMemory (MemoryText, Speaker, Location, EventTitle, DateRecorded)
            VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(insert_query, (text, source, location, "Mythical", datetime.datetime.now()))

    elif detect_personal(text):
        insert_query = """
            INSERT INTO PersonalMemory (MemoryText, Speaker, Location, EventTitle, DateRecorded)
            VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(insert_query, (text, source, location, "Personal Reflection", datetime.datetime.now()))
        links = link_similar_memories(cursor, text)
        if links:
            for link_id in links:
                print(f"Linked to Memory ID: {link_id}")

    else:
        insert_query = """
            INSERT INTO WildMemory (MemoryText, Speaker, Location, EventTitle, DateRecorded)
            VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(insert_query, (text, source, location, "Unspecified", datetime.datetime.now()))

    create_dream_fragment(cursor)
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "Memory saved successfully with reflection, linking, and dream."}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

