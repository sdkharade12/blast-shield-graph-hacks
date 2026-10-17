from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent.gemini_service import run_gemini_investigation
from app.graph.connection import graph_connection
from app.integration import (
    get_containment_status,
    investigate_alert,
    list_alerts,
    simulate_containment,
)
from app.schemas import InvestigationRequest, InvestigationResponse


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


@app.get("/api/health")
def health() -> dict:
    try:
        with graph_connection() as graph:
            graph.ro_query("RETURN 1 AS ok")
        return {"status": "ok", "mode": "synthetic_falkordb"}
    except Exception:
        return {"status": "degraded", "mode": "synthetic_falkordb"}


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
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini/MCP investigation failed: {type(exc).__name__}.",
        ) from exc
    return {
        "status": "completed",
        "alert_id": request.alert_id,
        "answer": answer,
        "data_source": "synthetic_falkordb",
        "provenance": "Synthetic seeded FalkorDB graph; not a live cloud tenant.",
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
        "verification": get_containment_status(request.alert_id),
    }
