"""Read-only deployment check. Emits configuration states, never secret values."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from . import learning_db as db


def migration_manifest() -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql'))}


def check_setup() -> dict:
    checks = []
    def add(name, title, ready, detail):
        checks.append({'id': name, 'title': title, 'ready': bool(ready), 'detail': detail})
    admin = os.environ.get('LEARNING_ADMIN_TOKEN', '')
    document_key = os.environ.get('LEARNING_DOCUMENT_KEY', '')
    add('admin_token', '관리자 인증키', len(admin) >= 32, '서버 비밀 환경변수에 32자 이상의 난수가 필요합니다.')
    add('document_key', '문서 식별키', len(document_key) >= 32, '같은 문서의 평가 분리를 유지할 별도의 32자 이상 난수가 필요합니다.')
    add('separate_keys', '인증키와 문서 식별키 분리', bool(admin and document_key and admin != document_key), '관리자 인증키와 문서 식별키는 서로 달라야 합니다.')
    try:
        origin = urlparse(os.environ.get('LEARNING_PUBLIC_ORIGIN', ''))
        valid_origin = bool(origin.scheme == 'https' and origin.netloc and not origin.username and not origin.password
                            and not origin.path and not origin.query and not origin.fragment)
    except ValueError:
        valid_origin = False
    add('public_origin', '외부 HTTPS 주소', valid_origin, 'LEARNING_PUBLIC_ORIGIN에 서비스의 HTTPS origin을 설정합니다. 경로와 마지막 /는 넣지 않습니다.')
    add('database_configured', 'PostgreSQL 연결 설정', db.configured(), 'DATABASE_URL을 서버 비밀 환경변수에 설정합니다.')
    if db.configured():
        try:
            with db.connection() as conn:
                tables = ['learning_schema_migrations', 'learning_consents', 'learning_events', 'learning_cases', 'learning_reviews']
                missing = [table for table in tables if conn.execute('SELECT to_regclass(%s) AS name', (table,)).fetchone()['name'] is None]
                add('database_reachable', 'PostgreSQL 접속', True, '연결 성공. 사용자 기록은 조회하지 않았습니다.')
                if missing:
                    add('migrations', '학습 스키마', False, 'python -m backend.learning_db를 실행해 마이그레이션을 적용해야 합니다.')
                else:
                    applied = {row['version']: row['checksum'] for row in conn.execute('SELECT version,checksum FROM learning_schema_migrations').fetchall()}
                    expected = migration_manifest()
                    matches = applied == expected
                    add('migrations', '학습 스키마·체크섬', matches, '현재 코드와 적용한 마이그레이션의 버전·체크섬을 비교했습니다.')
                    allowed = all(conn.execute('SELECT has_table_privilege(current_user,%s,%s) AS allowed', (table, privilege)).fetchone()['allowed']
                                  for table in tables[1:] for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'))
                    add('database_permissions', '기록 조회·저장·수정·삭제 권한', allowed, '운영 계정의 대상 학습 테이블 권한을 확인했습니다. 데이터는 변경하지 않았습니다.')
        except Exception:
            # A driver error or even an upstream HTTP detail may contain a DSN.
            add('database_reachable', 'PostgreSQL 접속', False, '연결 또는 스키마 확인에 실패했습니다. 비밀값과 원문 오류는 출력하지 않습니다.')
    return {'ready': all(item['ready'] for item in checks), 'checks': checks,
            'dictionary_configured': bool(os.environ.get('STDICT_API_KEY')),
            'read_only': True, 'recommendations_enabled': False, 'auto_confirmation_enabled': False}


def main() -> int:
    result = check_setup()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
