"""Separate, pseudonymous learning identity and server-only reviewer authority."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from urllib.parse import urlparse
from fastapi import HTTPException, Request, Response

COOKIE = "sori_learning"
POLICY_VERSION = "2026-11-10.1"


def identity(request: Request | None) -> str | None:
    if request is None:
        return None
    token = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_identity(request: Request, response: Response) -> str:
    subject = identity(request)
    if subject:
        return subject
    token = secrets.token_hex(32)
    public_origin = os.environ.get("LEARNING_PUBLIC_ORIGIN", "")
    response.set_cookie(COOKIE, token, max_age=365 * 24 * 3600, httponly=True,
                        secure=request.url.scheme == "https" or public_origin.startswith("https://"),
                        samesite="strict", path="/api")
    return hashlib.sha256(token.encode()).hexdigest()


def require_identity(request: Request) -> str:
    subject = identity(request)
    if subject is None:
        raise HTTPException(401, "학습 관리 화면을 다시 열어 사용자 식별을 초기화해 주세요.")
    return subject


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        expected = os.environ.get("LEARNING_PUBLIC_ORIGIN", "").rstrip("/")
        supplied = urlparse(origin)
        valid = origin == expected if expected else (supplied.scheme in ("http", "https")
                                                     and supplied.netloc == request.headers.get("host")
                                                     and not supplied.path and not supplied.query and not supplied.fragment)
        if not valid:
            raise HTTPException(403, "같은 사이트에서 요청해 주세요.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "다른 사이트의 요청은 허용되지 않습니다.")


def admin(request: Request) -> str:
    same_origin(request)
    expected = os.environ.get("LEARNING_ADMIN_TOKEN", "")
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if len(expected) < 32 or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "관리자 인증이 필요합니다.")
    return os.environ.get("LEARNING_ADMIN_ID", "reviewer-" + hashlib.sha256(expected.encode()).hexdigest()[:12])


def document_group(sha256: str) -> str:
    key = os.environ.get("LEARNING_DOCUMENT_KEY", "")
    if len(key) < 32:
        raise HTTPException(503, "서버의 학습 문서 식별키 설정이 필요합니다.")
    return hmac.new(key.encode(), sha256.encode(), hashlib.sha256).hexdigest()
