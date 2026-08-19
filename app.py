from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from audio_utils import analyze_audio
from etl import DB_PATH, clean_phone, schema

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    schema(db)
    return db


def save_submission(name: str, phone: str, uploaded_file) -> None:
    phone_clean = clean_phone(phone)
    if not phone_clean:
        st.error("Enter a valid phone number containing at least 10 digits.")
        return
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg"}:
        st.error("Supported formats: WAV, MP3, M4A, OGG.")
        return
    safe_name = "".join(c if c.isalnum() else "_" for c in name.strip()) or "worker"
    path = UPLOAD_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{safe_name}{suffix}"
    path.write_bytes(uploaded_file.getbuffer())
    metrics = analyze_audio(path)
    db = get_db()
    candidate = db.execute("SELECT id FROM unified_candidates WHERE clean_phone = ?", (phone_clean,)).fetchone()
    db.execute("""INSERT INTO audio_submissions
        (candidate_id, worker_name, worker_phone, file_path, duration_seconds, sample_rate_khz,
         bitrate_kbps, loudness_dbfs, quality_status, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (candidate["id"] if candidate else None, name.strip(), phone_clean, str(path),
         metrics["duration_seconds"], metrics["sample_rate_khz"], metrics["bitrate_kbps"],
         metrics["loudness_dbfs"], metrics["quality_status"], datetime.now(timezone.utc).isoformat()))
    db.commit()
    db.close()
    st.success("Recording saved and analyzed.")


st.set_page_config(page_title="ConsultBae Audio Portal", page_icon="🎙️", layout="wide")
st.title("ConsultBae Worker Portal")
st.caption("Audio collection, quality checks, and the merged candidate database")
submit_tab, portal_tab, database_tab = st.tabs(["Submit Audio Recording", "Audio Submissions Portal", "Merged Database Viewer"])

with submit_tab:
    with st.form("audio_submission", clear_on_submit=True):
        name = st.text_input("Worker name")
        phone = st.text_input("Phone number", placeholder="+91 9000000000")
        audio = st.file_uploader("Upload audio", type=["wav", "mp3", "m4a", "ogg"])
        submitted = st.form_submit_button("Submit recording", type="primary")
        if submitted:
            if not name.strip() or not audio:
                st.error("Worker name and an audio file are required.")
            else:
                save_submission(name, phone, audio)

with portal_tab:
    db = get_db()
    submissions = pd.read_sql_query("SELECT * FROM audio_submissions ORDER BY submitted_at DESC", db)
    db.close()
    if submissions.empty:
        st.info("No audio submissions yet.")
    else:
        for _, row in submissions.iterrows():
            with st.container(border=True):
                st.subheader(f"{row.worker_name} · {row.worker_phone}")
                st.audio(row.file_path)
                cols = st.columns(5)
                cols[0].metric("Duration", f"{row.duration_seconds:.2f}s")
                cols[1].metric("Sample rate", f"{row.sample_rate_khz:.2f} kHz")
                cols[2].metric("Bitrate", f"{row.bitrate_kbps:.2f} kbps")
                cols[3].metric("Loudness", f"{row.loudness_dbfs:.2f} dBFS")
                cols[4].metric("Quality", row.quality_status)
                st.caption(f"Submitted: {row.submitted_at}")

with database_tab:
    db = get_db()
    candidates = pd.read_sql_query("SELECT * FROM unified_candidates ORDER BY id", db)
    db.close()
    if candidates.empty:
        st.warning("No candidates found. Run `python etl.py` first.")
    else:
        city = st.multiselect("Filter by city", sorted(candidates["city"].dropna().unique()))
        category = st.multiselect("Filter by skill category", sorted(candidates["llm_skill_category"].dropna().unique()))
        filtered = candidates
        if city:
            filtered = filtered[filtered.city.isin(city)]
        if category:
            filtered = filtered[filtered.llm_skill_category.isin(category)]
        st.dataframe(filtered, use_container_width=True, hide_index=True)