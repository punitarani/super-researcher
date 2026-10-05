const state = {
  config: null,
  agents: null,
  agentTimer: null,
  theme: "light",
  depth: "high",
  breadth: "high",
  activeRunId: null,
  lastCompletedRunId: null,
  pendingPayload: null,
  pollTimer: null,
  activeTab: "scope",
  atlas: {
    corpora: [],
    latestCorpusId: null,
    selectedCorpusId: null,
    dependency: null,
    postprocessDependency: null,
    points: [],
    selections: {},
    selectedChunkId: null,
    selectedChunk: null,
    jobId: null,
    jobTimer: null,
    postprocessJobId: null,
    postprocessTimer: null,
    hoverPointId: null,
    officialModule: null,
    officialView: null,
    officialMounted: false,
    officialUnavailable: false
  },
  curate: {
    topicPayload: null,
    jobId: null,
    jobTimer: null,
    collapsed: {},
    dirty: false
  },
  compose: {
    payload: null,
    activePayload: null,
    jobId: null,
    jobTimer: null,
    dirty: false,
    refineStack: []
  },
  publish: {
    payload: null,
    visuals: null,
    jobId: null,
    jobTimer: null,
    customPrompts: {},
    customPromptGroups: {},
    visualSelections: {},
    visualDirty: false,
    visualSectionId: "all",
    visualExtractionId: "all"
  },
  publishReport: {
    payload: null,
    preview: null,
    error: "",
    corpusId: null,
    templateId: "mckinsey",
    outputDir: "",
    metadata: {},
    previewTimer: null,
    previewProgressTimer: null,
    previewLoading: false,
    previewProgress: 0,
    previewLoadingStartedAt: 0,
    previewRequestId: 0,
    formats: { pdf: true, html: true, docx: false, latex: false }
  }
};

const depthResults = {
  low: 3,
  medium: 7,
  high: 10,
  extra_high: 15,
  ludicrous: 50
};

const breadthLevels = {
  low: 1,
  medium: 3,
  high: 5
};

const THEME_STORAGE_KEY = "superresearcher.theme";
const STAGE_IDS = ["scope", "map", "curate", "compose", "publish", "pulbish"];
const COMPOSE_REFINE_THRESHOLDS = [0.25, 0.2, 0.15, 0.1, 0.08];
const $ = (id) => document.getElementById(id);
const JSON_HEADERS = { "Content-Type": "application/json" };
const SIGN_IN_STATES = new Set(["signed_out", "wrong_auth", "expired"]);

async function init() {
  state.theme = storedTheme();
  applyTheme(state.theme);
  bindTabs();
  bindThemeToggle();
  bindSegments();
  bindAtlas();
  bindCurate();
  bindCompose();
  bindPublish();
  bindPublishReport();
  bindAgents();
  $("runForm").addEventListener("submit", onSubmit);
  $("cancelLargeRun").addEventListener("click", () => {
    $("confirmModal").hidden = true;
    state.pendingPayload = null;
  });
  $("continueLargeRun").addEventListener("click", () => {
    $("confirmModal").hidden = true;
    if (state.pendingPayload) startRun(state.pendingPayload);
  });
  $("finalSourceCount").addEventListener("input", updateEstimate);
  loadAgents().catch((error) => console.error(error));
  state.config = await fetchJson("/api/config");
  $("storageRoot").value = state.config.default_storage_root;
  updateEstimate();
  setRunState({ state: "idle", progress: 0, milestone: "Queued", topic: "No active run", counts: {}, events: [] });
  await initAtlas();
}

function storedTheme() {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch (_) {
    return "light";
  }
}

function bindThemeToggle() {
  $("lightThemeButton").addEventListener("click", () => setTheme("light"));
  $("darkThemeButton").addEventListener("click", () => setTheme("dark"));
}

function setTheme(theme) {
  state.theme = theme === "dark" ? "dark" : "light";
  try {
    localStorage.setItem(THEME_STORAGE_KEY, state.theme);
  } catch (_) {
    // localStorage may be unavailable in restrictive browser contexts.
  }
  applyTheme(state.theme);
  renderOfficialAtlasIfAvailable();
  drawAtlas();
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  $("lightThemeButton")?.classList.toggle("active", theme === "light");
  $("darkThemeButton")?.classList.toggle("active", theme === "dark");
}

function bindTabs() {
  STAGE_IDS.forEach((stage) => {
    $(`${stage}TabButton`).addEventListener("click", () => showTab(stage));
  });
}

function showTab(tab) {
  state.activeTab = tab;
  STAGE_IDS.forEach((stage) => {
    $(`${stage}Tab`).hidden = tab !== stage;
    $(`${stage}TabButton`).classList.toggle("active", tab === stage);
  });
  if (tab === "map") {
    window.setTimeout(drawAtlas, 0);
  }
  if (tab === "curate") {
    loadCurateForSelected();
  }
  if (tab === "compose") {
    loadComposeForSelected();
  }
  if (tab === "publish") {
    loadPublishForSelected();
  }
  if (tab === "pulbish") {
    loadPublishReportForSelected();
  }
}

function bindSegments() {
  document.querySelectorAll(".segmented[data-name]").forEach((group) => {
    group.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      group.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      state[group.dataset.name] = button.dataset.value;
      updateEstimate();
    });
  });
}

function bindAgents() {
  const modal = $("agentModal");
  const open = () => {
    modal.hidden = false;
    loadAgents(true).catch(showAgentError);
  };
  $("agentButton").addEventListener("click", open);
  $("agentWarning").addEventListener("click", open);
  $("agentClose").addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (event) => {
    if (event.target === modal) modal.hidden = true;
  });
  $("agentRecheck").addEventListener("click", () => loadAgents(true).catch(showAgentError));
  $("agentSignIn").addEventListener("click", signInWithChatGPT);
  $("agentPicker").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-agent]");
    if (button) selectAgent(button.dataset.agent).catch(showAgentError);
  });
}

async function loadAgents(refresh = false) {
  state.agents = await fetchJson(`/api/agents${refresh ? "?refresh=1" : ""}`);
  renderAgents();
  return state.agents;
}

async function selectAgent(agentId) {
  state.agents = await fetchJson("/api/agents", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ selected: agentId })
  });
  renderAgents();
}

function renderAgents() {
  const agent = state.agents.agents.find((item) => item.id === state.agents.selected);
  const chip = $("agentButton");
  chip.textContent = `${agent.label} · ${agent.ready ? "Ready" : "Set up"}`;
  chip.classList.toggle("needs-setup", !agent.ready);
  chip.title = agent.message;
  $("agentPicker").querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.agent === agent.id);
  });
  $("agentMessage").className = `agent-message ${agent.ready ? "ready" : "needs-setup"}`;
  $("agentMessage").innerHTML = withInlineCode(agent.message);
  $("agentSignIn").hidden = !SIGN_IN_STATES.has(agent.state);
  if (agent.ready) $("agentSignInHint").hidden = true;
  $("agentWarning").hidden = agent.ready;
  $("agentWarning").textContent = `${agent.label} isn't set up yet, so research runs will use built-in planning. Set up ${agent.label} →`;
}

async function signInWithChatGPT() {
  const hint = $("agentSignInHint");
  $("agentSignIn").disabled = true;
  try {
    const { url } = await fetchJson("/api/agents/codex/login", { method: "POST", headers: JSON_HEADERS, body: "{}" });
    hint.innerHTML = `Finish signing in to ChatGPT in your browser. No tab opened? <a href="${escapeHtml(url)}" target="_blank" rel="noopener">Open the sign-in page</a>.`;
    hint.hidden = false;
    waitForSignIn(Date.now() + 5 * 60 * 1000);
  } catch (error) {
    showAgentError(error);
  } finally {
    $("agentSignIn").disabled = false;
  }
}

function waitForSignIn(deadline) {
  window.clearInterval(state.agentTimer);
  state.agentTimer = window.setInterval(async () => {
    try {
      const codex = (await loadAgents()).agents.find((item) => item.id === "codex");
      if (codex.ready || Date.now() > deadline) window.clearInterval(state.agentTimer);
    } catch (error) {
      window.clearInterval(state.agentTimer);
      showAgentError(error);
    }
  }, 2000);
}

function showAgentError(error) {
  $("agentMessage").className = "agent-message needs-setup";
  $("agentMessage").textContent = error.message;
}

