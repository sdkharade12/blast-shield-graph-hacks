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

function scenarioType(scenario = "") {
  return scenario.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
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
        <span className="source-badge"><Icon>◈</Icon> Synthetic FalkorDB</span>
        <span className={`health-pill ${apiReachable ? "good" : "bad"}`}><i aria-hidden="true" /> API {apiReachable ? "Reachable" : "Unreachable"}</span>
        <span className={`health-pill ${databaseOnline ? "good" : "bad"}`}><i aria-hidden="true" /> Graph DB {databaseOnline ? "Connected" : "Unavailable"}</span>
      </div>
    </header>

    <main id="main">
      <section className="page-intro">
        <div><p className="eyebrow">CONNECTED-DATA INVESTIGATION</p><h1>Security investigations</h1><p>Trace modeled access paths, understand their impact, and review the evidence behind each finding.</p></div>
        <div className="live-metrics" aria-label="Current investigation metrics">
          <span><b>{alerts.length || "—"}</b><small>Modeled scenarios</small></span>
          <span><b>{investigation ? paths.length : "—"}</b><small>Paths in this result</small></span>
          <span><b>{investigation ? `${investigation.trace?.[0]?.duration_ms ?? "—"} ms` : "—"}</b><small>Query duration</small></span>
        </div>
      </section>

      {!loading && !apiReachable && <div className="notice-banner warning" role="status"><Icon>!</Icon><div><strong>API unavailable</strong><span>Start the FastAPI service to load scenarios and graph results.</span></div></div>}
      {graphDown && <div className="notice-banner warning" role="status"><Icon>!</Icon><div><strong>Graph database unavailable</strong><span>The API is reachable, but FalkorDB is not. Start the database and retry the investigation.</span></div></div>}
      {loading && <div className="scenario-skeleton"><i /><i /><i /><i /></div>}
      {!loading && <nav className="scenario-nav" aria-label="Investigation scenarios">
        {alerts.map((alert, index) => <button
          key={alert.id}
          type="button"
          className={`scenario-tab ${selectedAlert?.id === alert.id ? "selected" : ""}`}
          aria-current={selectedAlert?.id === alert.id ? "page" : undefined}
          aria-busy={selectedAlert?.id === alert.id && investigating}
          onClick={() => resetWorkspace(alert)}
        ><span className="tab-index">0{String(index + 1).padStart(2, "0")}</span><span className="tab-copy"><small>{alert.id}</small><strong>{alert.title}</strong><span className="scenario-type">{scenarioType(alert.scenario)}</span></span><span className="queue-status">{selectedAlert?.id === alert.id && investigating ? "Loading" : alert.status}</span></button>)}
      </nav>}

      {selectedAlert && <section className="selected-case">
        <div className="case-identity"><div><p className="eyebrow">Selected investigation · {selectedAlert.id}</p><h2>{selectedAlert.title}</h2></div></div>
        <div className="case-target"><span>Investigation target</span><strong>{investigation?.target?.type || selectedAlert.target_type || "—"}</strong><code>{investigation?.target?.id || selectedAlert.target_id || "Target not supplied"}</code></div>
        <button className="primary-button" disabled={!databaseOnline || investigating} onClick={investigate} aria-busy={investigating}>
          <Icon>{investigating ? "◌" : "⌁"}</Icon>{investigating ? "Investigating…" : investigation ? "Run again" : "Investigate"}
        </button>
      </section>}

      {investigationError && <div className="notice-banner error"><Icon>!</Icon><div><strong>Structured investigation error</strong><span>{investigationError}</span></div></div>}
      {investigating && <div className="progress-line"><i /><span>Querying FalkorDB for modeled access paths…</span></div>}
      {!investigation && !investigating && !investigationError && <section className="empty-state panel"><span className="empty-mark">⌁</span><div><strong>Investigation ready</strong><p>Run the selected scenario to display its graph evidence, outcome, and risk factors.</p></div></section>}

      {investigation && <>
        <section className={`investigation-heading panel outcome-${investigation.outcome}`}>
          <div className="finding-summary"><OutcomeBadge outcome={investigation.outcome} /><p>{investigation.summary}</p><div className="finding-caveats"><span>Synthetic FalkorDB data</span><span>Potential reachability is not proof of exploitation</span></div></div>
          <div className="risk-summary">{investigation.risk_assessment ? <><span>DEMO RULES · {investigation.risk_assessment.rule_version}</span><b>{investigation.risk_assessment.score}<small>/100</small></b><strong>{investigation.risk_assessment.severity}</strong></> : <><span>DETERMINISTIC RISK</span><b>Not assessed</b></>}</div>
          {investigation.risk_assessment?.factors?.length > 0 && <div className="risk-factors"><strong>Why it matters</strong><ul>{investigation.risk_assessment.factors.map((factor) => <li key={factor}>{factor}</li>)}</ul><small>Demonstration scoring only; not calibrated breach probability.</small></div>}
        </section>

        <div className="primary-grid">
          <section className="graph-panel panel" aria-label="Graph path evidence">
            <div className="panel-header"><div><p className="eyebrow">Graph evidence</p><h3>Modeled access topology</h3><p className="panel-description">Actual nodes and relationships returned for this investigation.</p></div><span className="path-count">{paths.length} {paths.length === 1 ? "path" : "paths"}</span></div>
            {paths.length > 0 ? <>
              <div className="graph-scroll"><div className="graph-canvas"><ReactFlow key={`${investigation.alert_id}-${investigation.investigation_id}`} nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.18, minZoom: 0.25, maxZoom: 1.15 }} nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}><Background color="#24415b" gap={24} /><Controls showInteractive={false} /></ReactFlow></div></div>
              <div className="path-index" aria-label="All returned paths"><h4>Path sequence</h4>{paths.map((path, index) => <div key={`path-${index}`}><b>Path {String(index + 1).padStart(2, "0")}</b><span className="path-chain">{path.nodes.map((node, nodeIndex) => <span className="path-step" key={`${node.id}-${nodeIndex}`}><code>{node.id}</code>{path.edges[nodeIndex] && <em>{path.edges[nodeIndex].type.replaceAll("_", " ")}</em>}</span>)}</span></div>)}</div>
            </> : <div className={`graph-empty ${investigation.outcome}`}><strong>{investigation.outcome === "query_error" ? "Graph evidence could not be loaded" : investigation.outcome === "unknown_entity" ? "The target is not present in this graph" : investigation.outcome === "invalid_input" ? "The target input is invalid" : "No qualifying path was returned"}</strong><p>{investigation.outcome === "query_error" ? "No reachability conclusion can be drawn." : "This result describes the modeled query only; missing paths do not establish that a real environment is safe."}</p></div>}
          </section>

          <aside className="side-rail">
            <section className="panel briefing-panel"><div className="panel-header"><div><p className="eyebrow">Gemini + MCP</p><h3>Investigation briefing</h3></div>{agent?.grounding?.status && <span className={`grounding-badge ${agent.grounding.status}`} title={agent.grounding.status === "grounded" ? "Gemini answer passed checks against canonical graph evidence." : "Gemini answer was not accepted; a deterministic canonical summary is shown."}>{agent.grounding.status.toUpperCase()}</span>}</div>
              <p className="panel-description">{agent?.grounding?.status === "grounded" ? "Gemini answer passed checks against the canonical evidence." : agent?.grounding?.status === "fallback" ? "Deterministic evidence summary — Gemini answer not accepted." : "Optional explanation of the structured graph result."}</p>
              {agentLoading && <p className="subtle" role="status">Gemini is reviewing the structured evidence…</p>}
              {agent?.stale && <div className="stale-message"><strong>Previous briefing is stale</strong><p>The graph changed after this briefing. Generate a new explanation for the current route state.</p><button className="text-button" onClick={refreshAgentBriefing} disabled={agentLoading}>Refresh briefing</button></div>}
              {agent?.answer && !agent.stale && <div className="markdown-answer"><ReactMarkdown>{agent.answer}</ReactMarkdown></div>}
              {agentError && <p className="agent-error" role="alert">AI briefing unavailable: {agentError} The structured graph result remains available.</p>}
              {!agentLoading && !agentError && !agent?.answer && !agent?.stale && <p className="subtle">The AI explanation is optional; the graph investigation remains available independently.</p>}
              {agent?.grounding?.status === "fallback" && <small className="fallback-note">Deterministic evidence summary — Gemini answer not accepted.</small>}
            </section>

            {selectedAlert.id === "BH-CONTAINMENT-4001" && <ContainmentLab containment={containment} history={history} paths={containment?.before?.paths || paths} target={investigation.target} busy={containmentBusy} onSimulate={simulateRoute} />}
          </aside>
        </div>

        <section className="investigation-details panel" aria-label="Investigation details">
          <div className="panel-header"><div><p className="eyebrow">Supporting evidence</p><h3>Investigation details</h3></div><span className="panel-description">Expand a section to inspect sources, graph queries, and agent tool calls.</span></div>
          <details><summary>Evidence &amp; provenance</summary><div className="disclosure-content">
            {investigation.evidence.length ? <ul className="evidence-list">{investigation.evidence.map((item, index) => <li className="evidence-line" key={`${index}-${item}`}>{item}</li>)}</ul> : <p className="empty-detail">No additional evidence items were returned.</p>}
            <div className="relationship-evidence"><h4>Returned relationships and properties</h4>{investigation.paths.flatMap((path, pathIndex) => path.edges.map((edge, edgeIndex) => <article className="relationship-row" key={`${pathIndex}-${edgeIndex}-${edge.source}-${edge.target}`}><strong>{edge.source} <span>{edge.type}</span> {edge.target}</strong>{Object.keys(edge.properties || {}).length > 0 ? <code>{JSON.stringify(edge.properties)}</code> : <small>No relationship properties were returned.</small>}</article>))}{investigation.paths.every((path) => path.edges.length === 0) && <p className="empty-detail">This result contains no traversed relationships.</p>}</div>
            {investigation.warnings.length > 0 && <div className="warning-list"><h4>Interpretation limits</h4>{investigation.warnings.map((warning, index) => <p className="warning-line" key={`${index}-${warning}`}>{warning}</p>)}</div>}
            <dl className="provenance-grid"><dt>Data source</dt><dd>{investigation.data_source}</dd><dt>Dataset</dt><dd>{investigation.provenance?.dataset_version || "Not provided"}</dd><dt>Coverage</dt><dd>{investigation.provenance?.coverage || "Not provided"}</dd><dt>Target</dt><dd>{investigation.target?.type}: {investigation.target?.id}</dd><dt>Risk rule</dt><dd>{investigation.risk_assessment?.rule_version || "Not assessed"}</dd></dl>
          </div></details>
          <details><summary>Graph query trace <span>{investigation.trace?.length || 0} queries</span></summary><div className="disclosure-content">{investigation.trace?.length ? investigation.trace.map((trace, index) => <TraceRow trace={trace} index={index} key={`${trace.tool_name}-${index}`} />) : <p className="empty-detail">No graph-query trace was returned.</p>}</div></details>
          <details><summary>Agent tool calls <span>{agent?.trace?.length || 0} calls</span></summary><div className="disclosure-content">{agent?.trace?.length ? agent.trace.map((trace, index) => <TraceRow trace={trace} index={index} key={`${trace.tool_name}-${index}`} routeStatuses={trace.tool_name === "get_containment_route_statuses" ? containment?.routes : null} />) : <p className="empty-detail">MCP call details appear here after a Gemini tool-assisted briefing. This trace is separate from the graph-query trace above.</p>}</div></details>
        </section>
      </>}
    </main>
  </div>;
}

