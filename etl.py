"""Normalize the three source CSVs into consultbae.db.

Run with: python etl.py
The script is intentionally repeatable: it rebuilds the unified table and
preserves audio submissions already stored in the same SQLite database.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "consultbae.db"
SOURCE_FILES = {
    "naukri": BASE_DIR / "source1_naukri_applicants.csv",
    "gig_workers": BASE_DIR / "source2_gig_workers.csv",
    "cbnexus": BASE_DIR / "source3_cbnexus_contacts.csv",
}
INPUT_ALIASES = {
    "source1_naukri_applicants.csv": SOURCE_FILES["naukri"],
    "source2_gig_workers.csv": SOURCE_FILES["gig_workers"],
    "source3_cbnexus_contacts.csv": SOURCE_FILES["cbnexus"],
}


def clean_phone(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else None


def clean_email(value: Any) -> str | None:
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) else None


def clean_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).title()


def clean_city(value: Any) -> str | None:
    value = re.sub(r"\s+", " ", str(value or "").strip())
    return value.title() if value else None


def parse_number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(re.sub(r"[^\d.]", "", str(value)))
    except ValueError:
        return None


def parse_ctc(value: Any) -> float | None:
    """Normalize annual CTC to INR. Small decimal values are lakh notation."""
    number = parse_number(value)
    if number is None:
        return None
    return number * 100000 if number < 1000 else number


def parse_rate(value: Any) -> float | None:
    match = re.search(r"([\d,.]+)\s*(k)?\s*/\s*(hr|hour|month)", str(value or "").lower())
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    if match.group(2):
        amount *= 1000
    return amount if match.group(3) in {"hr", "hour"} else amount / 160


def parse_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_bool(value: Any) -> int | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"y", "yes", "verified", "true", "1"}:
        return 1
    if normalized in {"n", "no", "not verified", "false", "0"}:
        return 0
    return None


def clean_skills(value: Any) -> str | None:
    tokens = []
    for token in re.split(r"[,;|]", str(value or "")):
        token = re.sub(r"\s+", " ", token.strip().lower())
        if token and token not in tokens:
            tokens.append(token)
    return ", ".join(token.title() if token not in {"sql", "n8n", "apis"} else token.upper() for token in tokens) or None


def merge_skills(first: str | None, second: str | None) -> str | None:
    return clean_skills(", ".join(filter(None, [first, second])))


def infer_category(skills: str | None) -> str | None:
    skill_set = {s.strip().lower() for s in (skills or "").split(",")}
    if not skill_set:
        return None
    if skill_set & {"n8n", "zapier", "selenium", "web scraping"}:
        return "Automation-Heavy"
    if skill_set & {"fastapi", "react", "javascript", "docker"}:
        return "Web Dev"
    if skill_set & {"pandas", "sql", "langchain", "python", "mongodb"}:
        return "Data / ML"
    return "Generalist"


def read_rows(path: Path, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.reader(handle):
            if not raw or not any(cell.strip() for cell in raw):
                continue
            first = raw[0].strip().lower()
            if first in {"full name", "name", "email_id"}:
                continue
            # One gig-worker row has a skill list accidentally placed before
            # the email; recover it rather than silently dropping the record.
            if source == "gig_workers" and len(raw) == 6 and "," in raw[0] and "@" in raw[1]:
                # Malformed row: skills,email,name,rate,location,status.
                raw = [raw[1], raw[2], raw[3], raw[4], raw[5], raw[0]]
            if source == "naukri" and len(raw) >= 8:
                rows.append({
                    "name": raw[0], "email": raw[1], "phone": raw[2], "city": raw[3],
                    "experience_years": parse_number(raw[4]), "current_ctc": parse_ctc(raw[5]),
                    "applied_date": parse_date(raw[6]), "skills": clean_skills(raw[7]),
                })
            elif source == "gig_workers" and len(raw) >= 6:
                rows.append({
                    "name": raw[1], "email": raw[0], "phone": None, "city": raw[3],
                    "experience_years": None, "current_ctc": None, "applied_date": None,
                    "gig_rate_hourly": parse_rate(raw[2]), "gig_status": raw[4].strip().title(),
                    "skills": clean_skills(raw[5]),
                })
            elif source == "cbnexus" and len(raw) >= 5:
                rows.append({
                    "name": raw[0], "email": None, "phone": raw[1], "city": raw[2],
                    "experience_years": None, "current_ctc": None, "applied_date": None,
                    "cbnexus_verified": parse_bool(raw[3]),
                    "cbnexus_projects_completed": int(parse_number(raw[4]) or 0),
                    "skills": None,
                })
    return rows


def schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS unified_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clean_name TEXT NOT NULL,
        clean_phone TEXT UNIQUE,
        clean_email TEXT UNIQUE,
        city TEXT,
        experience_years REAL,
        current_ctc REAL,
        applied_date TEXT,
        gig_rate_hourly REAL,
        gig_status TEXT,
        cbnexus_verified INTEGER,
        cbnexus_projects_completed INTEGER,
        skills TEXT,
        llm_skill_category TEXT,
        sources_found TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audio_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER,
        worker_name TEXT NOT NULL,
        worker_phone TEXT NOT NULL,
        file_path TEXT NOT NULL,
        duration_seconds REAL,
        sample_rate_khz REAL,
        bitrate_kbps REAL,
        loudness_dbfs REAL,
        quality_status TEXT,
        submitted_at TEXT NOT NULL,
        FOREIGN KEY(candidate_id) REFERENCES unified_candidates(id)
    );
    """)