function withInlineCode(text) {
  return escapeHtml(text).replace(/`([^`]+)`/g, "<code>$1</code>");
}

function updateEstimate() {
  const finalSources = clampFinalSources($("finalSourceCount").value);
  $("estimateDepth").textContent = depthResults[state.depth] || 10;
  $("estimateBreadth").textContent = breadthLevels[state.breadth] || 5;
  $("estimateSources").textContent = finalSources;
}

function onSubmit(event) {
  event.preventDefault();
  $("formError").textContent = "";
  const payload = formPayload();
  if (!payload.topic.trim()) {
    $("formError").textContent = "Topic is required.";
    return;
  }
  const estimate = roughCandidateEstimate(payload);
  if (estimate > 1000 || payload.final_source_count > 200 || payload.depth === "ludicrous") {
    $("confirmText").textContent = `This run may check roughly ${estimate.toLocaleString()} search results and target ${payload.final_source_count} final sources.`;
    state.pendingPayload = payload;
    $("confirmModal").hidden = false;
    return;
  }
  startRun(payload);
}

function formPayload() {
  return {
    topic: $("topic").value,
    context: $("context").value,
    depth: state.depth,
    breadth: state.breadth,
    final_source_count: clampFinalSources($("finalSourceCount").value),
    storage_root: $("storageRoot").value,
    audience: $("audience").value,
    geographic_scope: $("geographicScope").value,
    time_horizon: $("timeHorizon").value,
    objective: $("objective").value,
    must_include: $("mustInclude").value,
    must_exclude: $("mustExclude").value,
    preferred_sources: $("preferredSources").value,
    disallowed_sources: $("disallowedSources").value
  };
}

function clampFinalSources(value) {
  const n = Number.parseInt(value || "120", 10);
  return Math.max(1, Math.min(Number.isFinite(n) ? n : 120, 500));
}

function roughCandidateEstimate(payload) {
  const queryBase = { low: 9, medium: 28, high: 52 }[payload.breadth] || 52;
  const mechanisms = payload.breadth === "high" ? 2.1 : payload.breadth === "medium" ? 1.7 : 1.3;
  return Math.round(queryBase * mechanisms * (depthResults[payload.depth] || 10));
}

async function startRun(payload) {
  state.pendingPayload = null;
  $("formError").textContent = "";
  setRunState({ state: "queued", progress: 0, milestone: "Queued", topic: payload.topic, counts: {}, events: [] });
  try {
    const run = await fetchJson("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    state.activeRunId = run.run_id;
    setRunState(run);
    if (state.pollTimer) window.clearInterval(state.pollTimer);
    state.pollTimer = window.setInterval(pollRun, 2000);
  } catch (error) {
    $("formError").textContent = error.message;
  }
}

async function pollRun() {
  if (!state.activeRunId) return;
  try {
    const run = await fetchJson(`/api/runs/${encodeURIComponent(state.activeRunId)}`);
    setRunState(run);
    if (run.state === "completed" || run.state === "failed") {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  } catch (error) {
    $("formError").textContent = error.message;
  }
}

function setRunState(run) {
  $("runTitle").textContent = run.topic || "No active run";
  $("runState").textContent = titleCase(run.state || "idle");
  $("runState").className = `state ${run.state || "idle"}`;
  $("progressBar").style.width = `${run.progress || 0}%`;
  $("milestone").textContent = run.milestone || "-";
  $("candidateCount").textContent = run.counts?.candidate_sources || 0;
  $("selectedCount").textContent = run.counts?.selected_sources || 0;
  $("qualityVerdict").textContent = run.counts?.quality_verdict || run.quality?.verdict || "-";
  $("dossierPath").textContent = run.dossier_path || "-";
  renderFiles(run.files || {});
  renderEvents(run.events || []);
  if (run.state === "completed" && run.run_id && run.run_id !== state.lastCompletedRunId) {
    state.lastCompletedRunId = run.run_id;
    if (run.dossier_path || run.run_id.endsWith("_Corpus")) {
      onCorpusCompleted(run.run_id);
    }
  }
}

function renderFiles(files) {
  const entries = Object.entries(files);
  $("fileList").innerHTML = entries.length
    ? entries.map(([name, path]) => `<div><strong>${escapeHtml(labelize(name))}</strong>: ${escapeHtml(path)}</div>`).join("")
    : `<div>-</div>`;
}

function renderEvents(events) {
  const recent = events.slice(-12).reverse();
  $("eventLog").innerHTML = recent.length
    ? recent.map((event) => `<div class="event"><time>${escapeHtml(event.time || "")}</time>${escapeHtml(event.message || "")}</div>`).join("")
    : `<div class="event">No updates yet.</div>`;
}

function bindCurate() {
  $("runTopicDiscovery").addEventListener("click", runTopicDiscovery);
  $("saveCuratedTopics").addEventListener("click", saveCuratedTopics);
  $("selectAllTopics").addEventListener("click", () => setAllTopicSelection(true));
  $("selectNoTopics").addEventListener("click", () => setAllTopicSelection(false));
  $("expandAllTopics").addEventListener("click", () => {
    state.curate.collapsed = {};
    renderCurate();
  });
  $("collapseAllTopics").addEventListener("click", () => {
    const collapsed = {};
    collectTopicIds(state.curate.topicPayload?.topics || []).forEach((id) => {
      collapsed[id] = true;
    });
    state.curate.collapsed = collapsed;
    renderCurate();
  });
  $("topicTree").addEventListener("click", onTopicTreeClick);
  $("topicTree").addEventListener("change", onTopicTreeChange);
}

async function loadCurateForSelected() {
  const corpusId = state.atlas.selectedCorpusId;
  state.curate.topicPayload = null;
  state.curate.dirty = false;
  state.curate.collapsed = {};
  if (!corpusId) {
    $("curateCorpus").textContent = "Select a corpus in Map first.";
    setTopicStatus("idle", "Idle", 0, "Select a corpus in Map first.");
    renderCurate();
    return;
  }
  const corpus = state.atlas.corpora.find((row) => row.id === corpusId);
  $("curateCorpus").textContent = corpus ? corpus.name : corpusId;
  setTopicStatus("idle", "Loading", 10, "Loading topic artifacts.");
  try {
    state.curate.topicPayload = await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/topics`);
    const rawExists = Boolean(state.curate.topicPayload?.raw?.exists);
    setTopicStatus(rawExists ? "completed" : "idle", rawExists ? "Ready" : "Not run", rawExists ? 100 : 0, rawExists ? "Topic tree ready for curation." : "Run topic discovery after the Atlas is built.");
  } catch (error) {
    setTopicStatus("failed", "Load failed", 0, error.message);
  }
  renderCurate();
}

function renderCurate() {
  const payload = state.curate.topicPayload;
  const topics = payload?.topics || [];
  const hasRaw = Boolean(payload?.raw?.exists);
  const hasTopics = topics.length > 0;
  $("runTopicDiscovery").textContent = hasRaw ? "Topic Discovery Complete" : "Run Topic Discovery";
  $("runTopicDiscovery").disabled = !state.atlas.selectedCorpusId || hasRaw;
  $("selectAllTopics").disabled = !hasTopics;
  $("selectNoTopics").disabled = !hasTopics;
  $("expandAllTopics").disabled = !hasTopics;
  $("collapseAllTopics").disabled = !hasTopics;
  $("saveCuratedTopics").disabled = !hasTopics;
  if (!state.atlas.selectedCorpusId) {
    $("topicTreeEmpty").hidden = false;
    $("topicTreeEmpty").textContent = "Select a corpus in Map first.";
    $("topicTree").innerHTML = "";
    return;
  }
  if (!hasRaw) {
    $("topicTreeEmpty").hidden = false;
    $("topicTreeEmpty").textContent = "No topic tree yet. Run topic discovery after building the Atlas.";
    $("topicTree").innerHTML = "";
    return;
  }
  if (!hasTopics) {
    $("topicTreeEmpty").hidden = false;
    $("topicTreeEmpty").textContent = "Topic discovery did not return editable topics.";
    $("topicTree").innerHTML = "";
    return;
  }
  $("topicTreeEmpty").hidden = true;
  $("topicTree").innerHTML = renderTopicNodes(topics);
}

function renderTopicNodes(nodes) {
  return nodes
    .map((node) => {
      const hasChildren = Boolean(node.children?.length);
      const collapsed = Boolean(state.curate.collapsed[node.id]);
      const children = hasChildren && !collapsed ? `<div class="topic-children">${renderTopicNodes(node.children)}</div>` : "";
      const toggle = hasChildren
        ? `<button type="button" class="topic-toggle" data-topic-toggle="${escapeHtml(node.id)}" aria-label="${collapsed ? "Expand" : "Collapse"} topic">${collapsed ? "+" : "-"}</button>`
        : `<button type="button" class="topic-toggle placeholder" tabindex="-1">-</button>`;
      return `
        <div class="topic-node" data-topic-id="${escapeHtml(node.id)}">
          <div class="topic-row">
            ${toggle}
            <input class="topic-check" type="checkbox" data-topic-check="${escapeHtml(node.id)}" ${node.selected ? "checked" : ""} aria-label="Select topic">
            <input class="topic-label" type="text" data-topic-label="${escapeHtml(node.id)}" value="${escapeHtml(node.label || "")}" aria-label="Topic label">
            <button type="button" data-topic-move="up" data-topic-id="${escapeHtml(node.id)}" aria-label="Move topic up">↑</button>
            <button type="button" data-topic-move="down" data-topic-id="${escapeHtml(node.id)}" aria-label="Move topic down">↓</button>
          </div>
          ${children}
        </div>
      `;
    })
    .join("");
}

function onTopicTreeClick(event) {
  const toggle = event.target.closest("[data-topic-toggle]");
  if (toggle) {
    const id = toggle.dataset.topicToggle;
    state.curate.collapsed[id] = !state.curate.collapsed[id];
    renderCurate();
    return;
  }
  const move = event.target.closest("[data-topic-move]");
  if (move) {
    moveTopicNode(move.dataset.topicId, move.dataset.topicMove);
  }
}

function onTopicTreeChange(event) {
  const check = event.target.closest("[data-topic-check]");
  if (check) {
    const found = findTopicNode(check.dataset.topicCheck);
    if (found) {
      setTopicSelection(found.node, check.checked);
      state.curate.dirty = true;
      renderCurate();
    }
    return;
  }
  const label = event.target.closest("[data-topic-label]");
  if (label) {
    const found = findTopicNode(label.dataset.topicLabel);
    if (found) {
      found.node.label = label.value.trim() || found.node.label;
      state.curate.dirty = true;
      renderCurate();
    }
  }
}

function findTopicNode(id, nodes = state.curate.topicPayload?.topics || []) {
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (node.id === id) return { node, siblings: nodes, index };
    const child = findTopicNode(id, node.children || []);
    if (child) return child;
  }
  return null;
}

function moveTopicNode(id, direction) {
  const found = findTopicNode(id);
  if (!found) return;
  const offset = direction === "up" ? -1 : 1;
  const target = found.index + offset;
  if (target < 0 || target >= found.siblings.length) return;
  const [node] = found.siblings.splice(found.index, 1);
  found.siblings.splice(target, 0, node);
  state.curate.dirty = true;
  renderCurate();
}

function setTopicSelection(node, selected) {
  node.selected = selected;
  (node.children || []).forEach((child) => setTopicSelection(child, selected));
}

function setAllTopicSelection(selected) {
  (state.curate.topicPayload?.topics || []).forEach((node) => setTopicSelection(node, selected));
  state.curate.dirty = true;
  renderCurate();
}

function collectTopicIds(nodes) {
  return nodes.flatMap((node) => [node.id, ...collectTopicIds(node.children || [])]);
}

async function runTopicDiscovery() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) {
    setTopicStatus("failed", "No corpus", 0, "Select a corpus in Map first.");
    return;
  }
  try {
    const job = await fetchJson("/api/topics/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId, force: false })
    });
    state.curate.jobId = job.job_id;
    renderTopicJob(job);
    if (state.curate.jobTimer) window.clearInterval(state.curate.jobTimer);
    state.curate.jobTimer = window.setInterval(pollTopicDiscoveryJob, 1000);
  } catch (error) {
    setTopicStatus("failed", "Discovery failed", 0, error.message);
  }
}

async function pollTopicDiscoveryJob() {
  if (!state.curate.jobId) return;
  try {
    const job = await fetchJson(`/api/topics/jobs/${encodeURIComponent(state.curate.jobId)}`);
    renderTopicJob(job);
    if (job.state === "completed" || job.state === "failed") {
      window.clearInterval(state.curate.jobTimer);
      state.curate.jobTimer = null;
      if (job.state === "completed") await loadCurateForSelected();
    }
  } catch (error) {
    window.clearInterval(state.curate.jobTimer);
    state.curate.jobTimer = null;
    setTopicStatus("failed", "Poll failed", 0, error.message);
  }
}

function renderTopicJob(job) {
  setTopicStatus(job.state || "idle", titleCase(job.state || "idle"), job.progress || 0, job.error || job.stage || "Working");
}

function setTopicStatus(stateName, label, progress, stage) {
  $("topicStatus").textContent = label;
  $("topicStatus").className = `state ${stateName}`;
  $("topicProgressBar").style.width = `${progress || 0}%`;
  $("topicStage").textContent = stage || "";
}