function TraceRow({ trace, index, routeStatuses = null }) {
  const isRouteTool = trace.tool_name === "get_containment_route_statuses";
  const routeEvidence = (routeStatuses || []).map(({ route_id: routeId, containment_status: status }) => `${routeId}: ${status}`).join(" · ");
  const evidence = routeEvidence
    ? `Routes: ${routeEvidence}`
    : (trace.evidence || []).length
      ? `Entities: ${trace.evidence.join(", ")}`
      : isRouteTool
        ? "No route statuses returned by the containment API."
        : "No entity IDs returned by this tool.";
  const inputs = Object.entries(trace.input || {});
  return <article className="trace-row">
    <span className="trace-number">{String(index + 1).padStart(2, "0")}</span>
    <div className="trace-main"><strong>{trace.tool_name}</strong>
      {inputs.length > 0 && <div className="trace-inputs">{inputs.map(([key, value]) => <span key={key}><b>{key}</b><code>{String(value)}</code></span>)}</div>}
      <p className="trace-evidence">{evidence}</p>
    </div>
    <span className={`trace-status ${trace.result}`}>{String(trace.result || "unknown").replaceAll("_", " ")}</span>
    <span className="trace-measurement">{trace.result_count ?? 0} results · {trace.duration_ms ?? 0} ms</span>
  </article>;
}

