"""Создание FastAPI-приложения."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.web import routes

log = logging.getLogger("trambot.web")

BASE = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE / "templates"
STATIC_DIR = BASE / "static"


def create_app(lifespan=None) -> FastAPI:
    application = FastAPI(
        title="Trambot",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    routes.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    routes.templates.env.globals["settings"] = settings
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(routes.router)
    return application