async function saveCuratedTopics() {
  const corpusId = state.atlas.selectedCorpusId;
  const topics = state.curate.topicPayload?.topics || [];
  if (!corpusId || !topics.length) return;
  try {
    await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/topics/curated`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topics })
    });
    state.curate.dirty = false;
    await loadCurateForSelected();
    setTopicStatus("completed", "Saved", 100, "Curated topic tree saved.");
  } catch (error) {
    setTopicStatus("failed", "Save failed", 100, error.message);
  }
}

function bindCompose() {
  $("generateComposeTerms").addEventListener("click", () => startComposeBuild(false));
  $("regenerateComposeTerms").addEventListener("click", () => startComposeBuild(true));
  $("refineComposeTerms").addEventListener("click", refineComposeTerms);
  $("undoRefineComposeTerms").addEventListener("click", undoComposeRefine);
  $("finalizeComposeTerms").addEventListener("click", finalizeComposeTerms);
  $("composeSubtopicList").addEventListener("change", onComposeSubtopicChange);
  $("composeTagGroups").addEventListener("click", onComposeTagClick);
}

async function loadComposeForSelected(preferGenerated = false) {
  const corpusId = state.atlas.selectedCorpusId;
  state.compose.payload = null;
  state.compose.activePayload = null;
  state.compose.dirty = false;
  state.compose.refineStack = [];
  if (!corpusId) {
    $("composeCorpus").textContent = "Select a corpus in Map first.";
    setComposeStatus("idle", "Idle", 0, "Select a corpus in Map first.");
    renderCompose();
    return;
  }
  const corpus = state.atlas.corpora.find((row) => row.id === corpusId);
  $("composeCorpus").textContent = corpus ? corpus.name : corpusId;
  setComposeStatus("idle", "Loading", 10, "Loading Compose artifacts.");
  try {
    state.compose.payload = await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/compose`);
    const finalized = Boolean(state.compose.payload.finalized?.exists);
    const generated = Boolean(state.compose.payload.generated?.valid);
    state.compose.activePayload = cloneJson(preferGenerated && generated ? state.compose.payload.generated.payload : state.compose.payload.active);
    setComposeStatus(
      finalized || generated ? "completed" : "idle",
      preferGenerated && generated ? "Ready" : finalized ? "Finalized" : generated ? "Ready" : "Not generated",
      finalized || generated ? 100 : 0,
      preferGenerated && generated ? "Generated terms are ready." : finalized ? "Finalized terms are ready." : generated ? "Generated terms are ready." : "Generate terms from curated subtopics."
    );
  } catch (error) {
    setComposeStatus("failed", "Load failed", 0, error.message);
  }
  renderCompose();
}

function renderCompose() {
  const payload = state.compose.payload;
  const active = state.compose.activePayload;
  const selectedSubtopics = payload?.selected_subtopics || [];
  const subtopics = active?.subtopics || selectedSubtopics.map((item) => ({ ...item, topic_id: item.topic_id || item.id, enabled: true, tags: [] }));
  const hasCorpus = Boolean(state.atlas.selectedCorpusId);
  const hasSubtopics = selectedSubtopics.length > 0 || subtopics.length > 0;
  const hasGenerated = Boolean(active?.subtopics?.length);
  const working = Boolean(state.compose.jobTimer);
  const selectedTags = composeSelectedTagCount(active);
  const enabledSubtopics = subtopics.filter((item) => item.enabled !== false).length;
  const refineState = composeRefineState(active, working, hasGenerated);

  $("composeSubtopicCount").textContent = `${enabledSubtopics.toLocaleString()} subtopics`;
  $("composeTagCount").textContent = `${selectedTags.toLocaleString()} tags`;
  $("composeRefineCount").textContent = `${refineState.passCount}/${COMPOSE_REFINE_THRESHOLDS.length}`;
  $("composeRefineCount").title = refineState.nextThreshold ? `Next pass hides terms in at least ${refineState.nextThreshold} subtopics.` : "";
  $("generateComposeTerms").disabled = !hasCorpus || !hasSubtopics || hasGenerated || working;
  $("regenerateComposeTerms").disabled = !hasCorpus || !hasSubtopics || working;
  $("refineComposeTerms").disabled = !refineState.canRefine;
  $("undoRefineComposeTerms").disabled = !refineState.canUndo;
  $("finalizeComposeTerms").disabled = !hasGenerated || working;

  $("composeSubtopicList").innerHTML = subtopics.length
    ? subtopics.map(renderComposeSubtopicRow).join("")
    : `<div class="compose-empty">Save curated topics first.</div>`;

  if (!hasGenerated) {
    $("composeEmpty").hidden = false;
    $("composeEmpty").textContent = hasSubtopics ? "Generate terms to review removable tags." : "No selected leaf subtopics found.";
    $("composeTagGroups").innerHTML = "";
    return;
  }
  const enabledGroups = subtopics.filter((item) => item.enabled !== false);
  $("composeEmpty").hidden = enabledGroups.length > 0;
  $("composeEmpty").textContent = enabledGroups.length ? "" : "All subtopics are removed.";
  $("composeTagGroups").innerHTML = enabledGroups.map(renderComposeTagGroup).join("");
}

function renderComposeSubtopicRow(subtopic) {
  const id = escapeHtml(subtopic.topic_id || subtopic.id || "");
  const enabled = subtopic.enabled !== false;
  const selectedTags = (subtopic.tags || []).filter((tag) => tag.selected !== false).length;
  const parent = subtopic.parent_label || (subtopic.path || []).slice(-2, -1)[0] || "";
  return `
    <label class="compose-subtopic-row ${enabled ? "" : "disabled"}">
      <input type="checkbox" data-compose-subtopic="${id}" ${enabled ? "checked" : ""}>
      <span>
        <strong>${escapeHtml(subtopic.label || "")}</strong>
        <small>${escapeHtml(parent || "Top level")}</small>
      </span>
      <em>${selectedTags.toLocaleString()}</em>
    </label>
  `;
}

function renderComposeTagGroup(subtopic) {
  const topicId = escapeHtml(subtopic.topic_id || "");
  const tags = (subtopic.tags || []).filter((tag) => tag.selected !== false);
  return `
    <article class="compose-tag-group" data-compose-topic="${topicId}">
      <div class="compose-tag-head">
        <div>
          <h2>${escapeHtml(subtopic.label || "")}</h2>
          <p class="subtle">${escapeHtml((subtopic.path || []).join(" > "))}</p>
        </div>
        <span>${tags.length.toLocaleString()} tags</span>
      </div>
      <div class="tag-cloud">
        ${tags.map((tag) => renderComposeTag(topicId, tag)).join("") || `<span class="compose-empty">No terms selected.</span>`}
      </div>
    </article>
  `;
}

function renderComposeTag(topicId, tag) {
  return `
    <button type="button" class="tag-chip" data-compose-tag-remove="${escapeHtml(tag.id || "")}" data-compose-topic="${topicId}" title="Remove term">
      <span>${escapeHtml(tag.label || "")}</span>
      <strong aria-hidden="true">×</strong>
    </button>
  `;
}

function onComposeSubtopicChange(event) {
  const input = event.target.closest("[data-compose-subtopic]");
  if (!input || !state.compose.activePayload) return;
  const subtopic = findComposeSubtopic(input.dataset.composeSubtopic);
  if (!subtopic) return;
  subtopic.enabled = input.checked;
  state.compose.dirty = true;
  renderCompose();
}

function onComposeTagClick(event) {
  const button = event.target.closest("[data-compose-tag-remove]");
  if (!button || !state.compose.activePayload) return;
  const subtopic = findComposeSubtopic(button.dataset.composeTopic);
  if (!subtopic) return;
  const tag = (subtopic.tags || []).find((item) => item.id === button.dataset.composeTagRemove);
  if (!tag) return;
  tag.selected = false;
  state.compose.dirty = true;
  renderCompose();
}

function findComposeSubtopic(topicId) {
  return (state.compose.activePayload?.subtopics || []).find((item) => item.topic_id === topicId || item.id === topicId);
}

function composeRefineState(payload, working = false, hasGenerated = Boolean(payload?.subtopics?.length)) {
  const passCount = state.compose.refineStack.length;
  const nextPass = passCount;
  const common = hasGenerated && nextPass < COMPOSE_REFINE_THRESHOLDS.length ? commonComposeTerms(payload, nextPass) : [];
  return {
    passCount,
    nextThreshold: common.threshold || 0,
    commonTerms: common,
    canRefine: hasGenerated && !working && nextPass < COMPOSE_REFINE_THRESHOLDS.length && common.length > 0,
    canUndo: !working && passCount > 0
  };
}

function commonComposeTerms(payload, passIndex) {
  const enabledSubtopics = (payload?.subtopics || []).filter((subtopic) => subtopic.enabled !== false);
  const minimum = Math.max(3, Math.ceil(enabledSubtopics.length * (COMPOSE_REFINE_THRESHOLDS[passIndex] || 1)));
  const counts = new Map();
  const labels = new Map();
  enabledSubtopics.forEach((subtopic) => {
    const seen = new Set();
    (subtopic.tags || []).forEach((tag) => {
      if (tag.selected === false) return;
      const normalized = tag.normalized || normalizeClientLabel(tag.label);
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      counts.set(normalized, (counts.get(normalized) || 0) + 1);
      if (!labels.has(normalized)) labels.set(normalized, tag.label || normalized);
    });
  });
  const terms = Array.from(counts.entries())
    .filter(([, count]) => count >= minimum)
    .sort((left, right) => right[1] - left[1] || labels.get(left[0]).localeCompare(labels.get(right[0])))
    .map(([normalized, count]) => ({ normalized, label: labels.get(normalized), count }));
  terms.threshold = minimum;
  return terms;
}

function refineComposeTerms() {
  const active = state.compose.activePayload;
  const refineState = composeRefineState(active);
  if (!refineState.canRefine) return;
  const common = new Set(refineState.commonTerms.map((term) => term.normalized));
  const affected = [];
  (active.subtopics || []).forEach((subtopic) => {
    if (subtopic.enabled === false) return;
    (subtopic.tags || []).forEach((tag) => {
      const normalized = tag.normalized || normalizeClientLabel(tag.label);
      if (tag.selected === false || !common.has(normalized)) return;
      tag.selected = false;
      affected.push({ topic_id: subtopic.topic_id, tag_id: tag.id });
    });
  });
  if (!affected.length) return;
  state.compose.refineStack.push({
    pass: state.compose.refineStack.length + 1,
    affected,
    terms: refineState.commonTerms,
    threshold: refineState.nextThreshold
  });
  state.compose.dirty = true;
  setComposeStatus(
    "completed",
    "Refined",
    100,
    `Refine pass ${state.compose.refineStack.length}/${COMPOSE_REFINE_THRESHOLDS.length} hid ${affected.length.toLocaleString()} tags from ${refineState.commonTerms.length.toLocaleString()} common terms.`
  );
  renderCompose();
}

function undoComposeRefine() {
  const last = state.compose.refineStack.pop();
  if (!last || !state.compose.activePayload) return;
  const affected = new Set(last.affected.map((item) => `${item.topic_id}::${item.tag_id}`));
  (state.compose.activePayload.subtopics || []).forEach((subtopic) => {
    (subtopic.tags || []).forEach((tag) => {
      if (affected.has(`${subtopic.topic_id}::${tag.id}`)) tag.selected = true;
    });
  });
  state.compose.dirty = true;
  setComposeStatus(
    "completed",
    "Restored",
    100,
    `Undo restored refine pass ${last.pass}/${COMPOSE_REFINE_THRESHOLDS.length}.`
  );
  renderCompose();
}

