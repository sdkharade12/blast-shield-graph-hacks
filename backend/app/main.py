import asyncio
from typing import Literal
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictBool, StrictInt

from app.agent.gemini_service import run_gemini_investigation
from app.agent.gemini_service import _deterministic_summary
from app.agent.response_agent import run_autonomous_response
from app.graph.connection import graph_connection
from app.integration import (
    get_containment_status,
    get_containment_history,
    investigate_alert,
    list_alerts,
    simulate_containment,
)
from app.services.graph_service import find_shortest_access_path

logger = logging.getLogger(__name__)
from app.schemas import InvestigationRequest, InvestigationResponse

_response_run_lock = asyncio.Lock()


app = FastAPI(
    title="BlastShield API",
    description="Graph-backed investigations over synthetic FalkorDB data",
    version="0.2.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ContainmentSimulationRequest(BaseModel):
    alert_id: str = Field(min_length=1, max_length=64)
    route_id: Literal["primary", "alternate"]
    confirmed: bool


class ShortestPathRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)
    max_hops: StrictInt = Field(default=6, ge=1, le=6)


class AutonomousResponseRequest(BaseModel):
    alert_id: str = Field(min_length=1, max_length=64)
    confirmed: StrictBool
    max_actions: StrictInt = Field(default=3, ge=1, le=3)


@app.get("/api/health")
def health() -> dict:
    try:
        with graph_connection() as graph:
            graph.ro_query("RETURN 1 AS ok")
        database_status = "ok"
    except Exception:
        database_status = "unavailable"
    return {
        "status": "ok" if database_status == "ok" else "degraded",
        "api_status": "ok",
        "database_status": database_status,
        "mode": "synthetic_falkordb",
    }


@app.get("/api/alerts")
def get_alerts() -> dict:
    return {"items": list_alerts()}


@app.post("/api/investigations", response_model=InvestigationResponse)
def investigate(request: InvestigationRequest) -> InvestigationResponse:
    result = investigate_alert(request.alert_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"outcome": "unknown_alert", "message": "Alert not found."},
        )
    if result.outcome == "invalid_input":
        raise HTTPException(status_code=422, detail=result.summary)
    return result


@app.post("/api/graph/shortest-path")
def shortest_path(request: ShortestPathRequest) -> dict:
    """Explore a bounded path using reviewed relationship/depth templates."""
    return {
        **find_shortest_access_path(request.source_id, request.target_id, request.max_hops),
        "data_source": "synthetic_falkordb",
    }


@app.post("/api/agent/investigate")
async def investigate_with_agent(request: InvestigationRequest) -> dict:
    # Reject unknown IDs before starting Gemini/MCP subprocesses.
    from app.integration import find_alert

    alert = find_alert(request.alert_id)
    if alert is None:
        raise HTTPException(
            status_code=404,
            detail={"outcome": "unknown_alert", "message": "Alert not found."},
        )
    try:
        answer = await run_gemini_investigation(request.alert_id)
    except RuntimeError as exc:
        logger.warning("Gemini/MCP unavailable; serving canonical fallback (%s)", type(exc).__name__)
        return _agent_fallback(request.alert_id, "gemini_configuration_or_service", type(exc).__name__)
    except Exception as exc:
        logger.warning("Gemini/MCP failed; serving canonical fallback (%s)", type(exc).__name__)
        return _agent_fallback(request.alert_id, "gemini_mcp_failure", type(exc).__name__)
    return {
        "status": "completed",
        "alert_id": request.alert_id,
        **answer,
        "data_source": "synthetic_falkordb",
        "provenance": "Synthetic seeded FalkorDB graph; not a live cloud tenant.",
    }


@app.post("/api/agent/respond")
async def autonomous_response(request: AutonomousResponseRequest) -> dict:
    """Run a confirmed, bounded response simulation against the synthetic graph only."""
    from app.integration import find_alert

    alert = find_alert(request.alert_id.strip())
    if not alert:
        raise HTTPException(status_code=404, detail={"status": "unknown_alert"})
    if request.confirmed is not True:
        raise HTTPException(status_code=400, detail={"status": "confirmation_required", "message": "Explicit confirmation is required before any synthetic response action."})
    async with _response_run_lock:
        result = await run_autonomous_response(alert["id"], request.max_actions)
    if result.get("status") == "unknown_alert":
        raise HTTPException(status_code=404, detail=result)
    return result


def _agent_fallback(alert_id: str, reason: str, diagnostic_code: str) -> dict:
    canonical = investigate_alert(alert_id)
    return {
        "status": "fallback",
        "alert_id": alert_id,
        "answer": _deterministic_summary(alert_id, canonical),
        "grounding": {"status": "fallback", "reason": reason, "diagnostic_code": diagnostic_code},
        "trace": [],
        "grounding_source": "canonical_api_fallback",
        "data_source": "synthetic_falkordb",
        "provenance": "Deterministic summary from the canonical synthetic graph result; Gemini output was not accepted or unavailable.",
    }


@app.get("/api/containment/{alert_id}")
def containment_status(alert_id: str) -> dict:
    result = get_containment_status(alert_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Containment scenario not found.")
    if result.get("status") == "query_error":
        raise HTTPException(status_code=503, detail="Graph query failed.")
    return result


@app.post("/api/containment/simulate")
def containment_simulate(request: ContainmentSimulationRequest) -> dict:
    if not request.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Explicit confirmation is required for synthetic simulation.",
        )
    result = simulate_containment(request.alert_id, request.route_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Containment scenario not found.")
    if result.get("status") in {"query_error", "containment_failed"}:
        raise HTTPException(status_code=503, detail=result)
    if result.get("status") in {"unknown_identity", "unknown_route"}:
        raise HTTPException(status_code=404, detail=result)
    return {
        **result,
        "data_source": "synthetic_falkordb",
        "message": "Synthetic graph route updated; no live cloud session was changed.",
    }


@app.get("/api/containment/{alert_id}/history")
def containment_history(alert_id: str) -> dict:
    history = get_containment_history(alert_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Containment scenario not found.")
    return {"items": history, "data_source": "synthetic_falkordb"}
