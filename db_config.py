"""
Shared database connection for offline scripts.

Credentials come from api/.env (the same file the FastAPI app uses), so no
secrets live in source code. Required keys: DB_HOST, DB_PORT, DB_USER,
DB_PASSWORD, DB_NAME.
"""
import os

import pymysql
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "api", ".env"))


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name} (expected in api/.env)"
        )
    return value


def get_connection(read_timeout=600, write_timeout=600, connect_timeout=60, **extra):
    """Open a new MySQL connection using credentials from api/.env.

    Extra keyword args (e.g. autocommit=True) are passed to pymysql.connect.
    """
    return pymysql.connect(
        host=_require_env("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=_require_env("DB_USER"),
        passwd=_require_env("DB_PASSWORD"),
        db=_require_env("DB_NAME"),
        read_timeout=read_timeout,
        write_timeout=write_timeout,
        connect_timeout=connect_timeout,
        **extra,
    )