async function startComposeBuild(force) {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) {
    setComposeStatus("failed", "No corpus", 0, "Select a corpus in Map first.");
    return;
  }
  try {
    state.compose.refineStack = [];
    const job = await fetchJson("/api/compose/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId, force })
    });
    state.compose.jobId = job.job_id;
    renderComposeJob(job);
    renderCompose();
    if (state.compose.jobTimer) window.clearInterval(state.compose.jobTimer);
    state.compose.jobTimer = window.setInterval(pollComposeJob, 1000);
  } catch (error) {
    setComposeStatus("failed", "Build failed", 0, error.message);
  }
}

async function pollComposeJob() {
  if (!state.compose.jobId) return;
  try {
    const job = await fetchJson(`/api/compose/jobs/${encodeURIComponent(state.compose.jobId)}`);
    renderComposeJob(job);
    if (job.state === "completed" || job.state === "failed") {
      window.clearInterval(state.compose.jobTimer);
      state.compose.jobTimer = null;
      if (job.state === "completed") await loadComposeForSelected(true);
      renderCompose();
    }
  } catch (error) {
    window.clearInterval(state.compose.jobTimer);
    state.compose.jobTimer = null;
    setComposeStatus("failed", "Poll failed", 0, error.message);
  }
}

function renderComposeJob(job) {
  setComposeStatus(job.state || "idle", titleCase(job.state || "idle"), job.progress || 0, job.error || job.stage || "Working");
  const counts = job.counts || {};
  if (counts.subtopic_count || counts.enabled_subtopic_count) {
    $("composeSubtopicCount").textContent = `${(counts.enabled_subtopic_count || counts.subtopic_count || 0).toLocaleString()} subtopics`;
  }
  if (counts.selected_tag_count || counts.tag_count) {
    $("composeTagCount").textContent = `${(counts.selected_tag_count || counts.tag_count || 0).toLocaleString()} tags`;
  }
}

function setComposeStatus(stateName, label, progress, stage) {
  $("composeStatus").textContent = label;
  $("composeStatus").className = `state ${stateName}`;
  $("composeProgressBar").style.width = `${progress || 0}%`;
  $("composeStage").textContent = stage || "";
}

async function finalizeComposeTerms() {
  const corpusId = state.atlas.selectedCorpusId;
  const active = state.compose.activePayload;
  if (!corpusId || !active?.subtopics?.length) return;
  try {
    const result = await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/compose/finalized`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subtopics: active.subtopics })
    });
    state.compose.payload = result;
    state.compose.activePayload = cloneJson(result.active);
    state.compose.dirty = false;
    state.compose.refineStack = [];
    setComposeStatus("completed", "Finalized", 100, "Finalized terms are ready.");
    renderCompose();
  } catch (error) {
    setComposeStatus("failed", "Save failed", 100, error.message);
  }
}

function composeSelectedTagCount(payload) {
  return (payload?.subtopics || []).reduce((total, subtopic) => {
    if (subtopic.enabled === false) return total;
    return total + (subtopic.tags || []).filter((tag) => tag.selected !== false).length;
  }, 0);
}

function bindPublish() {
  $("planPublishPaper").addEventListener("click", planPublishPaper);
  $("compilePublishPaper").addEventListener("click", compilePublishPaper);
  $("publishTocList").addEventListener("input", onPublishPromptInput);
  $("togglePublishVisuals").addEventListener("click", () => toggleCollapse("togglePublishVisuals", "publishVisualBody"));
  $("togglePublishText").addEventListener("click", () => toggleCollapse("togglePublishText", "publishTextBody"));
  $("buildPublishVisuals").addEventListener("click", buildPublishVisuals);
  $("savePublishVisuals").addEventListener("click", savePublishVisuals);
  $("closeImagePeek").addEventListener("click", closeImagePeek);
  $("imagePeek").addEventListener("click", (event) => {
    if (event.target === $("imagePeek")) closeImagePeek();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("imagePeek").hidden) closeImagePeek();
  });
  $("publishVisualSectionFilter").addEventListener("change", () => {
    state.publish.visualSectionId = $("publishVisualSectionFilter").value || "all";
    renderPublishVisuals();
  });
  $("publishVisualTextFilter").addEventListener("change", () => {
    state.publish.visualExtractionId = $("publishVisualTextFilter").value || "all";
    renderPublishVisuals();
  });
  $("publishVisualList").addEventListener("click", onPublishVisualAction);
}

async function loadPublishForSelected() {
  const corpusId = state.atlas.selectedCorpusId;
  state.publish.payload = null;
  state.publish.visuals = null;
  state.publish.visualSelections = {};
  state.publish.visualDirty = false;
  if (!corpusId) {
    $("publishCorpus").textContent = "Select a corpus in Map first.";
    setPublishStatus("idle", "Idle", 0, "Select a corpus in Map first.");
    renderPublish();
    renderPublishVisuals();
    return;
  }
  const corpus = state.atlas.corpora.find((row) => row.id === corpusId);
  $("publishCorpus").textContent = corpus ? corpus.name : corpusId;
  setPublishStatus("idle", "Loading", 10, "Loading Compile artifacts.");
  try {
    state.publish.payload = await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/publish`);
    const loadedPrompts = { ...(state.publish.payload.state?.custom_prompts || state.publish.payload.plan?.payload?.custom_prompts || {}) };
    const planSections = state.publish.payload.plan?.payload?.sections || [];
    state.publish.customPromptGroups = derivePublishPromptGroups(planSections, loadedPrompts);
    state.publish.customPrompts = expandPublishCustomPrompts(planSections, state.publish.customPromptGroups);
    const ready = Boolean(state.publish.payload.ready);
    const hasPlan = Boolean(state.publish.payload.plan?.exists);
    const completed = state.publish.payload.state?.state === "completed";
    setPublishStatus(
      completed ? "completed" : ready ? "idle" : "failed",
      completed ? "Complete" : ready ? (hasPlan ? "Ready" : "Needs TOC") : "Compose needed",
      completed ? 100 : hasPlan ? 100 : 0,
      ready ? (hasPlan ? "Compile plan is ready." : "Plan the table of contents before compiling.") : "Finalize Compose terms before compiling."
    );
  } catch (error) {
    setPublishStatus("failed", "Load failed", 0, error.message);
  }
  await loadPublishVisuals();
  renderPublish();
}

function renderPublish() {
  const payload = state.publish.payload;
  const ready = Boolean(payload?.ready);
  const plan = payload?.plan?.payload;
  const sections = plan?.sections || [];
  const tocGroups = groupPublishTocSections(sections);
  const statePayload = payload?.state || {};
  const completed = statePayload.completed_sections || [];
  const working = Boolean(state.publish.jobTimer);
  $("planPublishPaper").disabled = !ready || working;
  $("compilePublishPaper").disabled = !ready || working;
  $("publishSectionCount").textContent = `${sections.length.toLocaleString()} sections`;
  $("publishCompletedCount").textContent = `${completed.length.toLocaleString()} complete`;
  $("publishPaperPath").textContent = payload?.paper?.exists ? payload.paper.path : "-";
  $("publishTocEmpty").hidden = sections.length > 0;
  $("publishTocEmpty").textContent = ready ? "No compile plan yet." : "Finalize Compose terms first.";
  $("publishTocList").innerHTML = tocGroups.map(renderPublishTocGroup).join("");
  $("publishSectionList").innerHTML = completed.length
    ? completed.map(renderPublishedSection).join("")
    : `<div class="compose-empty">${ready ? "No sections generated yet." : "Finalize Compose terms first."}</div>`;
  renderPublishVisuals();
}

function renderPublishTocGroup(group, groupIndex) {
  const prompt = state.publish.customPromptGroups[group.key] || "";
  const children = group.sections.map((section, childIndex) => {
    const childPath = (section.source_path || []).slice(1).join(" > ");
    return `
      <li>
        <strong>${groupIndex + 1}.${childIndex + 1} ${escapeHtml(section.title || "")}</strong>
        ${childPath ? `<small>${escapeHtml(childPath)}</small>` : ""}
      </li>
    `;
  }).join("");
  return `
    <article class="publish-toc-item publish-toc-group">
      <div>
        <strong>${groupIndex + 1}. ${escapeHtml(group.title || "")}</strong>
        <small>${group.sections.length.toLocaleString()} ${group.sections.length === 1 ? "section" : "subtopics"}</small>
      </div>
      <textarea data-publish-prompt-group="${escapeHtml(group.key)}" rows="2" placeholder="Optional custom instruction for this section">${escapeHtml(prompt)}</textarea>
      <ol class="publish-toc-children">${children}</ol>
    </article>
  `;
}

function groupPublishTocSections(sections) {
  const groups = [];
  const byKey = {};
  (sections || []).forEach((section, index) => {
    const title = publishLevelOneTitle(section, index);
    const key = publishLevelOneKey(title, groups.length);
    if (!byKey[key]) {
      byKey[key] = { key, title, sections: [] };
      groups.push(byKey[key]);
    }
    byKey[key].sections.push(section);
  });
  return groups;
}

function publishLevelOneTitle(section, index) {
  const path = (section?.source_path || []).map((item) => String(item || "").trim()).filter(Boolean);
  return path[0] || section?.title || `Section ${index + 1}`;
}

function publishLevelOneKey(title, fallbackIndex) {
  return normalizeClientLabel(title) || `group-${fallbackIndex + 1}`;
}

function derivePublishPromptGroups(sections, leafPrompts) {
  const groups = groupPublishTocSections(sections);
  const out = {};
  groups.forEach((group) => {
    const values = [];
    group.sections.forEach((section) => {
      const value = String(leafPrompts?.[section.source_topic_id] || "").trim();
      if (value && !values.includes(value)) values.push(value);
    });
    if (values.length) out[group.key] = values[0];
  });
  return out;
}

function expandPublishCustomPrompts(sections, groupPrompts) {
  const out = {};
  groupPublishTocSections(sections).forEach((group) => {
    const value = String(groupPrompts?.[group.key] || "").trim();
    if (!value) return;
    group.sections.forEach((section) => {
      if (section.source_topic_id) out[section.source_topic_id] = value;
    });
  });
  return out;
}

function renderPublishedSection(section) {
  return `
    <article class="publish-section-row">
      <div>
        <strong>${escapeHtml(section.title || "")}</strong>
        <small>${escapeHtml(compactSectionPath(section.path || section.name || ""))}</small>
      </div>
      <em>${(section.chunk_count || 0).toLocaleString()} chunks</em>
    </article>
  `;
}

function compactSectionPath(path) {
  const text = String(path || "").trim();
  if (!text) return "";
  const normalized = text.replace(/\\/g, "/");
  const marker = "/atlas/publish/sections/";
  const markerIndex = normalized.indexOf(marker);
  if (markerIndex >= 0) return `/${normalized.slice(markerIndex + marker.length)}`;
  const fileName = normalized.split("/").filter(Boolean).pop() || normalized;
  return fileName.startsWith("/") ? fileName : `/${fileName}`;
}

