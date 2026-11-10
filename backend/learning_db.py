"""Optional PostgreSQL store. No connection is made by PDF analysis or imports."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

from fastapi import HTTPException


def configured() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


@contextmanager
def connection():
    if not configured():
        raise HTTPException(503, "학습 데이터베이스가 설정되지 않았습니다. PDF 분석은 계속 사용할 수 있습니다.")
    try:
        import psycopg
        from psycopg.rows import dict_row
        # Bound connection, lock and SQL waits; do not inherit a URL-supplied long timeout.
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2,
                             options="-c statement_timeout=1500 -c lock_timeout=750",
                             row_factory=dict_row) as conn:
            yield conn
    except HTTPException:
        raise
    except Exception as exc:
        # Never expose a DSN, SQL, original text or driver details to clients/logs.
        raise HTTPException(503, "학습 기록 저장소에 연결하지 못했습니다. 수정은 적용되지 않았습니다. 잠시 후 다시 시도해 주세요.") from None


def migrate() -> list[str]:
    applied = []
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71009311)")
        conn.execute("CREATE TABLE IF NOT EXISTS learning_schema_migrations (version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            row = conn.execute("SELECT checksum FROM learning_schema_migrations WHERE version=%s", (path.name,)).fetchone()
            if row:
                if row["checksum"] != checksum:
                    raise RuntimeError("이미 적용된 마이그레이션을 수정할 수 없습니다.")
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO learning_schema_migrations(version, checksum) VALUES (%s,%s)", (path.name, checksum))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    print({"applied": migrate()})
