# ConsultBae AI Automation Assignment

This repository merges three imperfect candidate sources into SQLite, exposes
an automation API, and provides a Streamlit audio collection portal.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp attached_assets/source1_naukri_applicants_*.csv source1_naukri_applicants.csv
cp attached_assets/source2_gig_workers_*.csv source2_gig_workers.csv
cp attached_assets/source3_cbnexus_contacts_*.csv source3_cbnexus_contacts.csv
python etl.py
streamlit run app.py
```

For the API and n8n flow:

```bash
uvicorn api:app --reload --port 8000
```

Import `n8n_skill_tagger.json` into n8n, attach an OpenAI credential to the
LLM node, set `CONSULTBAE_API_URL` if the API is not on localhost, and call
the generated webhook URL. The flow gets unclassified candidates, sends each
skills list to the LLM, and writes the returned category through the API.

## Repository structure

```text
.
├── app.py                 # Streamlit UI: submit, portal, merged viewer
├── api.py                 # FastAPI endpoints used by n8n/Make
├── audio_utils.py         # Audio metrics and fallback analyzer
├── etl.py                 # CSV normalization, matching, merging, SQLite
├── n8n_skill_tagger.json  # Importable n8n workflow
├── requirements.txt
├── README.md
└── uploads/               # Runtime audio files; ignored by Git
```

## Data quality report

The inputs contained 41 Naukri data rows, 32 Gig Worker rows (including one
malformed shifted row and one blank row), and 31 CBnexus rows (including a
repeated header). The pipeline addresses:

* **Phone formats:** `+91`, `91`, leading zeroes, hyphens, and spaces are
  reduced to the final 10 digits. Invalid short values become null.
* **Email formats:** emails are trimmed and lowercased; malformed or blank
  values become null.
* **Names and cities:** repeated whitespace is removed and values are
  title-cased, so `RITU SHARMA` and `Noida ` are consistent.
* **Duplicate people:** email is the first key; phone is the fallback. This
  handles duplicate Naukri rows such as Rohit Verma and Nikhil Chopra, and
  joins records that only overlap across sources. Non-null values win over
  nulls, skills are unioned, and `sources_found` is accumulated.
* **Gig rates:** numeric `/hr` values remain hourly. `15k/month` becomes
  `15000 / 160 = 93.75` hourly; the same rule is used for every monthly rate.
* **CTC:** plain large numbers are treated as annual INR. Small decimal lakh
  values such as `4.2` and `11.9` are multiplied by 100,000.
* **Dates:** ISO, `DD-MM-YYYY`, `DD Mon YYYY`, and slash-form dates are
  normalized to ISO. Slash dates are parsed month-first when needed, which
  correctly handles values such as `07/13/2026`.
* **Booleans:** `Y`, `yes`, `Verified`, `No`, `N`, and common true/false
  variants become SQLite 1/0.
* **Skills:** delimiters, whitespace, casing, and duplicate tokens are
  normalized. Tokens are stored once as a readable comma-separated list.
* **Malformed CSV row:** the duplicated `Isha Chopra` row in Gig Workers has
  its skill list shifted into column one; the loader detects the email in
  column two and recovers the row instead of silently losing it.
* **Missing values:** blank records and blank attributes remain null rather
  than being invented.

## Stuck log

1. **Matching a phone with international prefixes.** Matching raw strings
   produced false non-matches (`+91-...`, `919...`, and `0...`). I inspected
   digit lengths and made the last-ten-digit rule the canonical key. A
   name-only fuzzy match was rejected because common names could merge two
   different people.
2. **Mixed rate and CTC notation.** A naive `float()` parser discarded `k`
   and `/month`. I tested representative rows, separated hourly rate parsing
   from annual CTC parsing, and made the monthly conversion explicit at 160
   hours. Guessing that every numeric CTC was lakh would inflate large INR
   values, so only values below 1,000 are treated as lakh notation.
3. **Audio formats and metadata.** `wave` cannot read MP3/M4A/OGG. The app
   uses SoundFile first and a stdlib WAV fallback, calculates RMS-based
   loudness and silence ratio, and derives bitrate from file size/duration.
   A single hard-coded “good” label was rejected because clipping and near
   silence are useful operational signals.

## 5,000-worker launch stretch report

The first failures will be local disk writes and SQLite write-lock
contention, followed by CPU and memory pressure from decoding many audio
files in Streamlit processes. A production design should:

1. Upload directly to object storage using short-lived S3 pre-signed URLs;
   store only an object key in the database and validate size/type.
2. Put analysis jobs on Celery/RQ with Redis or a managed queue. Workers
   decode audio off the web process, retry idempotently, and persist status
   (`queued`, `processing`, `complete`, `failed`).
3. Move candidate and submission metadata to Postgres with indexes on
   normalized email/phone and connection pooling. SQLite is excellent for
   this take-home but has one writer and is not the right shared production
   store.
4. Add a deterministic deduplication hash over normalized phone/email plus
   an ingestion idempotency key, so webhook retries cannot create duplicates.
5. Put the API behind a load balancer, add rate limits, structured logs,
   metrics, malware scanning, retention policies, and backups before launch.