async function loadPublishVisuals() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) return;
  try {
    state.publish.visuals = await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/publish/visuals`);
    state.publish.visualSelections = cloneJson(state.publish.visuals.selections?.payload?.selections) || {};
    state.publish.visualDirty = false;
    setPublishVisualStatus("completed", state.publish.visuals.candidates?.exists ? "Ready" : "Not built");
  } catch (error) {
    state.publish.visuals = null;
    setPublishVisualStatus("failed", "Load failed");
    $("publishVisualEmpty").textContent = error.message;
  }
  renderPublishVisuals();
}

function renderPublishVisuals() {
  const visuals = state.publish.visuals;
  const candidates = visuals?.candidates?.items || [];
  const sections = state.publish.payload?.plan?.payload?.sections || [];
  const selectedSection = state.publish.visualSectionId || "all";
  const validSection = selectedSection === "all" || sections.some((section) => section.section_id === selectedSection);
  if (!validSection) state.publish.visualSectionId = "all";
  $("publishVisualSectionFilter").innerHTML = [
    `<option value="all">All sections</option>`,
    ...sections.map((section) => `<option value="${escapeHtml(section.section_id || "")}">${escapeHtml(section.title || "")}</option>`)
  ].join("");
  $("publishVisualSectionFilter").value = state.publish.visualSectionId;
  $("publishVisualTextFilter").value = state.publish.visualExtractionId || "all";
  $("buildPublishVisuals").disabled = !state.atlas.selectedCorpusId;
  $("savePublishVisuals").disabled = !state.atlas.selectedCorpusId || !state.publish.visualDirty;
  const summary = visuals?.summary || {};
  const summaryText = candidates.length
    ? `${(summary.candidate_count || candidates.length).toLocaleString()} candidates, ${(summary.add_count || 0).toLocaleString()} added`
    : visuals?.candidates?.exists
      ? "No candidates"
      : "Not built";
  $("publishVisualSummary").textContent = state.publish.visualDirty ? `${summaryText}, unsaved` : summaryText;
  const filtered = candidates
    .filter((candidate) => visualMatchesSection(candidate, state.publish.visualSectionId))
    .filter((candidate) => visualMatchesExtraction(candidate, state.publish.visualExtractionId));
  $("publishVisualEmpty").hidden = filtered.length > 0;
  $("publishVisualEmpty").textContent = state.publish.visualExtractionId === "table_text_extracted"
    ? "Table-extracted images are hidden because their table text is already in the corpus."
    : candidates.length
    ? "No image candidates match these filters."
    : state.atlas.selectedCorpusId
      ? "Find images to review visual evidence."
      : "Select a corpus in Map first.";
  $("publishVisualList").innerHTML = filtered.map(renderPublishVisualCard).join("");
}

function visualMatchesSection(candidate, sectionId) {
  if (!sectionId || sectionId === "all") return true;
  if (candidate.primary_section_id === sectionId) return true;
  return (candidate.section_matches || []).some((match) => match.section_id === sectionId);
}

function visualMatchesExtraction(candidate, extractionId) {
  if (!extractionId || extractionId === "all") return true;
  return candidate.extraction_status === extractionId;
}

function renderPublishVisualCard(candidate) {
  const selection = state.publish.visualSelections[candidate.candidate_id] || candidate.selection || {};
  const status = selection.status || "none";
  const match = state.publish.visualSectionId !== "all"
    ? (candidate.section_matches || []).find((item) => item.section_id === state.publish.visualSectionId)
    : (candidate.section_matches || [])[0];
  const metaLine = visualMetaLine(candidate, match);
  return `
    <article class="publish-visual-card" data-visual-id="${escapeHtml(candidate.candidate_id || "")}">
      <button type="button" class="visual-thumb" data-visual-preview="${escapeHtml(candidate.candidate_id || "")}" ${candidate.image_url ? "" : "disabled"}>
        ${candidate.image_url ? `<img src="${escapeHtml(candidate.image_url)}" alt="${escapeHtml(candidate.alt_text || candidate.caption || "Visual evidence")}">` : `<span class="visual-missing">${candidate.missing ? "Missing asset" : "No preview"}</span>`}
      </button>
      <div class="visual-body">
        <div class="visual-badges">
          <span class="visual-badge ${escapeHtml(candidate.extraction_status || "no_extracted_text")}">${escapeHtml(candidate.extraction_label || "No extracted text found")}</span>
        </div>
        <small>${escapeHtml(metaLine)}</small>
        <div class="visual-actions">
          ${visualActionButton("add", "Add", status)}
          ${visualActionButton("maybe", "Maybe", status)}
          ${visualActionButton("reject", "Reject", status)}
        </div>
      </div>
    </article>
  `;
}

function visualMetaLine(candidate, match = null) {
  const parts = [candidate.publisher || "unknown"];
  if (candidate.width && candidate.height) {
    parts.push(`${candidate.width}×${candidate.height}`);
  } else if (candidate.is_remote) {
    parts.push("external");
    parts.push("size unknown");
  } else {
    parts.push("size unknown");
  }
  if (candidate.file_size) parts.push(formatBytes(candidate.file_size));
  const score = match?.score || candidate.visual_score;
  if (score) parts.push(`score ${score}`);
  return parts.join(" · ");
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function visualActionButton(status, label, activeStatus) {
  const active = status === activeStatus ? "active" : "";
  return `<button type="button" class="${active}" data-visual-status="${status}">${label}</button>`;
}

function onPublishVisualAction(event) {
  const preview = event.target.closest("[data-visual-preview]");
  if (preview) {
    openImagePeek(preview.dataset.visualPreview);
    return;
  }
  const button = event.target.closest("[data-visual-status]");
  const card = event.target.closest("[data-visual-id]");
  if (!button || !card) return;
  const candidateId = card.dataset.visualId;
  const status = button.dataset.visualStatus;
  const current = state.publish.visualSelections[candidateId] || { candidate_id: candidateId };
  state.publish.visualSelections[candidateId] = {
    ...current,
    candidate_id: candidateId,
    status,
    section_id: state.publish.visualSectionId === "all" ? current.section_id || "" : state.publish.visualSectionId
  };
  state.publish.visualDirty = true;
  setPublishVisualStatus("idle", "Unsaved");
  renderPublishVisuals();
}

function openImagePeek(candidateId) {
  const candidate = (state.publish.visuals?.candidates?.items || []).find((item) => item.candidate_id === candidateId);
  if (!candidate?.image_url) return;
  const match = state.publish.visualSectionId !== "all"
    ? (candidate.section_matches || []).find((item) => item.section_id === state.publish.visualSectionId)
    : (candidate.section_matches || [])[0];
  $("imagePeekImage").src = candidate.image_url;
  $("imagePeekImage").alt = candidate.alt_text || candidate.caption || "Visual evidence";
  $("imagePeekMeta").textContent = visualMetaLine(candidate, match);
  $("imagePeek").hidden = false;
  $("closeImagePeek").focus();
}

function closeImagePeek() {
  $("imagePeek").hidden = true;
  $("imagePeekImage").removeAttribute("src");
  $("imagePeekImage").alt = "";
  $("imagePeekMeta").textContent = "";
}

async function buildPublishVisuals() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) return;
  try {
    setPublishVisualStatus("running", "Finding");
    state.publish.visuals = await fetchJson("/api/publish/visuals/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId })
    });
    state.publish.visualSelections = cloneJson(state.publish.visuals.selections?.payload?.selections) || {};
    state.publish.visualDirty = false;
    setPublishVisualStatus("completed", "Ready");
  } catch (error) {
    setPublishVisualStatus("failed", "Build failed");
    $("publishVisualEmpty").hidden = false;
    $("publishVisualEmpty").textContent = error.message;
  }
  renderPublishVisuals();
}

async function savePublishVisuals() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) return;
  try {
    setPublishVisualStatus("running", "Saving");
    state.publish.visuals = await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/publish/visuals/selections`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selections: state.publish.visualSelections })
    });
    state.publish.visualSelections = cloneJson(state.publish.visuals.selections?.payload?.selections) || {};
    state.publish.visualDirty = false;
    setPublishVisualStatus("completed", "Saved");
  } catch (error) {
    setPublishVisualStatus("failed", "Save failed");
    $("publishVisualEmpty").hidden = false;
    $("publishVisualEmpty").textContent = error.message;
  }
  renderPublishVisuals();
}

function setPublishVisualStatus(stateName, label) {
  $("publishVisualStatus").textContent = label;
  $("publishVisualStatus").className = `state ${stateName}`;
}

function toggleCollapse(buttonId, bodyId) {
  const button = $(buttonId);
  const body = $(bodyId);
  const expanded = button.getAttribute("aria-expanded") === "true";
  button.setAttribute("aria-expanded", expanded ? "false" : "true");
  body.hidden = expanded;
}

function onPublishPromptInput(event) {
  const groupInput = event.target.closest("[data-publish-prompt-group]");
  if (groupInput) {
    const groupKey = groupInput.dataset.publishPromptGroup;
    const value = groupInput.value.trim();
    if (value) {
      state.publish.customPromptGroups[groupKey] = value;
    } else {
      delete state.publish.customPromptGroups[groupKey];
    }
    const sections = state.publish.payload?.plan?.payload?.sections || [];
    state.publish.customPrompts = expandPublishCustomPrompts(sections, state.publish.customPromptGroups);
    return;
  }
  const input = event.target.closest("[data-publish-prompt]");
  if (!input) return;
  const topicId = input.dataset.publishPrompt;
  const value = input.value.trim();
  if (value) {
    state.publish.customPrompts[topicId] = value;
  } else {
    delete state.publish.customPrompts[topicId];
  }
}

async function planPublishPaper() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) return;
  try {
    setPublishStatus("running", "Planning", 25, "Arranging sections into a paper flow.");
    state.publish.payload = await fetchJson("/api/publish/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId, custom_prompts: state.publish.customPrompts })
    });
    await loadPublishVisuals();
    setPublishStatus("completed", "Ready", 100, "Compile plan is ready.");
  } catch (error) {
    setPublishStatus("failed", "Plan failed", 0, error.message);
  }
  renderPublish();
}

async function compilePublishPaper() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) return;
  try {
    const job = await fetchJson("/api/publish/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        corpus_id: corpusId,
        custom_prompts: state.publish.customPrompts,
        force_plan: $("forcePublishPlan").checked
      })
    });
    state.publish.jobId = job.job_id;
    renderPublishJob(job);
    if (state.publish.jobTimer) window.clearInterval(state.publish.jobTimer);
    state.publish.jobTimer = window.setInterval(pollPublishJob, 1000);
  } catch (error) {
    setPublishStatus("failed", "Compile failed", 0, error.message);
  }
}

async function pollPublishJob() {
  if (!state.publish.jobId) return;
  try {
    const job = await fetchJson(`/api/publish/jobs/${encodeURIComponent(state.publish.jobId)}`);
    renderPublishJob(job);
    if (job.state === "completed" || job.state === "failed") {
      window.clearInterval(state.publish.jobTimer);
      state.publish.jobTimer = null;
      await loadPublishForSelected();
      if (state.activeTab === "pulbish") await loadPublishReportForSelected();
    }
  } catch (error) {
    window.clearInterval(state.publish.jobTimer);
    state.publish.jobTimer = null;
    setPublishStatus("failed", "Poll failed", 0, error.message);
  }
}

