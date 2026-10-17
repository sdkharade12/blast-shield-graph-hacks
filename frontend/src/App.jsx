import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./App.css";

const API = "http://127.0.0.1:8000";
const EMPTY_PATHS = [];
const OUTCOME_LABELS = {
  path_found: "Potential access path identified",
  no_path_in_graph: "No qualifying path in modeled graph",
  unknown_entity: "Target entity not found",
  invalid_input: "Invalid investigation request",
  query_error: "Graph query failed",
};

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || `HTTP ${response.status}`));
  }
  return data;
}

function Icon({ children }) {
  return <span className="icon" aria-hidden="true">{children}</span>;
}

function OutcomeBadge({ outcome }) {
  return <span className={`outcome-badge ${outcome || "unknown"}`}>{OUTCOME_LABELS[outcome] || outcome || "Not investigated"}</span>;
}

export default function App() {
  const [alerts, setAlerts] = useState([]);
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [investigation, setInvestigation] = useState(null);
  const [containment, setContainment] = useState(null);
  const [history, setHistory] = useState([]);
  const [agent, setAgent] = useState(null);
  const [caseCache, setCaseCache] = useState({});
  const [apiReachable, setApiReachable] = useState(false);
  const [databaseOnline, setDatabaseOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [investigating, setInvestigating] = useState(false);
  const [agentLoading, setAgentLoading] = useState(false);
  const [containmentBusy, setContainmentBusy] = useState(false);
  const [investigationError, setInvestigationError] = useState("");
  const [agentError, setAgentError] = useState("");
  const requestVersion = useRef(0);
  const controllerRef = useRef(null);

  function cacheCase(alertId, patch) {
    setCaseCache((current) => ({
      ...current,
      [alertId]: { ...current[alertId], ...patch },
    }));
  }

  const resetWorkspace = useCallback((alert) => {
    requestVersion.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    const snapshot = caseCache[alert.id] || {};
    setSelectedAlert(alert);
    setInvestigation(snapshot.investigation || null);
    setContainment(snapshot.containment || null);
    setHistory(snapshot.history || []);
    setAgent(snapshot.agent || null);
    setInvestigationError("");
    setAgentError("");
    setInvestigating(false);
    setAgentLoading(false);
  }, [caseCache]);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const [health, catalog] = await Promise.all([
          apiRequest(`${API}/api/health`),
          apiRequest(`${API}/api/alerts`),
        ]);
        if (!mounted) return;
        setApiReachable(health.api_status === "ok");
        setDatabaseOnline(health.database_status === "ok");
        setAlerts(catalog.items);
        setSelectedAlert(catalog.items[0] || null);
      } catch {
        if (mounted) {
          setApiReachable(false);
          setDatabaseOnline(false);
        }
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
      controllerRef.current?.abort();
    };
  }, []);

  const loadContainment = useCallback(async (alertId, signal) => {
    const [routes, audit] = await Promise.all([
      apiRequest(`${API}/api/containment/${alertId}`, { signal }),
      apiRequest(`${API}/api/containment/${alertId}/history`, { signal }),
    ]);
    return { routes, history: audit.items || [] };
  }, []);

  async function runAgentBriefing(alert, version, signal) {
    setAgentLoading(true);
    setAgentError("");
    try {
      const result = await apiRequest(`${API}/api/agent/investigate`, {
        method: "POST",
        signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_id: alert.id }),
      });
      if (version === requestVersion.current) {
        setAgent(result);
        cacheCase(alert.id, { agent: result });
      }
    } catch (error) {
      if (error.name !== "AbortError" && version === requestVersion.current) {
        setAgentError(error.message || "Gemini/MCP briefing failed.");
      }
    } finally {
      if (version === requestVersion.current) setAgentLoading(false);
    }
  }

  async function investigate() {
    if (!selectedAlert || !databaseOnline) return;
    const alert = selectedAlert;
    const version = ++requestVersion.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setInvestigating(true);
    setInvestigation(null);
    setContainment(null);
    setHistory([]);
    setAgent(null);
    setInvestigationError("");
    setAgentError("");
    try {
      const result = await apiRequest(`${API}/api/investigations`, {
        method: "POST",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_id: alert.id }),
      });
      if (version !== requestVersion.current) return;
      setInvestigation(result);
      cacheCase(alert.id, { investigation: result });
      if (alert.id === "BH-CONTAINMENT-4001") {
        try {
          const status = await loadContainment(alert.id, controller.signal);
          if (version !== requestVersion.current) return;
          setContainment(status.routes);
          setHistory(status.history);
          cacheCase(alert.id, { containment: status.routes, history: status.history });
        } catch (error) {
          if (error.name !== "AbortError" && version === requestVersion.current) {
            setContainment({ status: "query_error", routes: [], error: error.message });
          }
        }
      }
      setInvestigating(false);
      // Keep structured findings on screen while the optional Gemini request runs.
      void runAgentBriefing(alert, version, controller.signal);
    } catch (error) {
      if (error.name !== "AbortError" && version === requestVersion.current) {
        setInvestigationError(error.message || "Could not retrieve graph evidence.");
      }
    } finally {
      if (version === requestVersion.current) setInvestigating(false);
    }
  }

  async function refreshAgentBriefing() {
    if (!selectedAlert || !investigation || agentLoading) return;
    const alert = selectedAlert;
    const version = requestVersion.current;
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setAgent(null);
    cacheCase(alert.id, { agent: null });
    setAgentError("");
    await runAgentBriefing(selectedAlert, version, controller.signal);
  }

  async function simulateRoute(routeId) {
    const alert = selectedAlert;
    if (!alert || !window.confirm(`Simulate blocking the ${routeId} route? This only changes the synthetic FalkorDB graph.`)) return;
    controllerRef.current?.abort();
    controllerRef.current = null;
    const version = ++requestVersion.current;
    setContainmentBusy(true);
    setInvestigationError("");
    // Immediately invalidate the old graph view and briefing; they describe pre-action state.
    setInvestigation(null);
    cacheCase(alert.id, { investigation: null, agent: null });
    setAgent(null);
    setAgentError("");
    try {
      const result = await apiRequest(`${API}/api/containment/simulate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_id: alert.id, route_id: routeId, confirmed: true }),
      });
      if (version !== requestVersion.current) return;
      setContainment({ status: "routes_found", routes: result.routes, ...result });
      cacheCase(alert.id, { containment: { status: "routes_found", routes: result.routes, ...result } });
      if (result.action) setHistory((items) => [result.action, ...items.filter((item) => item.action_id !== result.action.action_id)]);
      const [refreshed, audit] = await Promise.all([
        apiRequest(`${API}/api/investigations`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ alert_id: alert.id }),
        }),
        apiRequest(`${API}/api/containment/${alert.id}/history`),
      ]);
      if (version !== requestVersion.current) return;
      setInvestigation(refreshed);
      setHistory(audit.items || []);
      setAgent({ stale: true });
      cacheCase(alert.id, {
        investigation: refreshed,
        history: audit.items || [],
        agent: { stale: true },
        containment: { status: "routes_found", routes: result.routes, ...result },
      });
    } catch (error) {
      if (version === requestVersion.current) setInvestigationError(error.message || "Containment verification or graph refresh failed.");
    } finally {
      if (version === requestVersion.current) setContainmentBusy(false);
    }
  }

  const paths = investigation?.paths ?? EMPTY_PATHS;
  const nodes = useMemo(() => {
    const byId = new Map();
    paths.forEach((path, pathIndex) => path.nodes.forEach((node, hopIndex) => {
      if (!byId.has(node.id)) {
        byId.set(node.id, {
          id: node.id,
          position: { x: hopIndex * 190, y: pathIndex * 118 + (hopIndex % 2 ? 48 : 0) },
          ariaLabel: `${node.label}, ${node.type}, ID ${node.id}`,
          data: { label: <><b>{node.label}</b><small>{node.type} · {node.id}</small></> },
          className: `graph-node ${(node.type || "entity").toLowerCase()}`,
        });
      }
    }));
    return [...byId.values()];
  }, [paths]);
  const edges = useMemo(() => paths.flatMap((path, pathIndex) => path.edges.map((edge, edgeIndex) => ({
    id: `${pathIndex}-${edgeIndex}-${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
    label: edge.type.replaceAll("_", " "),
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#7f95bd" },
    ariaLabel: `${edge.source} ${edge.type} ${edge.target}`,
  }))), [paths]);
  const graphDown = apiReachable && !databaseOnline;

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="#main" aria-label="BlastShield home">
        <span className="brand-mark">✦</span><span><strong>BLAST<span>SHIELD</span></strong><small>GRAPH SECURITY</small></span>
      </a>
      <div className="topbar-status">
        <span className="source-badge"><Icon>◈</Icon> SYNTHETIC FALKORDB</span>
        <span className={`health-pill ${apiReachable ? "good" : "bad"}`}><i /> API {apiReachable ? "REACHABLE" : "UNREACHABLE"}</span>
        <span className={`health-pill ${databaseOnline ? "good" : "bad"}`}><i /> GRAPH DB {databaseOnline ? "CONNECTED" : "UNAVAILABLE"}</span>
      </div>
    </header>

    <main id="main">
      <section className="page-intro">
        <div><p className="eyebrow">CONNECTED-DATA INVESTIGATION</p><h1>Security investigations</h1><p>Choose a scenario to trace modeled access paths and inspect the evidence behind each result.</p></div>
        <div className="live-metrics" aria-label="Current investigation metrics">
          <span><b>{alerts.length || "—"}</b>SCENARIOS</span>
          <span><b>{investigation ? paths.length : "—"}</b>RETURNED PATHS</span>
          <span><b>{investigation ? `${investigation.trace?.[0]?.duration_ms ?? "—"} ms` : "—"}</b>GRAPH QUERY</span>
        </div>
      </section>

      {graphDown && <div className="notice-banner warning"><Icon>!</Icon>API is reachable, but FalkorDB is unavailable. Start the database and retry the investigation.</div>}
      {loading && <div className="scenario-skeleton"><i /><i /><i /><i /></div>}
      {!loading && <nav className="scenario-nav" aria-label="Investigation scenarios">
        {alerts.map((alert, index) => <button
          key={alert.id}
          type="button"
          className={`scenario-tab ${selectedAlert?.id === alert.id ? "selected" : ""}`}
          aria-current={selectedAlert?.id === alert.id ? "page" : undefined}
          onClick={() => resetWorkspace(alert)}
        ><span className="tab-index">0{index + 1}</span><span className="tab-copy"><small>{alert.id}</small><strong>{alert.title}</strong></span><span className="queue-status">{alert.status}</span></button>)}
      </nav>}

      {selectedAlert && <section className="selected-case">
        <div className="case-identity"><div><p className="eyebrow">SELECTED SCENARIO</p><h2>{selectedAlert.title}</h2></div><span className="alert-id">{selectedAlert.id}</span></div>
          <div className="case-target"><span>Target</span><strong>{investigation?.target?.type || selectedAlert.target_type || "—"}</strong><code>{investigation?.target?.id || selectedAlert.target_id || "Run investigation to resolve"}</code></div>
        <button className="primary-button" disabled={!databaseOnline || investigating} onClick={investigate}>
          <Icon>{investigating ? "◌" : "⌁"}</Icon>{investigating ? "INVESTIGATING…" : investigation ? "RUN AGAIN" : "INVESTIGATE"}
        </button>
      </section>}

      {investigationError && <div className="notice-banner error"><Icon>!</Icon><div><strong>Structured investigation error</strong><span>{investigationError}</span></div></div>}
      {investigating && <div className="progress-line"><i /><span>Querying FalkorDB for modeled access paths…</span></div>}
      {!investigation && !investigating && !investigationError && <section className="empty-state panel"><span className="empty-mark">⌁</span><div><strong>Investigation ready</strong><p>Run the selected scenario to display its graph evidence, outcome, and risk factors.</p></div></section>}

      {investigation && <>
        <section className="investigation-heading"><div><OutcomeBadge outcome={investigation.outcome} /><p>{investigation.summary}</p><small>Potential reachability does not prove exploitation.</small></div>{investigation.risk_assessment ? <div className="risk-summary"><span>DEMO RISK · {investigation.risk_assessment.rule_version}</span><b>{investigation.risk_assessment.score}<small>/100</small></b><strong>{investigation.risk_assessment.severity}</strong></div> : <div className="risk-summary unassessed"><span>DETERMINISTIC RISK</span><b>Not assessed</b></div>}</section>

        <div className="primary-grid">
          <section className="graph-panel panel" aria-label="Graph path evidence">
            <div className="panel-header"><div><p className="eyebrow">GRAPH EVIDENCE</p><h3>Modeled access topology</h3></div><span className="path-count">{paths.length} PATH{paths.length === 1 ? "" : "S"}</span></div>
            {paths.length > 0 ? <>
              <div className="graph-canvas"><ReactFlow key={`${investigation.alert_id}-${investigation.investigation_id}`} nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.18, minZoom: 0.25, maxZoom: 1.15 }} nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}><Background color="#24415b" gap={20} /><Controls showInteractive={false} /></ReactFlow></div>
              <div className="path-index" aria-label="All returned paths">{paths.map((path, index) => <div key={`path-${index}`}><b>PATH {String(index + 1).padStart(2, "0")}</b><span>{path.nodes.map((node) => node.id).join(" → ")}</span></div>)}</div>
            </> : <div className={`graph-empty ${investigation.outcome}`}><strong>{investigation.outcome === "query_error" ? "Graph evidence could not be loaded" : investigation.outcome === "unknown_entity" ? "The target is not present in this graph" : investigation.outcome === "invalid_input" ? "The target input is invalid" : "No qualifying path was returned"}</strong><p>{investigation.outcome === "query_error" ? "No reachability conclusion can be drawn." : "This result describes the modeled query only; missing paths do not establish that a real environment is safe."}</p></div>}
          </section>

          <aside className="side-rail">
            <section className="panel briefing-panel"><div className="panel-header"><div><p className="eyebrow">GEMINI + MCP</p><h3>Investigation briefing</h3></div>{agent?.grounding?.status && <span className="grounding-badge">{agent.grounding.status.toUpperCase()}</span>}</div>
              {agentLoading && <p className="subtle">Gemini is reviewing the structured evidence…</p>}
              {agent?.stale && <div className="stale-message"><strong>Briefing needs refresh</strong><p>The graph changed after this briefing. Generate a new explanation for the current route state.</p><button className="text-button" onClick={refreshAgentBriefing} disabled={agentLoading}>REFRESH BRIEFING</button></div>}
              {agent?.answer && !agent.stale && <div className="markdown-answer"><ReactMarkdown>{agent.answer}</ReactMarkdown></div>}
              {agentError && <p className="agent-error">Gemini/MCP error: {agentError}</p>}
              {!agentLoading && !agentError && !agent?.answer && !agent?.stale && <p className="subtle">The AI explanation is optional; the graph investigation remains available independently.</p>}
              {agent?.grounding?.status === "fallback" && <small className="fallback-note">Deterministic summary from canonical investigation evidence.</small>}
            </section>

            {investigation.risk_assessment && <section className="panel risk-panel"><div className="panel-header"><div><p className="eyebrow">RULE BREAKDOWN</p><h3>Why this score</h3></div><b>{investigation.risk_assessment.score}<small>/100</small></b></div><div className="score-track"><i style={{ width: `${investigation.risk_assessment.score}%` }} /></div><ul>{investigation.risk_assessment.factors.map((factor) => <li key={factor}>{factor}</li>)}</ul><small className="risk-note">Demonstration rules; not a calibrated breach probability.</small></section>}

            {selectedAlert.id === "BH-CONTAINMENT-4001" && <ContainmentLab containment={containment} history={history} busy={containmentBusy} onSimulate={simulateRoute} />}
          </aside>
        </div>

        <section className="detail-disclosures" aria-label="Investigation details">
          <details><summary>Evidence, warnings and provenance</summary><div className="disclosure-content">{investigation.evidence.map((item, index) => <p className="evidence-line" key={`${index}-${item}`}>{item}</p>)}{investigation.warnings.map((warning, index) => <p className="warning-line" key={`${index}-${warning}`}>{warning}</p>)}<dl><dt>Data source</dt><dd>{investigation.data_source}</dd><dt>Dataset</dt><dd>{investigation.provenance?.dataset_version || "Not provided"}</dd><dt>Coverage</dt><dd>{investigation.provenance?.coverage || "Not provided"}</dd><dt>Target</dt><dd>{investigation.target?.type}: {investigation.target?.id}</dd></dl></div></details>
          <details><summary>Graph query trace</summary><div className="disclosure-content">{investigation.trace?.map((trace, index) => <TraceRow trace={trace} index={index} key={`${trace.tool_name}-${index}`} />)}</div></details>
          <details><summary>Agent MCP tool calls</summary><div className="disclosure-content">{agent?.trace?.length ? agent.trace.map((trace, index) => <TraceRow trace={trace} index={index} key={`${trace.tool_name}-${index}`} />) : <p className="subtle">MCP call details appear here after Gemini completes a tool-assisted briefing.</p>}</div></details>
        </section>
      </>}
    </main>
  </div>;
}

