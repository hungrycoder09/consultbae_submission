from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from etl import DB_PATH, infer_category, schema

app = FastAPI(title="ConsultBae Automation API", version="1.0.0")


class SkillClassification(BaseModel):
    candidate_id: int
    category: str | None = None


@app.get("/api/candidates/unclassified")
def unclassified_candidates():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    schema(db)
    rows = [dict(row) for row in db.execute(
        "SELECT id, clean_name, clean_email, clean_phone, skills FROM unified_candidates "
        "WHERE llm_skill_category IS NULL OR llm_skill_category = ''")]
    db.close()
    return {"count": len(rows), "candidates": rows}


@app.post("/api/webhook/classify-skill")
def classify_skill(payload: SkillClassification):
    if payload.category and payload.category not in {"Automation-Heavy", "Web Dev", "Data / ML", "Generalist"}:
        raise HTTPException(400, "category must be one of: Automation-Heavy, Web Dev, Data / ML, Generalist")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT skills FROM unified_candidates WHERE id = ?", (payload.candidate_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "candidate not found")
    category = payload.category or infer_category(row["skills"])
    db.execute("UPDATE unified_candidates SET llm_skill_category = ? WHERE id = ?", (category, payload.candidate_id))
    db.commit()
    db.close()
    return {"candidate_id": payload.candidate_id, "category": category, "updated": True}