function renderPublishJob(job) {
  setPublishStatus(job.state || "idle", titleCase(job.state || "idle"), job.progress || 0, job.error || job.stage || "Working");
  const counts = job.counts || {};
  if (counts.section_count) $("publishSectionCount").textContent = `${counts.section_count.toLocaleString()} sections`;
  if (counts.completed_sections) $("publishCompletedCount").textContent = `${counts.completed_sections.toLocaleString()} complete`;
}

function setPublishStatus(stateName, label, progress, stage) {
  $("publishStatus").textContent = label;
  $("publishStatus").className = `state ${stateName}`;
  $("publishProgressBar").style.width = `${progress || 0}%`;
  $("publishStage").textContent = stage || "";
}

function bindPublishReport() {
  $("refreshReportPreview").addEventListener("click", loadReportPreview);
  $("publishReportButton").addEventListener("click", exportPublishReport);
  $("reportTemplateSelect").addEventListener("change", () => {
    state.publishReport.templateId = $("reportTemplateSelect").value || "mckinsey";
    renderPublishReport();
    scheduleReportPreview();
  });
  $("reportOutputDir").addEventListener("input", () => {
    state.publishReport.outputDir = $("reportOutputDir").value.trim();
  });
  document.querySelectorAll("[data-report-meta]").forEach((input) => {
    input.addEventListener("input", () => {
      state.publishReport.metadata[input.dataset.reportMeta] = input.value;
      scheduleReportPreview();
    });
  });
  document.querySelectorAll("[data-report-format]").forEach((input) => {
    input.addEventListener("change", () => {
      state.publishReport.formats[input.dataset.reportFormat] = input.checked;
      renderPublishReport();
    });
  });
}

async function loadPublishReportForSelected() {
  const corpusId = state.atlas.selectedCorpusId;
  const previousCorpusId = state.publishReport.corpusId;
  state.publishReport.previewRequestId += 1;
  stopReportPreviewProgressTimer();
  state.publishReport.payload = null;
  state.publishReport.preview = null;
  state.publishReport.previewLoading = false;
  state.publishReport.previewProgress = 0;
  state.publishReport.previewLoadingStartedAt = 0;
  state.publishReport.error = "";
  state.publishReport.corpusId = corpusId || null;
  if (!corpusId) {
    $("reportCorpus").textContent = "Select a corpus in Map first.";
    setReportStatus("idle", "Idle");
    renderPublishReport();
    return;
  }
  const corpus = state.atlas.corpora.find((row) => row.id === corpusId);
  $("reportCorpus").textContent = corpus ? corpus.name : corpusId;
  setReportStatus("idle", "Loading");
  try {
    const payload = await fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/publish/report`);
    state.publishReport.payload = payload;
    state.publishReport.templateId = state.publishReport.templateId || payload.default_template_id || "mckinsey";
    if (!state.publishReport.outputDir || previousCorpusId !== corpusId) {
      state.publishReport.outputDir = payload.default_output_dir || "";
    }
    if (previousCorpusId !== corpusId || !Object.keys(state.publishReport.metadata || {}).length) {
      state.publishReport.metadata = { ...(payload.metadata_defaults || {}) };
    } else {
      state.publishReport.metadata = { ...(payload.metadata_defaults || {}), ...(state.publishReport.metadata || {}) };
    }
    setReportStatus(payload.ready ? "idle" : "failed", payload.ready ? "Ready" : "Compile needed");
  } catch (error) {
    state.publishReport.error = error.message;
    setReportStatus("failed", "Load failed");
    $("reportWarnings").innerHTML = renderWarningList([error.message]);
  }
  renderPublishReport();
  if (state.publishReport.payload?.ready) await loadReportPreview();
}

function renderPublishReport() {
  const payload = state.publishReport.payload;
  const ready = Boolean(payload?.ready);
  const templates = payload?.templates || [];
  const templateId = state.publishReport.templateId || payload?.default_template_id || "mckinsey";
  const selectedTemplate = templates.find((template) => template.id === templateId) || templates[0] || {};
  $("reportTemplateSelect").innerHTML = templates.map((template) => `<option value="${escapeHtml(template.id)}">${escapeHtml(template.name)}</option>`).join("");
  if (templates.length) $("reportTemplateSelect").value = selectedTemplate.id || templateId;
  $("reportTemplateSelect").disabled = !templates.length || !state.atlas.selectedCorpusId;
  $("reportTemplateDescription").textContent = selectedTemplate.description || "";
  $("reportOutputDir").value = state.publishReport.outputDir || payload?.default_output_dir || "";
  $("reportOutputDir").disabled = !state.atlas.selectedCorpusId;
  document.querySelectorAll("[data-report-meta]").forEach((input) => {
    const key = input.dataset.reportMeta;
    input.value = state.publishReport.metadata?.[key] || payload?.metadata_defaults?.[key] || "";
    input.disabled = !state.atlas.selectedCorpusId;
  });
  $("reportPaperPath").textContent = payload?.paper?.exists ? payload.paper.path : "-";
  $("refreshReportPreview").disabled = !ready || state.publishReport.previewLoading;
  $("publishReportButton").disabled = !ready || selectedReportFormats().length === 0;
  document.querySelectorAll("[data-report-format]").forEach((input) => {
    const fmt = input.dataset.reportFormat;
    input.checked = Boolean(state.publishReport.formats[fmt]);
    input.disabled = !state.atlas.selectedCorpusId;
  });
  $("reportWarnings").innerHTML = renderWarningList([
    state.publishReport.error,
    ...(payload?.warnings || []),
    ...(state.publishReport.preview?.warnings || [])
  ]);
  renderReportOutputs(payload?.manifest || null);
  const hasPreview = Boolean(
    state.publishReport.preview?.preview_page_urls?.length ||
    state.publishReport.preview?.preview_url ||
    state.publishReport.preview?.html
  );
  const isLoading = Boolean(state.publishReport.previewLoading);
  $("reportPreviewEmpty").hidden = isLoading || hasPreview;
  $("reportPreviewLoading").hidden = !isLoading;
  $("reportPreviewPages").hidden = isLoading || !state.publishReport.preview?.preview_page_urls?.length;
  $("reportPreviewFrame").hidden = isLoading || !hasPreview || Boolean(state.publishReport.preview?.preview_page_urls?.length);
  updateReportPreviewLoadingView();
  $("reportPreviewPath").textContent =
    (isLoading ? `Rendering ${selectedTemplate.name || "template"} preview...` : "") ||
    state.publishReport.preview?.pdf_preview_path ||
    state.publishReport.preview?.html_preview_path ||
    state.publishReport.preview?.report_path ||
    (ready ? "Preview not loaded." : "Compile paper before publishing.");
}

async function loadReportPreview(requestId = null) {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId || !state.publishReport.payload?.ready) return;
  const activeRequestId = requestId || beginReportPreviewLoading();
  try {
    state.publishReport.error = "";
    setReportStatus("running", "Previewing");
    const preview = await fetchJson("/api/publish/report/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId, template_id: state.publishReport.templateId, metadata: reportMetadataPayload() })
    });
    if (activeRequestId !== state.publishReport.previewRequestId) return;
    stopReportPreviewLoading(true);
    state.publishReport.preview = preview;
    const frame = $("reportPreviewFrame");
    const pages = $("reportPreviewPages");
    if (preview.preview_page_urls?.length) {
      frame.removeAttribute("src");
      frame.srcdoc = "";
      pages.innerHTML = preview.preview_page_urls
        .map((url, index) => `<img src="${escapeHtml(url)}" alt="Preview page ${index + 1}" loading="lazy">`)
        .join("");
    } else if (preview.preview_url) {
      pages.innerHTML = "";
      frame.removeAttribute("sandbox");
      frame.srcdoc = "";
      frame.src = preview.preview_url;
    } else {
      pages.innerHTML = "";
      frame.setAttribute("sandbox", "");
      frame.removeAttribute("src");
      frame.srcdoc = preview.html || "";
    }
    setReportStatus("completed", "Preview ready");
  } catch (error) {
    if (activeRequestId !== state.publishReport.previewRequestId) return;
    stopReportPreviewLoading(false);
    state.publishReport.preview = null;
    state.publishReport.error = error.message;
    $("reportPreviewFrame").removeAttribute("src");
    $("reportPreviewFrame").srcdoc = "";
    $("reportPreviewPages").innerHTML = "";
    setReportStatus("failed", "Preview failed");
    $("reportWarnings").innerHTML = renderWarningList([error.message]);
  }
  renderPublishReport();
}

function beginReportPreviewLoading() {
  stopReportPreviewProgressTimer();
  const requestId = state.publishReport.previewRequestId + 1;
  state.publishReport.previewRequestId = requestId;
  state.publishReport.preview = null;
  state.publishReport.previewLoading = true;
  state.publishReport.previewProgress = 4;
  state.publishReport.previewLoadingStartedAt = Date.now();
  $("reportPreviewFrame").removeAttribute("src");
  $("reportPreviewFrame").srcdoc = "";
  $("reportPreviewPages").innerHTML = "";
  renderPublishReport();
  state.publishReport.previewProgressTimer = setInterval(() => {
    if (!state.publishReport.previewLoading || state.publishReport.previewRequestId !== requestId) return;
    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - state.publishReport.previewLoadingStartedAt) / 1000));
    state.publishReport.previewProgress = Math.min(95, Math.max(state.publishReport.previewProgress, 6 + Math.floor(elapsedSeconds * 1.5)));
    updateReportPreviewLoadingView();
  }, 1000);
  return requestId;
}

function stopReportPreviewLoading(complete) {
  stopReportPreviewProgressTimer();
  state.publishReport.previewProgress = complete ? 100 : 0;
  state.publishReport.previewLoading = false;
  state.publishReport.previewLoadingStartedAt = 0;
  updateReportPreviewLoadingView();
}

function stopReportPreviewProgressTimer() {
  if (state.publishReport.previewProgressTimer) {
    clearInterval(state.publishReport.previewProgressTimer);
    state.publishReport.previewProgressTimer = null;
  }
}

function updateReportPreviewLoadingView() {
  const progress = Math.max(0, Math.min(100, Math.round(state.publishReport.previewProgress || 0)));
  $("reportPreviewLoadingPercent").textContent = `${progress}%`;
  $("reportPreviewLoadingBar").style.width = `${progress}%`;
  const selected = (state.publishReport.payload?.templates || []).find((template) => template.id === state.publishReport.templateId);
  $("reportPreviewLoadingTitle").textContent = `Loading ${selected?.name || "template"} preview...`;
  const startedAt = state.publishReport.previewLoadingStartedAt;
  const elapsed = startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0;
  $("reportPreviewLoadingDetail").textContent = `Rendering the selected template into PDF preview pages. ${elapsed}s elapsed.`;
}

function clearReportPreviewDisplay() {
  stopReportPreviewProgressTimer();
  state.publishReport.previewRequestId += 1;
  state.publishReport.previewLoading = false;
  state.publishReport.previewProgress = 0;
  state.publishReport.previewLoadingStartedAt = 0;
  state.publishReport.preview = null;
  $("reportPreviewEmpty").hidden = false;
  $("reportPreviewLoading").hidden = true;
  $("reportPreviewPages").hidden = true;
  $("reportPreviewFrame").hidden = true;
  $("reportPreviewFrame").removeAttribute("src");
  $("reportPreviewFrame").srcdoc = "";
  $("reportPreviewPages").innerHTML = "";
}

async function exportPublishReport() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId || !state.publishReport.payload?.ready) return;
  const outputDir = $("reportOutputDir").value.trim();
  const formats = selectedReportFormats();
  try {
    state.publishReport.error = "";
    setReportStatus("running", "Publishing");
    const manifest = await fetchJson("/api/publish/report/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        corpus_id: corpusId,
        template_id: state.publishReport.templateId,
        output_dir: outputDir,
        formats,
        metadata: reportMetadataPayload()
      })
    });
    state.publishReport.payload = {
      ...(state.publishReport.payload || {}),
      manifest
    };
    state.publishReport.outputDir = outputDir;
    setReportStatus("completed", "Published");
  } catch (error) {
    state.publishReport.error = error.message;
    setReportStatus("failed", "Publish failed");
    $("reportWarnings").innerHTML = renderWarningList([error.message]);
  }
  renderPublishReport();
}

function scheduleReportPreview() {
  clearTimeout(state.publishReport.previewTimer);
  if (!state.publishReport.payload?.ready || !state.atlas.selectedCorpusId) return;
  const requestId = beginReportPreviewLoading();
  state.publishReport.previewTimer = setTimeout(() => loadReportPreview(requestId), 450);
}

function reportMetadataPayload() {
  const metadata = { ...(state.publishReport.payload?.metadata_defaults || {}), ...(state.publishReport.metadata || {}) };
  document.querySelectorAll("[data-report-meta]").forEach((input) => {
    metadata[input.dataset.reportMeta] = input.value;
  });
  state.publishReport.metadata = metadata;
  return metadata;
}

function selectedReportFormats() {
  return Object.entries(state.publishReport.formats)
    .filter(([, selected]) => selected)
    .map(([format]) => format);
}

function renderWarningList(warnings) {
  const rows = Array.from(new Set((warnings || []).filter(Boolean)));
  if (!rows.length) return "";
  return rows.map((warning) => `<div>${escapeHtml(warning)}</div>`).join("");
}

function renderReportOutputs(manifest) {
  const outputs = manifest?.outputs || [];
  if (!outputs.length) {
    $("reportOutputs").innerHTML = "";
    return;
  }
  $("reportOutputs").innerHTML = [
    `<strong>Outputs</strong>`,
    ...outputs.map((item) => `<p><span>${escapeHtml((item.format || "").toUpperCase())}</span>${escapeHtml(item.path || "")}</p>`)
  ].join("");
}

function setReportStatus(stateName, label) {
  $("reportStatus").textContent = label;
  $("reportStatus").className = `state ${stateName}`;
}

function cloneJson(value) {
  return value ? JSON.parse(JSON.stringify(value)) : null;
}

function normalizeClientLabel(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function bindAtlas() {
  $("refreshCorpora").addEventListener("click", () => refreshCorpora());
  $("postprocessCorpus").addEventListener("click", postprocessSelectedCorpus);
  $("buildAtlas").addEventListener("click", buildAtlas);
  $("corpusSelect").addEventListener("change", () => {
    state.atlas.selectedCorpusId = $("corpusSelect").value;
    loadAtlasForSelected();
    if (state.activeTab === "compose") loadComposeForSelected();
    if (state.activeTab === "publish") loadPublishForSelected();
  });
  $("atlasCanvas").addEventListener("click", onAtlasClick);
  $("atlasCanvas").addEventListener("mousemove", onAtlasHover);
  $("atlasCanvas").addEventListener("mouseleave", () => {
    state.atlas.hoverPointId = null;
    $("atlasHover").hidden = true;
    drawAtlas();
  });
  window.addEventListener("resize", drawAtlas);
}

async function initAtlas() {
  await Promise.all([refreshAtlasDependencies(), refreshPostprocessDependencies(), refreshCorpora()]);
}

async function refreshAtlasDependencies() {
  try {
    state.atlas.dependency = await fetchJson("/api/atlas/dependencies");
    renderAtlasDependency();
  } catch (error) {
    $("atlasDependency").hidden = false;
    $("atlasDependency").textContent = error.message;
  }
}

async function refreshPostprocessDependencies() {
  try {
    state.atlas.postprocessDependency = await fetchJson("/api/postprocess/dependencies");
    renderAtlasDependency();
  } catch (error) {
    $("atlasDependency").hidden = false;
    $("atlasDependency").textContent = error.message;
  }
}

async function refreshCorpora(preferredId = null) {
  const payload = await fetchJson("/api/corpora");
  state.atlas.corpora = payload.corpora || [];
  state.atlas.latestCorpusId = payload.latest_corpus_id || null;
  const selected = preferredId || state.atlas.selectedCorpusId || state.atlas.latestCorpusId || state.atlas.corpora[0]?.id || "";
  state.atlas.selectedCorpusId = selected;
  renderCorpusSelect();
  if (selected) {
    await loadAtlasForSelected();
  } else {
    setAtlasStatus("idle", "No corpus");
    $("atlasStage").textContent = "No corpus folders found under research_runs.";
    $("atlasEmpty").textContent = "No corpus folders found.";
    $("atlasProgressBar").style.width = "0%";
    drawAtlas();
  }
}

function renderCorpusSelect() {
  const select = $("corpusSelect");
  select.innerHTML = state.atlas.corpora.length
    ? state.atlas.corpora
        .map((corpus) => {
          const suffix = `${corpus.markdown_count || 0} md / ${corpus.source_count || 0} sources`;
          return `<option value="${escapeHtml(corpus.id)}">${escapeHtml(corpus.name)} (${escapeHtml(suffix)})</option>`;
        })
        .join("")
    : `<option value="">No corpora found</option>`;
  select.value = state.atlas.selectedCorpusId || "";
}

function renderAtlasDependency() {
  const status = state.atlas.dependency;
  if (!status) return;
  const missingPython = Object.entries(status.python || {})
    .filter(([, ok]) => !ok)
    .map(([name]) => name);
  const missingNode = Object.entries(status.node || {})
    .filter(([, ok]) => !ok)
    .map(([name]) => name);
  const postprocessStatus = state.atlas.postprocessDependency;
  const missingPostprocess = postprocessStatus?.ready
    ? []
    : Object.entries(postprocessStatus?.python || {})
        .filter(([, ok]) => !ok)
        .map(([name]) => name);
  const dependency = $("atlasDependency");
  if (!missingPython.length && !missingNode.length && !missingPostprocess.length) {
    dependency.hidden = true;
    dependency.textContent = "";
    return;
  }
  const parts = [];
  if (missingPython.length) {
    parts.push(`Missing Python packages for local embeddings: ${missingPython.join(", ")}.`);
  }
  if (missingNode.length) {
    parts.push(`Missing npm package for the official atlas component: ${missingNode.join(", ")}. The local canvas map remains available until npm install succeeds.`);
  }
  if (missingPostprocess.length) {
    parts.push(`Missing PDF post-processing packages: ${missingPostprocess.join(", ")}.`);
  }
  parts.push(`Setup: ${(status.install_commands || []).join(" | ")}`);
  if (postprocessStatus?.install_commands?.length) {
    parts.push(`Post-process setup: ${postprocessStatus.install_commands.join(" | ")}`);
  }
  dependency.textContent = parts.join(" ");
  dependency.hidden = false;
}

async function loadAtlasForSelected() {
  const corpusId = state.atlas.selectedCorpusId;
  state.atlas.points = [];
  state.atlas.selections = {};
  state.atlas.selectedChunkId = null;
  state.atlas.selectedChunk = null;
  renderInspector();
  if (!corpusId) {
    drawAtlas();
    return;
  }
  $("atlasEmpty").textContent = "Build an atlas to visualize Markdown chunks.";
  setAtlasStatus("idle", "Loading");
  $("atlasStage").textContent = "Loading atlas artifacts.";
  try {
    const [pointsPayload, selectionsPayload] = await Promise.all([
      fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/points`),
      fetchJson(`/api/atlas/${encodeURIComponent(corpusId)}/selections`)
    ]);
    state.atlas.points = pointsPayload.points || [];
    state.atlas.selections = selectionsPayload.selections || {};
    $("atlasCounts").textContent = `${state.atlas.points.length.toLocaleString()} chunks`;
    $("atlasStage").textContent = state.atlas.points.length ? "Atlas ready." : "No atlas points found. Build the atlas for this corpus.";
    $("atlasProgressBar").style.width = state.atlas.points.length ? "100%" : "0%";
    setAtlasStatus(state.atlas.points.length ? "completed" : "idle", state.atlas.points.length ? "Ready" : "Not built");
  } catch (error) {
    $("atlasStage").textContent = error.message;
    setAtlasStatus("failed", "Load failed");
  }
  await renderOfficialAtlasIfAvailable();
  drawAtlas();
}

