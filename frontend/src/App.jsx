import ReactMarkdown from "react-markdown";
import { useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  // MiniMap,
  MarkerType,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";
import "./App.css";

const API = "http://127.0.0.1:8000";
const EMPTY_PATHS = [];

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = data.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : JSON.stringify(detail || `HTTP ${response.status}`)
    );
  }

  return data;
}

export default function App() {
  const [alerts, setAlerts] = useState([]);
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [investigation, setInvestigation] = useState(null);
  const [apiOnline, setApiOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [investigating, setInvestigating] = useState(false);
  const [error, setError] = useState("");
  const [agentAnswer, setAgentAnswer] = useState("");
  const [agentError, setAgentError] = useState("");
  const [agentLoading, setAgentLoading] = useState(false);
  const [containment, setContainment] = useState(null);
  const [containmentBusy, setContainmentBusy] = useState(false);

  useEffect(() => {
    let active = true;

    async function initialise() {
      try {
        const health = await apiRequest(`${API}/api/health`);
        const result = await apiRequest(`${API}/api/alerts`);

        if (!active) return;

        setApiOnline(health.status === "ok");
        setAlerts(result.items);
        setSelectedAlert(result.items[0] || null);
      } catch {
        if (!active) return;
        setApiOnline(false);
        setError(
          "Cannot reach FastAPI. Check that your backend is running."
        );
      } finally {
        if (active) setLoading(false);
      }
    }

    initialise();

    return () => {
      active = false;
    };
  }, []);


  async function investigate() {
    if (!selectedAlert) return;

    setInvestigating(true);
    setInvestigation(null);
    setContainment(null);
    setAgentAnswer("");
    setAgentError("");
    setError("");

    try {
      // First retrieve structured data for the graph UI.
      const result = await apiRequest(
        `${API}/api/investigations`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            alert_id: selectedAlert.id,
          }),
        }
      );

      setInvestigation(result);
      if (selectedAlert.id === "BH-CONTAINMENT-4001") {
        try {
          setContainment(await apiRequest(`${API}/api/containment/${selectedAlert.id}`));
        } catch (err) {
          setContainment({ status: "query_error", error: err.message, routes: [] });
        }
      }

      // Then ask Gemini to investigate through MCP.
      setAgentLoading(true);

      try {
        const agentResult = await apiRequest(
          `${API}/api/agent/investigate`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              alert_id: selectedAlert.id,
            }),
          }
        );

        setAgentAnswer(agentResult.answer);
      } catch (err) {
        setAgentError(
          err.message || "Gemini investigation failed."
        );
      } finally {
        setAgentLoading(false);
      }
    } catch (err) {
      setError(
        err.message || "Could not retrieve the investigation."
      );
    } finally {
      setInvestigating(false);
    }
  }


  const paths = investigation?.paths ?? EMPTY_PATHS;
  const path = paths[0];

  const nodes = useMemo(() => {
    const byId = new Map();
    paths.forEach((currentPath, pathIndex) => currentPath.nodes.forEach((item, index) => {
      if (!byId.has(item.id)) byId.set(item.id, {
        id: item.id,
        position: { x: index * 190, y: pathIndex * 150 + (index % 2 === 0 ? 35 : 100) },
        data: { label: `${item.label}\n${item.type}` },
        style: {
          width: 155, padding: 12, whiteSpace: "pre-line", textAlign: "center",
          borderRadius: 10, color: "#edf3ff",
          background: item.type === "DataAsset" ? "#472332" : item.type === "IAMRole" ? "#202f4d" : "#17313a",
          border: item.type === "DataAsset" ? "1px solid #f18aa8" : "1px solid #486681",
          fontSize: 12,
        },
      });
    }));
    return [...byId.values()];
  }, [paths]);

  const edges = useMemo(() => {
    return paths.flatMap((currentPath, pathIndex) => currentPath.edges.map((edge, index) => ({
      id: `edge-${pathIndex}-${index}`,
      source: edge.source,
      target: edge.target,
      label: edge.type,
      type: "smoothstep",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: "#8ca8ca",
      },
      style: {
        stroke: "#8ca8ca",
        strokeWidth: 2,
      },
      labelStyle: {
        fill: "#dbe7fa",
        fontSize: 10,
      },
      labelBgStyle: {
        fill: "#101a2b",
      },
    })));
  }, [paths]);

  const riskScore = Math.max(
    0,
    Math.min(100, investigation?.risk_score ?? 0)
  );

  async function simulateRoute(routeId) {
    const approved = window.confirm(`Block the ${routeId} route in the synthetic graph? This does not change any live cloud session.`);
    if (!approved) return;
    setContainmentBusy(true);
    try {
      const result = await apiRequest(`${API}/api/containment/simulate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_id: selectedAlert.id, route_id: routeId, confirmed: true }),
      });
      setContainment(result.verification);
      setAgentAnswer("");
      setAgentError("Graph state changed. Re-run the investigation to refresh Gemini's explanation.");
      const refreshed = await apiRequest(`${API}/api/investigations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_id: selectedAlert.id }),
      });
      setInvestigation(refreshed);
    } catch (err) {
      setError(err.message || "Synthetic containment simulation failed.");
    } finally {
      setContainmentBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-logo">B</div>
          <div>
            <strong>BlastShield</strong>
            <p>Graph-powered cloud security</p>
          </div>
        </div>

        <div className="connection">
          <span className={apiOnline ? "dot online" : "dot"} />
          {apiOnline ? "API connected" : "API disconnected"}
        </div>
      </header>

      <section className="page-heading">
        <div>
          <p className="eyebrow">SECURITY OPERATIONS</p>
          <h1>Investigation workspace</h1>
          <p className="muted">
            Investigate security findings and trace potential
            access paths to critical resources.
          </p>
        </div>
        <span className="mock-label">SYNTHETIC FALKORDB DATA</span>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <div className="workspace">
        <aside className="sidebar panel">
          <div className="section-heading">
            <span>INCIDENT QUEUE</span>
            <span className="count">{alerts.length}</span>
          </div>

          {loading && <p className="muted">Loading incidents...</p>}

          {!loading &&
            alerts.map((alert) => (
              <button
                key={alert.id}
                className={`incident ${selectedAlert?.id === alert.id ? "selected" : ""
                  }`}
                onClick={() => {
                  setSelectedAlert(alert);
                  setInvestigation(null);
                  setAgentAnswer("");
                  setAgentError("");
                  setContainment(null);
                  setAgentLoading(false);
                  setError("");
                }}
              >
                <span className="incident-id">{alert.id}</span>
                <strong>{alert.title}</strong>
                <span className="incident-status">{alert.status}</span>
              </button>
            ))}

          {!loading && alerts.length === 0 && (
            <p className="muted">No incidents available.</p>
          )}

          <div className="sidebar-note">
            <strong>Development mode</strong>
            <p>
              Findings are queried from a locally seeded synthetic FalkorDB graph, not a live cloud tenant.
            </p>
          </div>
        </aside>

        <main className="main-content">
          <section className="incident-header">
            <div>
              <p className="eyebrow">SELECTED INCIDENT</p>
              <h2>{selectedAlert?.id || "No incident selected"}</h2>
              <p className="muted">
                {selectedAlert?.title || "Select an incident to begin."}
              </p>
            </div>

            <button
              className="primary-button"
              disabled={!selectedAlert || investigating || !apiOnline}
              onClick={investigate}
            >
              {investigating ? "Investigating..." : "Investigate alert"}
            </button>
          </section>

          {!investigation && !investigating && (
            <section className="panel empty-state">
              <div className="empty-icon">⌕</div>
              <h3>Ready to investigate</h3>
              <p className="muted">
                Select an incident and investigate it to display
                the findings returned by the API.
              </p>
            </section>
          )}

          {investigating && (
            <section className="panel empty-state">
              <h3>Retrieving investigation</h3>
              <p className="muted">
                Querying the synthetic FalkorDB graph...
              </p>
            </section>
          )}

          {investigation && (
            <>
              <section className="panel findings">
                <div className="finding-heading">
                  <div>
                    <p className="eyebrow">INVESTIGATION RESULT</p>
                    <h3>
              {investigation.outcome === "path_found"
                        ? "Potential access path identified"
                        : investigation.outcome === "query_error"
                          ? "Graph query failed"
                          : investigation.outcome === "unknown_entity"
                            ? "Investigation target not found"
                            : "No qualifying path in represented graph"}
                    </h3>
                  </div>

                  <span
                    className={`severity ${investigation.severity.toLowerCase()}`}
                  >
                    {investigation.severity}
                  </span>
                </div>

                <p>{investigation.summary}</p>

                <div className="risk-heading">
                  <span>Deterministic risk score</span>
                  <strong>{investigation.risk_score == null ? "Not assessed" : `${riskScore}/100`}</strong>
                </div>
                {investigation.risk_score != null && <div className="risk-track"><div className="risk-fill" style={{ width: `${riskScore}%` }} /></div>}
              </section>


              <section className="panel agent-panel">
                <div className="section-title">
                  <div>
                    <p className="eyebrow">AI INVESTIGATION</p>
                    <h3>Gemini's explanation</h3>
                  </div>

                  <span className="source-label">MCP · SYNTHETIC FALKORDB</span>
                </div>

                {agentLoading && (
                  <p className="muted">
                    Gemini is reviewing the investigation evidence...
                  </p>
                )}

                {agentError && (
                  <div className="error-banner">
                    AI explanation unavailable: {agentError}
                  </div>
                )}

                {!agentLoading && agentAnswer && (
                  <div className="markdown-answer">
                    <ReactMarkdown>{agentAnswer}</ReactMarkdown>
                  </div>
                )}

                {!agentLoading && !agentAnswer && !agentError && (
                  <p className="muted">
                    Run an investigation to view the AI explanation.
                  </p>
                )}
              </section>


              <section className="panel">
                <div className="section-title">
                  <div>
                    <p className="eyebrow">GRAPH EVIDENCE</p>
                    <h3>Represented access paths ({paths.length})</h3>
                  </div>
                  <span className="source-label">
                    {investigation.data_source}
                  </span>
                </div>

                {path ? (
                  <div className="graph-canvas">
                    <ReactFlow
                      nodes={nodes}
                      edges={edges}
                      fitView
                      fitViewOptions={{ padding: 0.15 }}
                      nodesDraggable={false}
                      nodesConnectable={false}
                    >
                      <Background color="#29384c" />
                      <Controls />
                      {/* <MiniMap /> */}
                    </ReactFlow>
                  </div>
                ) : (
                  <div className="no-path">
                    <h4>{investigation.outcome === "query_error" ? "Graph data unavailable" : investigation.outcome === "unknown_entity" ? "Target not found" : "No qualifying path returned"}</h4>
                    <p>
                      {investigation.outcome === "query_error" ? "The graph query failed; no reachability conclusion is available." : "No qualifying path was returned by this query. That does not prove a real environment is safe."}
                    </p>
                  </div>
                )}
              </section>

              <div className="detail-grid">
                <section className="panel">
                  <div className="section-title">
                    <h3>Investigation evidence</h3>
                  </div>

                  {investigation.evidence.map((item, index) => (
                    <div className="evidence-item" key={index}>
                      <span>{index + 1}</span>
                      <p>{item}</p>
                    </div>
                  ))}
                </section>

                <section className="panel">
                  <div className="section-title">
                    <h3>Proposed response</h3>
                    <span className="source-label">PREVIEW</span>
                  </div>

                  {investigation.proposed_actions.length > 0 ? (
                    investigation.proposed_actions.map((action) => (
                      <div className="action-card" key={action.action_id}>
                        <span className="action-type">
                          {action.action_type}
                        </span>
                        <h4>{action.title}</h4>
                        <p>{action.reason}</p>
                        <p className="muted">
                          Target: {action.target_id}
                        </p>
                      </div>
                    ))
                  ) : (
                    <p className="muted">
                      No response action is supported for this investigation result.
                    </p>
                  )}
                </section>
              </div>

              {selectedAlert.id === "BH-CONTAINMENT-4001" && (
                <section className="panel">
                  <div className="section-title">
                    <div><p className="eyebrow">SYNTHETIC SIMULATION</p><h3>Containment route verification</h3></div>
                    <span className="source-label">NO LIVE CLOUD CHANGES</span>
                  </div>
                  <p className="muted">Each action requires confirmation and marks only a route in the seeded FalkorDB graph as BLOCKED.</p>
                  {containment?.routes?.map((route) => (
                    <div className="evidence-item" key={route.route_id}>
                      <span>{route.containment_status === "BLOCKED" ? "✓" : "•"}</span>
                      <p>{route.route_id}: {route.containment_status}</p>
                      {route.containment_status !== "BLOCKED" && <button className="secondary-button" disabled={containmentBusy} onClick={() => simulateRoute(route.route_id)}>Simulate block</button>}
                    </div>
                  ))}
                  {containment?.error && <p className="error-banner">{containment.error}</p>}
                </section>
              )}

              {investigation.warnings.length > 0 && (
                <section className="warning-panel">
                  <h3>Important limitations</h3>
                  {investigation.warnings.map((warning, index) => (
                    <p key={index}>• {warning}</p>
                  ))}
                </section>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