function ContainmentLab({ containment, history, paths, target, busy, onSimulate }) {
  const routes = containment?.routes || [];
  const verification = containment?.verification_outcome;
  const routesAvailable = containment?.status === "routes_found" || routes.length > 0;
  const routeContext = (routeId) => {
    const routePath = paths.find((path) => path.edges.some((edge) => edge.type === "HAS_SESSION" && edge.properties?.route_id === routeId));
    if (!routePath) return "No active path for this route in the current graph result.";
    const destination = routePath.nodes.at(-1);
    return `${target?.id || routePath.nodes[0]?.id} → ${destination?.label || destination?.id} (${destination?.id})`;
  };
  const verificationLabel = {
    alternative_path_remains: "Another modeled route is still reachable.",
    no_qualifying_path_in_modeled_graph: "No qualifying route remains in this modeled graph.",
    verification_failed: "The post-action graph verification failed; containment is unverified.",
  }[verification] || verification?.replaceAll("_", " ");
  return <section className="panel containment-panel">
    <div className="panel-header"><div><p className="eyebrow">Synthetic-only simulation</p><h3>Containment lab</h3></div>{routesAvailable && <span className="route-count">{routes.filter((route) => route.containment_status === "ACTIVE").length} active · {routes.filter((route) => route.containment_status === "BLOCKED").length} blocked</span>}</div>
    <p className="panel-description">Simulated route-state change in FalkorDB only. No real session or cloud resource is modified. Confirm before each action.</p>
    {containment?.error && <p className="agent-error" role="alert">Route status query failed: {containment.error}</p>}
    {!containment && <p className="empty-detail">Route statuses are loading or have not been queried.</p>}
    {verification && <div className={`verification-result ${verification}`} role="status"><small>Fresh post-action graph verification</small><strong>{verificationLabel}</strong>{containment.action && <><span>Action: {containment.action.action_id} · {containment.action.route_id}</span><span>Status: {containment.action.previous_status} → {containment.action.new_status}</span><time dateTime={containment.action.timestamp}>{new Date(containment.action.timestamp).toLocaleString()}</time></>}{containment.verification_error && <em>{containment.verification_error}</em>}</div>}
    {routesAvailable && <div className="route-list" aria-label="Synthetic session routes">{routes.map((route) => {
      const active = route.containment_status === "ACTIVE";
      return <article className="route-card" key={route.route_id}>
        <div className="route-heading"><strong>{route.route_id}</strong><span className={`route-state ${active ? "active" : "blocked"}`}>{route.containment_status}</span></div>
        <p className="route-context">{routeContext(route.route_id)}</p>
        {active && <button className="secondary-button" disabled={busy} onClick={() => onSimulate(route.route_id)} aria-label={`Simulate blocking ${route.route_id} route`}>{busy ? "Verifying…" : "Simulate block"}</button>}
      </article>;
    })}</div>}
    {routesAvailable && routes.length === 0 && <p className="empty-detail">The API returned no containment routes.</p>}
    {history.length > 0 && <details className="audit-details"><summary>Action audit <span>{history.length} actions</span></summary><div className="audit-list">{history.slice(0, 5).map((entry) => <article className="audit-row" key={entry.action_id}><strong>{entry.route_id}: {entry.previous_status} → {entry.new_status}</strong><span>{entry.verification_outcome?.replaceAll("_", " ")}</span><code>{entry.action_id}</code><time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleString()}</time></article>)}</div></details>}
    <p className="seed-note">To restore the demo baseline with both routes active, rerun the Scenario 4 seed from the backend terminal.</p>
  </section>;
}