async function buildAtlas() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) return;
  await refreshAtlasDependencies();
  if (state.atlas.dependency && !state.atlas.dependency.ready_for_build) {
    setAtlasStatus("failed", "Setup needed");
    $("atlasStage").textContent = "Install the Python embedding dependencies before building.";
    return;
  }
  try {
    const job = await fetchJson("/api/atlas/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId, force: $("forceAtlasBuild").checked })
    });
    state.atlas.jobId = job.job_id;
    renderAtlasJob(job);
    if (state.atlas.jobTimer) window.clearInterval(state.atlas.jobTimer);
    state.atlas.jobTimer = window.setInterval(pollAtlasJob, 1000);
  } catch (error) {
    setAtlasStatus("failed", "Build failed");
    $("atlasStage").textContent = error.message;
  }
}

async function postprocessSelectedCorpus() {
  const corpusId = state.atlas.selectedCorpusId;
  if (!corpusId) return;
  await refreshPostprocessDependencies();
  if (state.atlas.postprocessDependency && !state.atlas.postprocessDependency.ready) {
    setAtlasStatus("failed", "Setup needed");
    $("atlasStage").textContent = "Install PyMuPDF4LLM or PyMuPDF before post-processing PDF Markdown.";
    return;
  }
  try {
    const job = await fetchJson("/api/postprocess/markdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId })
    });
    state.atlas.postprocessJobId = job.job_id;
    renderPostprocessJob(job);
    if (state.atlas.postprocessTimer) window.clearInterval(state.atlas.postprocessTimer);
    state.atlas.postprocessTimer = window.setInterval(pollPostprocessJob, 1000);
  } catch (error) {
    setAtlasStatus("failed", "Post-process failed");
    $("atlasStage").textContent = error.message;
  }
}

async function pollPostprocessJob() {
  if (!state.atlas.postprocessJobId) return;
  try {
    const job = await fetchJson(`/api/postprocess/jobs/${encodeURIComponent(state.atlas.postprocessJobId)}`);
    renderPostprocessJob(job);
    if (job.state === "completed" || job.state === "failed") {
      window.clearInterval(state.atlas.postprocessTimer);
      state.atlas.postprocessTimer = null;
      if (job.state === "completed") {
        await refreshCorpora(state.atlas.selectedCorpusId);
        $("atlasStage").textContent = job.counts?.atlas_invalidated
          ? "Post-processing complete. Markdown changed; rebuild the Atlas."
          : "Post-processing complete. No Atlas rebuild needed.";
        setAtlasStatus("completed", "Post-processed");
      }
    }
  } catch (error) {
    window.clearInterval(state.atlas.postprocessTimer);
    state.atlas.postprocessTimer = null;
    setAtlasStatus("failed", "Poll failed");
    $("atlasStage").textContent = error.message;
  }
}

