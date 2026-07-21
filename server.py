from dotenv import load_dotenv
load_dotenv()

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, List, Optional
from urllib.parse import unquote

import cognee
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
)




def _get_token() -> str:
    return os.environ["COGNEE_SIDECAR_TOKEN"]


def verify_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
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



class AddRequest(BaseModel):
    content: str
    dataset: str
    tags: List[str] = Field(default_factory=list)


class AddResponse(BaseModel):
    id: str


class RecallRequest(BaseModel):
    query: str
    dataset: str
    top_k: int = 5


class MemoryMatch(BaseModel):
    id: str
    content: str
    score: float


class RecallResponse(BaseModel):
    matches: List[MemoryMatch]


class StatsResponse(BaseModel):
    nodes: int
    edges: int



@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok", "service": "wyrmkeep-sidecar"}



@app.get("/memory/ping")
async def memory_ping(
    _: Annotated[None, Depends(verify_token)],
) -> dict:
    """Liveness check for the memory API specifically."""
    return {"status": "ok"}


@app.post("/memory/add")
async def memory_add(
    request: AddRequest,
    _: Annotated[None, Depends(verify_token)],
) -> AddResponse:
    """
    Add content to a Cognee dataset.
    Returns a stable ID derived from dataset + content for Axum to track.
    """
    logger.info("memory/add dataset=%s tags=%s", request.dataset, request.tags)

    try:
        await cognee.add(
            request.content,
            dataset_name=request.dataset,
            node_set=request.tags if request.tags else None,
        )
    except Exception as exc:
        logger.error("cognee.add failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"cognee.add failed: {exc}",
        )

    # Cognee's add() doesn't return a UUID — generate a deterministic one
    # from dataset + content so Axum has a stable ID to reference.
    import hashlib
    import uuid
    digest = hashlib.sha256(
        f"{request.dataset}:{request.content}".encode()
    ).digest()[:16]
    node_id = str(uuid.UUID(bytes=digest))

    logger.info("memory/add complete dataset=%s id=%s", request.dataset, node_id)
    return AddResponse(id=node_id)


@app.post("/memory/recall")
async def memory_recall(
    request: RecallRequest,
    _: Annotated[None, Depends(verify_token)],
) -> RecallResponse:
    """
    Search Cognee memory for content similar to the query.
    Returns ranked matches with scores.
    """
    logger.info(
        "memory/recall dataset=%s query=%s top_k=%d",
        request.dataset,
        request.query,
        request.top_k,
    )

    try:
        results = await cognee.search(
            query_text=request.query,
            query_type="GRAPH_COMPLETION",
            datasets=[request.dataset],
        )
    except Exception as exc:
        logger.error("cognee.search failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"cognee.search failed: {exc}",
        )

    matches: List[MemoryMatch] = []
    for i, result in enumerate(results[: request.top_k]):
        # Cognee search results vary by version — handle both dict and object
        if isinstance(result, dict):
            content = result.get("text") or result.get("content") or str(result)
            score = float(result.get("score", 1.0 - i * 0.1))
            node_id = str(result.get("id", f"match-{i}"))
        else:
            content = getattr(result, "text", None) or getattr(result, "content", str(result))
            score = float(getattr(result, "score", 1.0 - i * 0.1))
            node_id = str(getattr(result, "id", f"match-{i}"))

        matches.append(MemoryMatch(id=node_id, content=content, score=score))

    logger.info("memory/recall returned %d matches", len(matches))
    return RecallResponse(matches=matches)


@app.delete("/memory/dataset/{dataset:path}")
async def memory_forget_dataset(
    dataset: str,
    _: Annotated[None, Depends(verify_token)],
) -> dict:
    """
    Delete an entire Cognee dataset (GDPR / confidentiality compliance).
    Uses cognee.forget() — correct API for Cognee 1.2.2+.
    """
    decoded = unquote(dataset)
    logger.info("memory/forget dataset=%s", decoded)

    try:
        await cognee.forget(decoded)
    except Exception as exc:
        logger.error("cognee.forget failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to forget dataset: {exc}",
        )

    logger.info("memory/forget complete dataset=%s", decoded)
    return {"deleted": decoded}


@app.get("/memory/stats/{dataset:path}")
async def memory_stats(
    dataset: str,
    _: Annotated[None, Depends(verify_token)],
) -> StatsResponse:
    """
    Return node and edge counts.
    cognee.visualize_graph() in 1.2.2 takes no keyword arguments.
    """
    decoded = unquote(dataset)
    logger.info("memory/stats dataset=%s", decoded)

    try:
        graph_data = await cognee.visualize_graph()
        nodes = len(graph_data.get("nodes", []))
        edges = len(graph_data.get("edges", []))
    except Exception as exc:
        logger.warning(
            "cognee.visualize_graph failed for dataset=%s: %s — returning zeros",
            decoded,
            exc,
        )
        nodes, edges = 0, 0

    return StatsResponse(nodes=nodes, edges=edges)



@app.post("/audit")
async def audit(
    request: SidecarAuditRequest,
    _: Annotated[None, Depends(verify_token)],
) -> JSONResponse:
    """
    Single audit endpoint consumed by Axum's SidecarClient.
    Accepts raw Solidity source, runs Slither, runs Cognee cognify,
    returns SidecarAuditResult. One call per audit.
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

    return JSONResponse(content=result.model_dump(by_alias=True))