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

function readRoute() {
  const params = new URLSearchParams(window.location.search);
  return { view: params.get("view") === "workspace" ? "workspace" : "overview", scenarioId: params.get("scenario") || "" };
}

function workspaceHref(scenarioId = "") {
  const params = new URLSearchParams({ view: "workspace" });
  if (scenarioId) params.set("scenario", scenarioId);
  return `/?${params.toString()}`;
}

export default function App() {
  const [route, setRoute] = useState(readRoute);
  const navigate = useCallback((view, scenarioId = "", replace = false) => {
    const params = new URLSearchParams();
    if (view === "workspace") params.set("view", "workspace");
    if (scenarioId) params.set("scenario", scenarioId);
    window.history[replace ? "replaceState" : "pushState"]({}, "", params.size ? `/?${params}` : "/");
    const nextRoute = readRoute();
    setRoute(nextRoute);
    window.dispatchEvent(new CustomEvent("blastshield:navigate", { detail: nextRoute }));
  }, []);

  useEffect(() => {
    const onPopState = () => {
      const nextRoute = readRoute();
      setRoute(nextRoute);
      window.dispatchEvent(new CustomEvent("blastshield:navigate", { detail: nextRoute }));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  return route.view === "workspace"
    ? <Workspace key="workspace" initialScenarioId={route.scenarioId} onNavigate={navigate} />
    : <LandingPage initialScenarioId={route.scenarioId} onNavigate={navigate} />;
}

function Workspace({ initialScenarioId, onNavigate }) {
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
  const initialScenarioIdRef = useRef(initialScenarioId);

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
        const initialAlert = catalog.items.find((item) => item.id === initialScenarioIdRef.current) || catalog.items[0] || null;
        setSelectedAlert(initialAlert);
        if (initialAlert && !initialScenarioIdRef.current) onNavigate("workspace", initialAlert.id, true);
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
  }, [onNavigate]);

  useEffect(() => {
    const onNavigate = (event) => {
      if (event.detail?.view !== "workspace" || !event.detail.scenarioId) return;
      const requestedAlert = alerts.find((alert) => alert.id === event.detail.scenarioId);
      if (requestedAlert && requestedAlert.id !== selectedAlert?.id) resetWorkspace(requestedAlert);
    };
    window.addEventListener("blastshield:navigate", onNavigate);
    return () => window.removeEventListener("blastshield:navigate", onNavigate);
  }, [alerts, resetWorkspace, selectedAlert?.id]);

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
  const nodeTypes = [...new Set(paths.flatMap((path) => path.nodes.map((node) => node.type).filter(Boolean)))];
  const nodes = useMemo(() => {
    const nodeById = new Map();
    const lanes = Math.max(paths.length, 1);
    paths.forEach((path, pathIndex) => {
      const lane = (pathIndex - (lanes - 1) / 2) * 196;
      path.nodes.forEach((node, depth) => {
        const entry = nodeById.get(node.id) || { node, rank: depth, laneValues: [] };
        entry.rank = Math.max(entry.rank, depth);
        entry.laneValues.push(lane);
        nodeById.set(node.id, entry);
      });
    });
    const rankedNodes = [...nodeById.values()].sort((a, b) => a.rank - b.rank || a.laneValues.reduce((sum, value) => sum + value, 0) - b.laneValues.reduce((sum, value) => sum + value, 0));
    const rankOffsets = new Map();
    return rankedNodes.map(({ node, rank, laneValues }) => {
      const lane = laneValues.reduce((sum, value) => sum + value, 0) / laneValues.length;
      const peers = rankOffsets.get(rank) || 0;
      rankOffsets.set(rank, peers + 1);
      return {
        id: node.id,
        position: { x: rank * 260, y: lane + peers * 32 },
        ariaLabel: `${node.label}, ${node.type}, ID ${node.id}`,
        data: { label: <><b>{node.label}</b><small>{node.type} · {node.id}</small></> },
        className: `graph-node ${(node.type || "entity").toLowerCase()}`,
      };
    });
  }, [paths]);
  const edges = useMemo(() => paths.flatMap((path, pathIndex) => path.edges.map((edge, edgeIndex) => ({
    id: `${pathIndex}-${edgeIndex}-${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
    label: edge.type.replaceAll("_", " "),
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#7f95bd" },
    labelBgStyle: { fill: "#0b1a28", fillOpacity: 0.96 },
    labelBgPadding: [7, 4],
    labelBgBorderRadius: 5,
    labelStyle: { fill: "#e1edf5", fontSize: 12, fontWeight: 700 },
    ariaLabel: `${edge.source} ${edge.type} ${edge.target}`,
  }))), [paths]);
  const graphDown = apiReachable && !databaseOnline;

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="/" onClick={(event) => { event.preventDefault(); onNavigate("overview", selectedAlert?.id); }} aria-label="BlastShield overview">
        <span className="brand-mark">✦</span><span><strong>BLAST<span>SHIELD</span></strong><small>GRAPH SECURITY</small></span>
      </a>
      <nav className="top-nav" aria-label="Main navigation"><a href="/" onClick={(event) => { event.preventDefault(); onNavigate("overview", selectedAlert?.id); }}>Overview</a><a href={workspaceHref(selectedAlert?.id)} aria-current="page" onClick={(event) => { event.preventDefault(); onNavigate("workspace", selectedAlert?.id); }}>Investigation Workspace</a></nav>
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
          onClick={() => onNavigate("workspace", alert.id)}
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
            {nodeTypes.length > 0 && <div className="graph-legend" aria-label="Entity types in this graph">{nodeTypes.map((type) => <span key={type}><i className={`legend-swatch ${(type || "entity").toLowerCase()}`} aria-hidden="true" />{type}</span>)}</div>}
            <div className="graph-scroll"><div className="graph-canvas"><ReactFlow key={`${investigation.alert_id}-${investigation.investigation_id}`} nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.18, minZoom: 0.25, maxZoom: 1.15 }} nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}><Background color="#24415b" gap={24} /><Controls showInteractive={false} /></ReactFlow></div></div>
              <div className="path-index" aria-label="All returned paths"><h4>Path sequence</h4>{paths.map((path, index) => <div key={`path-${index}`}><b>Path {String(index + 1).padStart(2, "0")}</b><span className="path-chain">{path.nodes.map((node, nodeIndex) => <span className="path-step" key={`${node.id}-${nodeIndex}`}><code>{node.id}</code>{path.edges[nodeIndex] && <em>{path.edges[nodeIndex].type.replaceAll("_", " ")}</em>}</span>)}</span></div>)}</div>
            </> : <div className={`graph-empty ${investigation.outcome}`}><strong>{investigation.outcome === "query_error" ? "Graph evidence could not be loaded" : investigation.outcome === "unknown_entity" ? "The target is not present in this graph" : investigation.outcome === "invalid_input" ? "The target input is invalid" : "No qualifying path was returned"}</strong><p>{investigation.outcome === "query_error" ? "No reachability conclusion can be drawn." : "This result describes the modeled query only; missing paths do not establish that a real environment is safe."}</p></div>}
          </section>

          <aside className="side-rail">
            <section className="panel briefing-panel"><div className="panel-header"><div><p className="eyebrow">Gemini + MCP</p><h3>Investigation briefing</h3></div>{agent?.grounding?.status && <span className={`grounding-badge ${agent.grounding.status}`} title={agent.grounding.status === "grounded" ? "Gemini answer passed checks against canonical graph evidence." : "Gemini answer was not accepted; a deterministic canonical summary is shown."}>{agent.grounding.status.toUpperCase()}</span>}</div>
              <p className="panel-description">{agent?.grounding?.status === "grounded" ? "Gemini answer passed checks against the canonical evidence." : agent?.grounding?.status === "fallback" ? agent.grounding.reason?.startsWith("gemini_") ? "Gemini was unavailable or its request failed; this is a deterministic canonical summary." : "The Gemini answer did not pass grounding checks; this is a deterministic canonical summary." : "Optional explanation of the structured graph result."}</p>
              {agentLoading && <p className="subtle" role="status">Gemini is reviewing the structured evidence…</p>}
              {agent?.stale && <div className="stale-message"><strong>Previous briefing is stale</strong><p>The graph changed after this briefing. Generate a new explanation for the current route state.</p><button className="text-button" onClick={refreshAgentBriefing} disabled={agentLoading}>Refresh briefing</button></div>}
              {agent?.answer && !agent.stale && <div className="markdown-answer"><ReactMarkdown>{agent.answer}</ReactMarkdown></div>}
              {agentError && <p className="agent-error" role="alert">AI briefing unavailable: {agentError} The structured graph result remains available.</p>}
              {!agentLoading && !agentError && !agent?.answer && !agent?.stale && <p className="subtle">The AI explanation is optional; the graph investigation remains available independently.</p>}
              {agent?.grounding?.status === "fallback" && <small className="fallback-note">{agent.grounding.reason?.startsWith("gemini_") ? "Gemini unavailable or failed — canonical summary shown." : "Gemini answer not accepted — canonical summary shown."}</small>}
              {agent?.grounding?.status === "fallback" && <details className="grounding-debug"><summary>Grounding diagnostic</summary><p>Reason: {agent.grounding.reason || "not supplied"}</p>{agent.grounding.diagnostic_code && <p>Diagnostic: {agent.grounding.diagnostic_code}</p>}{agent.grounding.unsupported_entities?.length > 0 && <p>Unsupported entity claims: {agent.grounding.unsupported_entities.join(", ")}</p>}{agent.grounding.unsupported_relationships?.length > 0 && <p>Unsupported relationship claims: {agent.grounding.unsupported_relationships.join(", ")}</p>}{agent.grounding.claimed_scores?.length > 0 && <p>Rejected score claims: {agent.grounding.claimed_scores.join(", ")}</p>}</details>}
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
      <TraversalLab graphAvailable={databaseOnline} />
    </main>
  </div>;
}

const SCENARIO_DESCRIPTIONS = {
  identity_investigation: "Follow an identity's modeled access relationships to sensitive data.",
  excessive_iam_permissions: "Inspect reachability through relationships already classified as excessive.",
  exposed_workload_blast_radius: "Trace a modeled public workload to the assets it can reach.",
  containment_verification: "Simulate blocking a route and verify what remains reachable.",
};

function Reveal({ children, className = "" }) {
  const elementRef = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const element = elementRef.current;
    if (!element || !("IntersectionObserver" in window)) {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setVisible(true);
        observer.disconnect();
      }
    }, { threshold: 0.12 });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return <div ref={elementRef} className={`reveal ${visible ? "is-visible" : ""} ${className}`}>{children}</div>;
}

function TraversalLab({ graphAvailable }) {
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [maxHops, setMaxHops] = useState("5");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function compareDepth(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await apiRequest(`${API}/api/graph/shortest-path`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_id: sourceId.trim(), target_id: targetId.trim(), max_hops: Number(maxHops) }),
      });
      setResult(response);
    } catch (requestError) {
      setError(requestError.message || "The bounded path query failed.");
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const found = result?.status === "path_found";
  return <section className="traversal-lab panel" aria-labelledby="traversal-title">
    <div className="panel-header"><div><p className="eyebrow">Bounded graph query</p><h3 id="traversal-title">Traversal depth explorer</h3><p className="panel-description">Compare a shallow search with a deeper one. Only reviewed access relationships are traversed; the depth is limited to six.</p></div><span className="source-badge">Synthetic graph</span></div>
    <form className="traversal-form" onSubmit={compareDepth}>
      <label>Source entity ID<input autoComplete="off" value={sourceId} onChange={(event) => setSourceId(event.target.value)} placeholder="Enter a graph entity ID" required maxLength={128} /></label>
      <label>Target entity ID<input autoComplete="off" value={targetId} onChange={(event) => setTargetId(event.target.value)} placeholder="Enter a graph entity ID" required maxLength={128} /></label>
      <label>Maximum hops<select value={maxHops} onChange={(event) => setMaxHops(event.target.value)}>{[1, 2, 3, 4, 5, 6].map((depth) => <option key={depth} value={depth}>{depth} {depth === 1 ? "hop" : "hops"}</option>)}</select></label>
      <button className="primary-button" type="submit" disabled={!graphAvailable || busy || !sourceId.trim() || !targetId.trim()} aria-busy={busy}>{busy ? "Searching…" : "Run bounded search"}</button>
    </form>
    {!graphAvailable && <p className="empty-detail" role="status">Connect the graph database to run a traversal.</p>}
    {error && <p className="agent-error" role="alert">{error}</p>}
    {result && <div className={`traversal-result ${found ? "found" : "not-found"}`} role="status">
      <div><strong>{found ? `Path found · ${result.hop_count} ${result.hop_count === 1 ? "hop" : "hops"}` : result.status === "query_error" ? "Graph query failed" : result.status === "unknown_source" ? "Source entity not found" : result.status === "unknown_target" ? "Target entity not found" : "No qualifying path within selected depth"}</strong><span>Depth limit: {result.max_hops} · {result.data_source}</span></div>
      {result.message && <p>{result.message} This is not a safety guarantee.</p>}
      {result.error && <p>{result.error}</p>}
      {found && <div className="traversal-sequence" aria-label="Returned path sequence">{result.path.nodes.map((node, index) => <div key={`${node.id}-${index}`}><strong>{node.label || node.id}</strong><small>{node.type} · {node.id}</small>{result.path.edges[index] && <span>{result.path.edges[index].type.replaceAll("_", " ")} →</span>}</div>)}</div>}
    </div>}
    <p className="traversal-disclaimer">A missing path means none was returned within this selected depth in the synthetic graph; it does not establish real-world safety.</p>
  </section>;
}

function HeroNetwork() {
  return <svg className="hero-network" viewBox="0 0 640 480" role="img" aria-label="Illustration of connected security entities and a highlighted access path">
    <defs><linearGradient id="route-gradient" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#a88cff" /><stop offset="1" stopColor="#5de4d1" /></linearGradient><radialGradient id="network-glow"><stop stopColor="#6745a7" stopOpacity=".26" /><stop offset="1" stopColor="#111426" stopOpacity="0" /></radialGradient></defs>
    <circle cx="340" cy="238" r="220" fill="url(#network-glow)" />
    <g className="network-lines" fill="none" stroke="#546182" strokeOpacity=".42" strokeWidth="1.4"><path d="M87 93 214 151 338 93 482 154 548 276 431 365 288 330 168 399 87 286 214 151 288 330 482 154M87 93l81 306m170-306 93 272m117-89L288 330" /></g>
    <path className="network-route" d="M87 93 214 151 288 330 431 365" fill="none" stroke="url(#route-gradient)" strokeWidth="3" strokeLinecap="round" />
    <g className="network-nodes"><circle cx="87" cy="93" r="10"/><circle cx="214" cy="151" r="9"/><circle cx="338" cy="93" r="7"/><circle cx="482" cy="154" r="9"/><circle cx="548" cy="276" r="7"/><circle cx="431" cy="365" r="10"/><circle cx="288" cy="330" r="9"/><circle cx="168" cy="399" r="7"/><circle cx="87" cy="286" r="8"/></g>
    <g className="network-labels"><text x="50" y="70">IDENTITY</text><text x="184" y="130">SESSION</text><text x="394" y="398">DATA ASSET</text></g>
  </svg>;
}

function LandingPage({ initialScenarioId, onNavigate }) {
  const [alerts, setAlerts] = useState([]);
  const [apiState, setApiState] = useState("loading");
  const [preview, setPreview] = useState(null);
  const firstAlert = alerts.find((alert) => alert.id === initialScenarioId) || alerts[0];

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const catalog = await apiRequest(`${API}/api/alerts`);
        if (!mounted) return;
        setAlerts(catalog.items || []);
        setApiState("available");
        const example = catalog.items?.find((alert) => alert.id === "BH-1042");
        if (!example) {
          return;
        }
        try {
          const result = await apiRequest(`${API}/api/investigations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ alert_id: example.id }),
          });
          if (mounted) {
            setPreview(result);
          }
        } catch {
          if (mounted) setPreview(null);
        }
      } catch {
        if (mounted) {
          setApiState("unavailable");
        }
      }
    })();
    return () => { mounted = false; };
  }, []);

  const openWorkspace = (scenarioId = firstAlert?.id || "") => onNavigate("workspace", scenarioId);
  const examplePath = preview?.paths?.[0];

  return <div className="app-shell landing-shell">
    <header className="topbar landing-topbar">
      <a className="brand" href="/" aria-label="BlastShield overview"><span className="brand-mark">✦</span><span><strong>BLAST<span>SHIELD</span></strong><small>GRAPH SECURITY</small></span></a>
      <nav className="top-nav" aria-label="Main navigation"><a href="/" aria-current="page">Overview</a><a href={workspaceHref(firstAlert?.id)} onClick={(event) => { event.preventDefault(); openWorkspace(); }}>Investigation Workspace</a></nav>
      <div className="topbar-status"><span className="source-badge"><Icon>◈</Icon> Synthetic FalkorDB</span><span className={`health-pill ${apiState === "available" ? "good" : "bad"}`}><i aria-hidden="true" />API {apiState === "loading" ? "Checking" : apiState === "available" ? "Available" : "Unavailable"}</span></div>
    </header>

    <main className="landing-main">
      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="hero-copy"><p className="eyebrow hero-eyebrow"><span /> GRAPH-POWERED SECURITY INVESTIGATION</p><h1 id="landing-title">See the path.<br /><span>Understand the risk.</span><br />Verify the response.</h1><p className="hero-subtitle">Evidence-backed security investigations that turn hidden cloud relationships into defensible response decisions.</p><div className="hero-actions"><a className="primary-button hero-primary" href={workspaceHref(firstAlert?.id)} onClick={(event) => { event.preventDefault(); openWorkspace(); }}>Launch Investigation Workspace <span aria-hidden="true">↗</span></a><a className="secondary-button hero-secondary" href="#how-it-works">Explore how it works <span aria-hidden="true">↓</span></a></div><p className="hero-truth">Local synthetic graph · Evidence-first · Containment simulation only</p></div>
        <div className="hero-art"><HeroNetwork /><div className="hero-art-caption"><span className="caption-dot" /> Modeled relationships, made inspectable</div></div>
        <div className="hero-orb hero-orb-one" aria-hidden="true" /><div className="hero-orb hero-orb-two" aria-hidden="true" />
      </section>

      {apiState === "unavailable" && <div className="notice-banner warning" role="status"><Icon>!</Icon><div><strong>Investigation API unavailable</strong><span>You can explore how the product works. Live scenario cards and graph evidence will appear when the local API is available.</span></div></div>}

      <Reveal><section className="landing-section problem-section" id="problem"><div className="section-copy"><p className="eyebrow">THE PROBLEM</p><h2>Security risk hides in relationships.</h2><p>An identity may not directly access a critical database. Potential reachability can emerge through a session, a workload, and an assumed IAM role.</p><p>A graph makes those recorded relationships traversable—so an investigator can inspect the actual route instead of relying on a disconnected list of permissions.</p></div><div className="relationship-motif" aria-label="Illustrative chain: identity to session to workload to role to data asset"><span className="motif-label">Illustrative relationship chain</span><div className="motif-chain"><b>Identity</b><i>HAS_SESSION</i><b>Session</b><i>CAN_ACCESS</i><b>Workload</b><i>RUNS_AS</i><b>IAM role</b><i>CAN_READ</i><b>Data</b></div><small>Examples of relationship types used in the synthetic graph.</small></div></section></Reveal>

      <Reveal><section className="landing-section process-section" id="how-it-works"><div className="section-heading"><p className="eyebrow">HOW IT WORKS</p><h2>From finding to verifiable evidence.</h2><p>A straightforward workflow, grounded in the graph.</p></div><div className="process-grid"><article className="process-card"><span className="step-number">01</span><div className="step-symbol" aria-hidden="true">⌕</div><h3>Investigate</h3><p>Select a security finding and identify its modeled target from the canonical alert catalog.</p></article><article className="process-card"><span className="step-number">02</span><div className="step-symbol" aria-hidden="true">⌘</div><h3>Trace</h3><p>FalkorDB runs reviewed graph queries and returns the actual access paths and relationships.</p></article><article className="process-card"><span className="step-number">03</span><div className="step-symbol" aria-hidden="true">↗</div><h3>Explain and verify</h3><p>Gemini explains returned evidence through fixed MCP tools. A synthetic route change is followed by a fresh graph query.</p></article></div></section></Reveal>

      <Reveal><section className="landing-section scenarios-section" id="scenarios"><div className="section-heading section-heading-row"><div><p className="eyebrow">FOUR INVESTIGATION SCENARIOS</p><h2>Start with a concrete question.</h2></div><p>Targets and titles are loaded from the API catalog.</p></div>{alerts.length ? <div className="landing-scenario-grid">{alerts.map((alert, index) => <article className="landing-scenario-card" key={alert.id}><div className="scenario-card-top"><span className="scenario-glyph" aria-hidden="true">{["⌖", "◇", "▣", "↗"][index % 4]}</span><span className="landing-alert-id">{alert.id}</span></div><p className="eyebrow scenario-card-type">{scenarioType(alert.scenario)}</p><h3>{alert.title}</h3><p className="scenario-description">{SCENARIO_DESCRIPTIONS[alert.scenario] || "Inspect modeled graph evidence for this security finding."}</p><p className="scenario-target"><span>Target</span><b>{alert.target_type} · {alert.target_id}</b></p><a className="scenario-link" href={workspaceHref(alert.id)} onClick={(event) => { event.preventDefault(); openWorkspace(alert.id); }}>Try this scenario <span aria-hidden="true">→</span></a></article>)}</div> : <div className="catalog-empty" role="status"><strong>{apiState === "loading" ? "Loading scenario catalog…" : "Scenario catalog unavailable"}</strong><p>{apiState === "unavailable" ? "No live scenario claims are shown while the API is offline." : "The API returned no scenarios."}</p><a className="text-button" href={workspaceHref()} onClick={(event) => { event.preventDefault(); openWorkspace(); }}>Open workspace</a></div>}</section></Reveal>

      <Reveal><section className="landing-section graph-value-section" id="graph-reasoning"><div className="graph-value-copy"><p className="eyebrow">WHY GRAPH REASONING</p><h2>Don't just find an alert. Discover the path behind it.</h2><div className="capability-list"><article><span>01</span><div><h3>Graph-derived evidence</h3><p>Paths are returned by FalkorDB queries, with their nodes and relationships available to inspect.</p></div></article><article><span>02</span><div><h3>Bounded agent tools</h3><p>Gemini can call fixed, reviewed investigation tools through MCP—not generate arbitrary Cypher.</p></div></article><article><span>03</span><div><h3>Verifiable response</h3><p>Containment is simulated in the synthetic graph, then a fresh query checks what remains reachable.</p></div></article></div></div><div className="path-example panel"><div className="panel-header"><div><p className="eyebrow">{preview ? "LIVE STRUCTURED RESULT" : "TEACHING EXAMPLE"}</p><h3>{preview ? "Identity access path" : "A graph path, illustrated"}</h3></div>{preview && <span className={`outcome-badge ${preview.outcome}`}>{OUTCOME_LABELS[preview.outcome] || preview.outcome}</span>}</div>{preview && examplePath ? <><p className="example-note">Actual path returned by the structured investigation endpoint · {preview.data_source}</p><div className="example-path" aria-label="Actual returned path">{examplePath.nodes.map((node, index) => <div className="example-node" key={`${node.id}-${index}`}><strong>{node.label || node.id}</strong><small>{node.type} · {node.id}</small>{examplePath.edges[index] && <span className="example-edge">{examplePath.edges[index].type.replaceAll("_", " ")} <b aria-hidden="true">→</b></span>}</div>)}</div></> : preview ? <div className="example-empty"><strong>{preview.summary}</strong><p>This is the current API result; no example path was returned.</p></div> : <><p className="example-note">Illustrative only—not a live or verified graph result.</p><div className="example-path illustrative" aria-label="Illustrative path, not live graph data"><div className="example-node"><strong>Identity</strong><small>Example entity</small><span className="example-edge">HAS_SESSION <b aria-hidden="true">→</b></span></div><div className="example-node"><strong>Session</strong><small>Example entity</small><span className="example-edge">CAN_ACCESS <b aria-hidden="true">→</b></span></div><div className="example-node"><strong>Workload</strong><small>Example entity</small><span className="example-edge">RUNS_AS <b aria-hidden="true">→</b></span></div><div className="example-node"><strong>IAM role</strong><small>Example entity</small><span className="example-edge">CAN_READ <b aria-hidden="true">→</b></span></div><div className="example-node"><strong>Data asset</strong><small>Example entity</small></div></div></>}</div></section></Reveal>

      <Reveal><section className="landing-trust-section" id="transparency"><div><p className="eyebrow">TRANSPARENCY</p><h2>Built for evidence, not assumptions.</h2><p>BlastShield is a local synthetic-data investigation prototype—not a live cloud security platform.</p></div><ul><li>Results describe modeled potential reachability; they do not prove exploitation.</li><li>Deterministic demonstration risk rules are not calibrated breach probabilities.</li><li>Containment changes the synthetic graph only; no live cloud session is modified.</li><li>No qualifying path is not a guarantee of real-world safety.</li></ul></section></Reveal>

      <section className="landing-final-cta"><div><p className="eyebrow">EVIDENCE YOU CAN FOLLOW</p><h2>Open the investigation workspace.</h2><p>Choose a scenario, follow its modeled path, and inspect the evidence for yourself.</p></div><a className="primary-button hero-primary" href={workspaceHref(firstAlert?.id)} onClick={(event) => { event.preventDefault(); openWorkspace(); }}>Open BlastShield Workspace <span aria-hidden="true">↗</span></a></section>
    </main>
    <footer className="landing-footer"><a className="footer-brand" href="/">BlastShield</a><span>WeMakeDevs Graph Hacks · Track 01</span><nav aria-label="Footer links"><a href="#problem">The problem</a><a href="#how-it-works">How it works</a><a href="#transparency">Transparency</a></nav></footer>
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