function renderPostprocessJob(job) {
  setAtlasStatus(job.state || "idle", titleCase(job.state || "idle"));
  $("atlasStage").textContent = job.error || job.stage || "Post-processing";
  $("atlasProgressBar").style.width = `${job.progress || 0}%`;
  const counts = job.counts || {};
  $("atlasCounts").textContent = `${counts.flagged || 0} flagged / ${counts.reconverted || 0} reconverted / ${counts.reflowed || 0} reflowed`;
}

async function pollAtlasJob() {
  if (!state.atlas.jobId) return;
  try {
    const job = await fetchJson(`/api/atlas/jobs/${encodeURIComponent(state.atlas.jobId)}`);
    renderAtlasJob(job);
    if (job.state === "completed" || job.state === "failed") {
      window.clearInterval(state.atlas.jobTimer);
      state.atlas.jobTimer = null;
      if (job.state === "completed") await loadAtlasForSelected();
    }
  } catch (error) {
    window.clearInterval(state.atlas.jobTimer);
    state.atlas.jobTimer = null;
    setAtlasStatus("failed", "Poll failed");
    $("atlasStage").textContent = error.message;
  }
}

function renderAtlasJob(job) {
  setAtlasStatus(job.state || "idle", titleCase(job.state || "idle"));
  $("atlasStage").textContent = job.error || job.stage || "Working";
  $("atlasProgressBar").style.width = `${job.progress || 0}%`;
  const counts = job.counts || {};
  const chunks = counts.chunk_count || state.atlas.points.length || 0;
  const sources = counts.source_count || 0;
  $("atlasCounts").textContent = `${chunks.toLocaleString()} chunks${sources ? ` / ${sources.toLocaleString()} sources` : ""}`;
}

function setAtlasStatus(stateName, label) {
  $("atlasStatus").textContent = label;
  $("atlasStatus").className = `state ${stateName}`;
}

async function onCorpusCompleted(corpusId) {
  state.atlas.selectedCorpusId = corpusId;
  await refreshCorpora(corpusId);
}

function onAtlasClick(event) {
  const point = nearestAtlasPoint(event, 14);
  if (point) selectChunk(point.id);
}

function onAtlasHover(event) {
  const point = nearestAtlasPoint(event, 12);
  const hover = $("atlasHover");
  if (!point) {
    state.atlas.hoverPointId = null;
    hover.hidden = true;
    drawAtlas();
    return;
  }
  state.atlas.hoverPointId = point.id;
  const rect = $("atlasCanvas").getBoundingClientRect();
  hover.style.left = `${Math.max(8, Math.min(rect.width - 345, event.offsetX + 14))}px`;
  hover.style.top = `${Math.max(8, event.offsetY + 14)}px`;
  hover.innerHTML = `<strong>${escapeHtml(point.metadata?.title || point.id)}</strong><br>${escapeHtml(point.text_preview || "")}`;
  hover.hidden = false;
  drawAtlas();
}

function nearestAtlasPoint(event, maxDistance) {
  const points = state.atlas.points;
  if (!points.length) return null;
  const rect = $("atlasCanvas").getBoundingClientRect();
  let nearest = null;
  let best = maxDistance;
  for (const point of points) {
    const [x, y] = pointToCanvas(point, rect.width, rect.height);
    const distance = Math.hypot(x - event.offsetX, y - event.offsetY);
    if (distance < best) {
      nearest = point;
      best = distance;
    }
  }
  return nearest;
}

async function selectChunk(chunkId) {
  state.atlas.selectedChunkId = chunkId;
  drawAtlas();
  try {
    state.atlas.selectedChunk = await fetchJson(`/api/atlas/${encodeURIComponent(state.atlas.selectedCorpusId)}/chunks/${encodeURIComponent(chunkId)}`);
    renderInspector();
    await renderOfficialAtlasIfAvailable();
    drawAtlas();
  } catch (error) {
    $("chunkInspector").innerHTML = `<h2>Chunk inspector</h2><p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function renderInspector() {
  const target = $("chunkInspector");
  const chunk = state.atlas.selectedChunk;
  if (!chunk) {
    target.innerHTML = `<h2>Chunk inspector</h2><p class="subtle">Click a point to inspect text, metadata, and curation state.</p>`;
    return;
  }
  const meta = chunk.metadata || {};
  const selection = state.atlas.selections[chunk.id] || { status: "none", notes: "" };
  target.innerHTML = `
    <h2 class="chunk-title">${escapeHtml(meta.title || chunk.id)}</h2>
    <p class="subtle">${escapeHtml(meta.publisher || "unknown")} ${meta.source_type ? `- ${escapeHtml(meta.source_type)}` : ""}</p>
    <div class="chunk-meta">
      <div><span>Section</span>${escapeHtml(meta.section_path || "-")}</div>
      <div><span>Words</span>${escapeHtml(meta.word_count || 0)} (${escapeHtml(meta.chunk_type || "text")})</div>
      <div><span>URL</span>${meta.url ? `<a href="${escapeHtml(meta.url)}" target="_blank" rel="noreferrer">${escapeHtml(meta.url)}</a>` : "-"}</div>
      <div><span>Markdown</span>${escapeHtml(meta.markdown_path || "-")}</div>
      ${meta.warning_flags?.length ? `<div class="warning-flags"><span>Warnings</span>${escapeHtml(meta.warning_flags.join(", "))}</div>` : ""}
    </div>
    <div class="curation-actions">
      ${curationButton("keep", "Keep", selection.status)}
      ${curationButton("reject", "Reject", selection.status, "reject")}
      ${curationButton("key_evidence", "Key Evidence", selection.status, "key")}
      ${curationButton("maybe", "Maybe", selection.status)}
    </div>
    <div class="field notes-field">
      <label for="selectionNotes">Notes</label>
      <textarea id="selectionNotes" rows="3">${escapeHtml(selection.notes || "")}</textarea>
    </div>
    <button type="button" id="clearSelection">Clear status</button>
    <div class="chunk-text">${escapeHtml(chunk.text || "")}</div>
  `;
  target.querySelectorAll("[data-curation]").forEach((button) => {
    button.addEventListener("click", () => saveSelection(button.dataset.curation));
  });
  $("clearSelection").addEventListener("click", () => saveSelection("none"));
  $("selectionNotes").addEventListener("blur", () => saveSelection(selection.status || "none"));
}

function curationButton(status, label, activeStatus, extraClass = "") {
  const active = status === activeStatus ? "active" : "";
  return `<button type="button" class="${active} ${extraClass}" data-curation="${status}">${label}</button>`;
}

async function saveSelection(status) {
  const chunkId = state.atlas.selectedChunkId;
  if (!chunkId) return;
  const notes = $("selectionNotes")?.value || "";
  const payload = { chunk_id: chunkId, status, notes };
  try {
    const result = await fetchJson(`/api/atlas/${encodeURIComponent(state.atlas.selectedCorpusId)}/selections`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    state.atlas.selections = result.selections || {};
    renderInspector();
    await renderOfficialAtlasIfAvailable();
    drawAtlas();
  } catch (error) {
    $("atlasStage").textContent = error.message;
  }
}

async function renderOfficialAtlasIfAvailable() {
  const mount = $("atlasEmbeddingMount");
  const canvas = $("atlasCanvas");
  if (!state.atlas.points.length) {
    destroyOfficialAtlas();
    mount.hidden = true;
    canvas.hidden = false;
    return false;
  }
  if (state.atlas.officialUnavailable) return false;
  try {
    if (!state.atlas.officialModule) {
      state.atlas.officialModule = await import("/static/atlas/atlas.js");
    }
    mount.hidden = false;
    canvas.hidden = true;
    state.atlas.officialView = state.atlas.officialModule.mountEmbeddingAtlas(
      mount,
      {
        points: state.atlas.points,
        selections: state.atlas.selections,
        selectedChunkId: state.atlas.selectedChunkId,
        theme: state.theme
      },
      {
        onSelect: (chunkId) => selectChunk(chunkId)
      }
    );
    $("atlasEmpty").hidden = true;
    state.atlas.officialMounted = true;
    return true;
  } catch (_) {
    state.atlas.officialUnavailable = true;
    destroyOfficialAtlas();
    mount.hidden = true;
    canvas.hidden = false;
    return false;
  }
}

function destroyOfficialAtlas() {
  if (state.atlas.officialView?.destroy) {
    state.atlas.officialView.destroy();
  }
  state.atlas.officialView = null;
  state.atlas.officialMounted = false;
}

function drawAtlas() {
  const canvas = $("atlasCanvas");
  if (!canvas) return;
  if (state.atlas.officialMounted) return;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  const width = Math.floor(rect.width * dpr);
  const height = Math.floor(rect.height * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = cssVar("--atlas-canvas-bg");
  ctx.fillRect(0, 0, rect.width, rect.height);
  drawGrid(ctx, rect.width, rect.height);
  $("atlasEmpty").hidden = state.atlas.points.length > 0;
  for (const point of state.atlas.points) {
    const [x, y] = pointToCanvas(point, rect.width, rect.height);
    const selection = state.atlas.selections[point.id]?.status || "none";
    const selected = point.id === state.atlas.selectedChunkId;
    const hovered = point.id === state.atlas.hoverPointId;
    ctx.beginPath();
    ctx.arc(x, y, selected ? 6 : hovered ? 5 : 3.8, 0, Math.PI * 2);
    ctx.fillStyle = pointColor(selection, point.metadata?.warning_flags);
    ctx.globalAlpha = selection === "reject" ? 0.42 : 0.86;
    ctx.fill();
    ctx.globalAlpha = 1;
    if (selected || hovered) {
      ctx.lineWidth = selected ? 2 : 1.5;
      ctx.strokeStyle = selected ? cssVar("--atlas-selected-stroke") : cssVar("--atlas-hover-stroke");
      ctx.stroke();
    }
  }
}

function drawGrid(ctx, width, height) {
  ctx.strokeStyle = cssVar("--atlas-grid");
  ctx.lineWidth = 1;
  for (let x = 48; x < width; x += 64) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = 48; y < height; y += 64) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
}

function pointToCanvas(point, width, height) {
  const margin = 34;
  const x = margin + ((Number(point.x) + 1) / 2) * Math.max(1, width - margin * 2);
  const y = margin + ((1 - (Number(point.y) + 1) / 2)) * Math.max(1, height - margin * 2);
  return [x, y];
}

function pointColor(selection, flags = []) {
  if (selection === "key_evidence") return cssVar("--point-key");
  if (selection === "keep") return cssVar("--point-keep");
  if (selection === "reject") return cssVar("--point-reject");
  if (selection === "maybe") return cssVar("--point-maybe");
  if (flags && flags.length) return cssVar("--point-warning");
  return cssVar("--point-default");
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

function titleCase(value) {
  return String(value).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function labelize(value) {
  return titleCase(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

init();