def merge_record(connection: sqlite3.Connection, incoming: dict[str, Any], source: str) -> None:
    incoming["clean_name"] = clean_name(incoming.pop("name", "Unknown"))
    incoming["clean_phone"] = clean_phone(incoming.pop("phone", None))
    incoming["clean_email"] = clean_email(incoming.pop("email", None))
    incoming["city"] = clean_city(incoming.get("city"))
    incoming["sources_found"] = source
    where, key = None, None
    existing = None
    if incoming["clean_email"]:
        existing = connection.execute(
            "SELECT * FROM unified_candidates WHERE clean_email = ?", (incoming["clean_email"],)
        ).fetchone()
    if not existing and incoming["clean_phone"]:
        existing = connection.execute(
            "SELECT * FROM unified_candidates WHERE clean_phone = ?", (incoming["clean_phone"],)
        ).fetchone()
    if not existing:
        columns = ", ".join(incoming)
        placeholders = ", ".join("?" for _ in incoming)
        connection.execute(f"INSERT INTO unified_candidates ({columns}) VALUES ({placeholders})", tuple(incoming.values()))
        return
    current = dict(existing)
    updates: dict[str, Any] = {}
    for field, value in incoming.items():
        if field in {"sources_found", "clean_name"}:
            continue
        if field == "skills":
            updates[field] = merge_skills(current.get(field), value)
        elif value not in (None, ""):
            updates[field] = value
    sources = set(filter(None, (current.get("sources_found") or "").split(",")))
    sources.add(source)
    updates["sources_found"] = ",".join(sorted(sources))
    if not current.get("clean_email") and incoming.get("clean_email"):
        updates["clean_email"] = incoming["clean_email"]
    if not current.get("clean_phone") and incoming.get("clean_phone"):
        updates["clean_phone"] = incoming["clean_phone"]
    if not current.get("llm_skill_category"):
        updates["llm_skill_category"] = infer_category(updates.get("skills", current.get("skills")))
    connection.execute(
        f"UPDATE unified_candidates SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
        (*updates.values(), current["id"]),
    )


def main() -> None:
    missing = [str(path) for path in SOURCE_FILES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing input CSV(s): " + ", ".join(missing))
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    schema(db)
    db.execute("DELETE FROM unified_candidates")
    for source, path in SOURCE_FILES.items():
        for row in read_rows(path, source):
            merge_record(db, row, source)
    for row in db.execute("SELECT id, skills FROM unified_candidates WHERE llm_skill_category IS NULL"):
        db.execute("UPDATE unified_candidates SET llm_skill_category = ? WHERE id = ?", (infer_category(row["skills"]), row["id"]))
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM unified_candidates").fetchone()[0]
    print(f"Loaded {count} unified candidates into {DB_PATH}")
    db.close()


if __name__ == "__main__":
    main()