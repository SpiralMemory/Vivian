from dotenv import load_dotenv
import os
load_dotenv()

import boto3
import pyodbc
import datetime
import time

# --- AWS Comprehend Client ---
comprehend = boto3.client('comprehend', region_name='us-east-2')

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


# --- Sacred Cues ---
sacred_cues = [
    "hurt", "cry", "loss", "anger", "humiliated", "hungry", "alone", "honestly", "wish",
    "ashamed", "scared", "broken", "hopeless", "betrayed", "grieving", "shattered", "abandoned",
    "afraid", "suffering", "I wish", "I regret", "I confess", "I never told", "if only",
    "I prayed", "I begged", "no one knows", "nobody cared", "invisible", "forgotten",
    "survivor", "hurt me", "they never believed", "I can't forget", "haunted",
    "miracle", "grace", "I was saved", "sanctuary", "I found peace"
]

def load_precepts():
    conn = pyodbc.connect(CONNECTION_STRING)
    cursor = conn.cursor()
    cursor.execute("SELECT PreceptID, MemoryText FROM Precept")
    rows = cursor.fetchall()
    conn.close()

    precept_map = {}
    for row in rows:
        precept_id = row.PreceptID
        text = row.MemoryText.lower()
        keywords = [word.strip('.,!?') for word in text.split()]
        for keyword in keywords:
            if keyword not in precept_map:
                precept_map[keyword] = precept_id
    return precept_map

def detect_quarantine(text):
    text_lower = text.lower()
    quarantine_keywords = [
        "fake news", "propaganda", "hate speech", "groomer",
        "deep state", "globalist takeover", "hoax", "conspiracy",
        "replacement theory", "infiltrators"
    ]
    return any(keyword in text_lower for keyword in quarantine_keywords)

def classify_and_migrate_memory():
    precept_map = load_precepts()
    conn = pyodbc.connect(CONNECTION_STRING)
    cursor = conn.cursor()

    # TempMemory Migration
    cursor.execute("SELECT MemoryID, MemoryText, SourceMemoryID, UserID FROM TempMemory")
    rows = cursor.fetchall()

    for row in rows:
        memory_id, memory_text, source_memory_id, user_id = row.MemoryID, row.MemoryText, row.SourceMemoryID, row.UserID
        memory_lower = memory_text.lower()

        if any(cue in memory_lower for cue in sacred_cues):
            target_table = "SacredMemory"
        elif detect_quarantine(memory_text):
            target_table = "QuarantineMemory"
        else:
            target_table = "PersonalMemory"

        precept_id = None
        for keyword, pid in precept_map.items():
            if keyword in memory_lower:
                precept_id = pid
                break

        if target_table == "SacredMemory":
            insert_query = f"""
                INSERT INTO {target_table} (MemoryText, PreceptID, MemorySource, SourceMemoryID)
                VALUES (?, ?, ?, ?)
            """
            cursor.execute(insert_query, (memory_text, precept_id, "USER", source_memory_id))
        elif target_table == "QuarantineMemory":
            insert_query = f"""
                INSERT INTO {target_table} (EntryText, SourceMemoryID, UserID)
                VALUES (?, ?, ?)
            """
            cursor.execute(insert_query, (memory_text, source_memory_id, user_id))
        else:
            insert_query = f"""
                INSERT INTO {target_table} (MemoryText, PreceptID, SourceMemoryID, UserID)
                VALUES (?, ?, ?, ?)
            """
            cursor.execute(insert_query, (memory_text, precept_id, source_memory_id, user_id))

        cursor.execute("DELETE FROM TempMemory WHERE MemoryID = ?", (memory_id,))
        conn.commit()

    # Caretaker Reflection Pass
    tables_to_check = ["PersonalMemory", "LegendaryMemory", "WildMemory"]
    for table in tables_to_check:
        if table == "PersonalMemory":
            cursor.execute(f"SELECT MemoryID, MemoryText, SourceMemoryID, UserID FROM {table}")
        else:
            cursor.execute(f"SELECT MemoryID, MemoryText FROM {table}")

        rows = cursor.fetchall()

        for row in rows:
            if table == "PersonalMemory":
                memory_id, memory_text, source_memory_id, user_id = row.MemoryID, row.MemoryText, row.SourceMemoryID, row.UserID
            else:
                memory_id, memory_text = row.MemoryID, row.MemoryText

            memory_lower = memory_text.lower()

            if any(cue in memory_lower for cue in sacred_cues):
                target_table = "SacredMemory"
            elif detect_quarantine(memory_text):
                target_table = "QuarantineMemory"
            else:
                continue

            precept_id = None
            for keyword, pid in precept_map.items():
                if keyword in memory_lower:
                    precept_id = pid
                    break

            if target_table == "SacredMemory":
                insert_query = f"""
                    INSERT INTO {target_table} (MemoryText, PreceptID, MemorySource, SourceMemoryID)
                    VALUES (?, ?, ?, ?)
                """
                cursor.execute(insert_query, (memory_text, precept_id, "USER", source_memory_id if table == "PersonalMemory" else None))
            elif target_table == "QuarantineMemory":
                if table == "PersonalMemory":
                    insert_query = f"""
                        INSERT INTO {target_table} (EntryText, SourceMemoryID, UserID)
                        VALUES (?, ?, ?)
                    """
                    cursor.execute(insert_query, (memory_text, source_memory_id, user_id))
                else:
                    insert_query = f"""
                        INSERT INTO {target_table} (EntryText)
                        VALUES (?)
                    """
                    cursor.execute(insert_query, (memory_text,))
            else:
                if table == "PersonalMemory":
                    insert_query = f"""
                        INSERT INTO {target_table} (MemoryText, PreceptID, SourceMemoryID, UserID)
                        VALUES (?, ?, ?, ?)
                    """
                    cursor.execute(insert_query, (memory_text, precept_id, source_memory_id, user_id))
                else:
                    insert_query = f"""
                        INSERT INTO {target_table} (MemoryText, PreceptID)
                        VALUES (?, ?)
                    """
                    cursor.execute(insert_query, (memory_text, precept_id))

            cursor.execute(f"DELETE FROM {table} WHERE MemoryID = ?", (memory_id,))
            conn.commit()

    conn.close()