function TraceRow({ trace, index }) {
  return <div className="trace-row"><b>{String(index + 1).padStart(2, "0")}</b><div><strong>{trace.tool_name}</strong><code>{Object.entries(trace.input || {}).map(([key, value]) => `${key}: ${value}`).join(" · ")}</code><small>{(trace.evidence || []).join(", ") || "No entity IDs returned"}</small></div><span className={`trace-status ${trace.result}`}>{String(trace.result).replaceAll("_", " ")}</span><span>{trace.result_count} rows · {trace.duration_ms} ms</span></div>;
}

function ContainmentLab({ containment, history, busy, onSimulate }) {
  const routes = containment?.routes || [];
  const verification = containment?.verification_outcome;
  return <section className="panel containment-panel"><div className="panel-header"><div><p className="eyebrow">SYNTHETIC SIMULATION</p><h3>Containment verification</h3></div><span className={`route-count ${routes.length && routes.some((route) => route.containment_status === "ACTIVE") ? "active" : ""}`}>{routes.filter((route) => route.containment_status === "ACTIVE").length} ACTIVE</span></div>
    <p className="subtle">Human confirmed graph-state simulation. No live session or cloud resource is changed.</p>
    {containment?.error && <p className="agent-error">Route status query failed: {containment.error}</p>}
    {verification && <div className={`verification-result ${verification}`}><small>POST-ACTION GRAPH QUERY</small><strong>{verification.replaceAll("_", " ")}</strong>{containment.action && <span>{containment.action.route_id}: {containment.action.previous_status} → {containment.action.new_status} · {containment.action.action_id}</span>}{containment.verification_error && <em>{containment.verification_error}</em>}</div>}
    <div className="route-list">{routes.map((route) => <div className="route-row" key={route.route_id}><span><b>{route.route_id}</b><small>{route.containment_status}</small></span>{route.containment_status !== "BLOCKED" && <button className="secondary-button" disabled={busy} onClick={() => onSimulate(route.route_id)}>{busy ? "VERIFYING…" : "SIMULATE BLOCK"}</button>}</div>)}</div>
    {history.length > 0 && <details className="audit-details"><summary>Action audit · {history.length}</summary>{history.slice(0, 5).map((entry) => <div className="audit-row" key={entry.action_id}><code>{entry.action_id}</code><span>{entry.route_id}: {entry.previous_status} → {entry.new_status}</span><strong>{entry.verification_outcome?.replaceAll("_", " ")}</strong><small>{entry.timestamp}</small></div>)}</details>}
  </section>;
}
