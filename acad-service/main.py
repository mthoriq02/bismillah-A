from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import psycopg2
import requests
import os
from datetime import datetime
from contextlib import contextmanager

app = FastAPI(title="Acad Service", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database configuration (sesuai docker-compose)
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "acad_db"),
    "user": os.getenv("DB_USER", "acad_user"),
    "password": os.getenv("DB_PASSWORD", "acad_pass"),
}

class Mahasiswa(BaseModel):
    nim: str
    nama: str
    jurusan: str
    angkatan: int = Field(ge=0)

@contextmanager
def get_db_connection():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

@app.on_event("startup")
async def startup_event():
    try:
        with get_db_connection() as conn:
            print("Acad Service: Connected to PostgreSQL")
    except Exception as e:
        print(f"Acad Service: PostgreSQL connection error: {e}")

# URL ke auth-service untuk verifikasi JWT
AUTH_VERIFY_URL = os.getenv(
    "AUTH_VERIFY_URL",
    "http://auth-service:3001/api/auth/verify",
)

def verify_token_or_raise(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token tidak diberikan")

    token = authorization.split(" ", 1)[1]

    try:
        resp = requests.post(
            AUTH_VERIFY_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Token tidak valid")

        data = resp.json()
        if not data.get("valid"):
            raise HTTPException(status_code=401, detail="Token tidak valid")

        return data.get("user")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal verifikasi token: {e}")

# Health check
@app.get("/api/acad/health")
async def health_check():
    return {
        "status": "Acad Service is running",
        "timestamp": datetime.now().isoformat(),
    }

# List semua mahasiswa
@app.get("/api/acad/mahasiswa")
async def get_mahasiswas():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT nim, nama, jurusan, angkatan FROM mahasiswa"
            cursor.execute(query)
            rows = cursor.fetchall()
            return [
                {"nim": row[0], "nama": row[1], "jurusan": row[2], "angkatan": row[3]}
                for row in rows
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Hitung IPS per mahasiswa per semester (protected dengan JWT)
@app.get("/api/acad/ips/{nim}")
async def get_ips(
    nim: str,
    semester: int = Query(..., ge=1, description="Semester yang akan dihitung IPS-nya"),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """
    Menghitung IPS berdasarkan tabel:
    - krs
    - mata_kuliah
    - bobot_nilai
    """
    # Verifikasi token dulu
    user = verify_token_or_raise(authorization)

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Pastikan mahasiswa ada
            cursor.execute(
                "SELECT nim, nama FROM mahasiswa WHERE nim = %s",
                (nim,),
            )
            mhs = cursor.fetchone()
            if mhs is None:
                raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan")

            # Hitung IPS: SUM(sks * bobot) / SUM(sks)
            query = """
                SELECT
                    SUM(mk.sks * b.bobot) AS total_bobot,
                    SUM(mk.sks) AS total_sks
                FROM krs k
                JOIN mata_kuliah mk ON k.kode_mk = mk.kode_mk
                JOIN bobot_nilai b ON k.nilai = b.nilai
                WHERE k.nim = %s AND k.semester = %s
            """
            cursor.execute(query, (nim, semester))
            result = cursor.fetchone()

            total_bobot = result[0]
            total_sks = result[1]

            if total_sks is None or total_sks == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Tidak ada data KRS untuk mahasiswa dan semester tersebut",
                )

            ips = float(total_bobot) / float(total_sks)

            return {
                "nim": nim,
                "nama": mhs[1],
                "semester": semester,
                "total_sks": total_sks,
                "ips": round(ips, 2),
                "requested_by": user.get("username") if user else None,
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
