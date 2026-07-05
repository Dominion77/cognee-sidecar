from dotenv import load_dotenv
load_dotenv()

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

import cognee
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from models import SidecarAuditRequest, SidecarAuditResult
from pipeline import run_audit_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


_REQUIRED_ENV = [
    "COGNEE_SIDECAR_TOKEN",
    "LLM_API_KEY",
]


def _check_required_env() -> None:
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )



@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_required_env()

    # Cognee reads its configuration (LLM provider, vector DB, etc.) from
    # environment variables automatically when running on cognee/cognee:main.
    # We do a lightweight prune-free config touch here only to surface
    # any misconfiguration early, before the first real audit request.
    try:
        cognee.config.set_llm_config(
            {
                "llm_api_key": os.environ["LLM_API_KEY"],
                "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
                "llm_model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
            }
        )
    except Exception as exc:
        logger.error("Failed to configure Cognee on startup: %s", exc)
        raise

    logger.info("Wyrmkeep sidecar ready")
    yield
    logger.info("Wyrmkeep sidecar shutting down")



app = FastAPI(
    title="Wyrmkeep Sidecar",
    description="Slither + Cognee audit pipeline for Wyrmkeep",
    version="0.1.0",
    lifespan=lifespan,
    # Disable the default /docs and /redoc in production if desired
    # docs_url=None,
    # redoc_url=None,
)


_SIDECAR_TOKEN: str = ""  # populated after env check in lifespan


def _get_token() -> str:
    """Lazily read the token after lifespan has validated it exists."""
    return os.environ["COGNEE_SIDECAR_TOKEN"]


def verify_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """
    Validate the Bearer token sent by Axum.
    Header format: Authorization: Bearer <COGNEE_SIDECAR_TOKEN>
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if parts[1] != _get_token():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token",
        )




@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok", "service": "wyrmkeep-sidecar"}


@app.post("/audit")
async def audit(
    request: SidecarAuditRequest,
    _: Annotated[None, Depends(verify_token)],
) -> JSONResponse:
    """
    Single audit endpoint consumed by Axum's SidecarClient.

    Accepts raw Solidity source, runs Slither, runs Cognee cognify,
    returns SidecarAuditResult as JSON. One call per audit — no separate
    /analyze or /cognify endpoints exist.

    The JSON response uses field aliases (e.g. `type` not `element_type`)
    to match Axum's serde deserialization expectations exactly.
    """
    logger.info(
        "Audit request: contract=%s dataset=%s node_set=%s",
        request.contract_name,
        request.dataset,
        request.node_set,
    )

    try:
        slither_report, elapsed_ms = await run_audit_pipeline(
            source_code=request.source_code,
            contract_name=request.contract_name,
            dataset=request.dataset,
            node_set=request.node_set,
        )
    except RuntimeError as exc:
        # Slither binary missing — sidecar is misconfigured
        logger.error("Sidecar misconfigured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("Unhandled pipeline error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal audit pipeline error",
        )

    result = SidecarAuditResult(
        slither_report=slither_report,
        elapsed_ms=elapsed_ms,
    )

    logger.info(
        "Audit complete: contract=%s findings=%d elapsed_ms=%d",
        request.contract_name,
        len(slither_report.detectors),
        elapsed_ms,
    )

    # Use by_alias=True so SlitherElement serializes `type` not `element_type`,
    # matching Axum's #[serde(rename = "type")] expectation.
    return JSONResponse(content=result.model_dump(by_alias=True))