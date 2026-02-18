// LineupLogic demo UI (MATCHES WORKING BACKEND main.py)
// - Left: roster (click player to drop)
// - Right: waiver recommendations (add/drop swap cards)
//
// Backend routes expected:
//   GET /health
//   GET /league/nba/teams?days=...
//   GET /league/nba/roster?team_id=...&days=...
//   GET /league/nba/recommendations/waivers?team_id=...&days=...&pool_size=...&limit=...&drop_player_id=...

const el = (id) => document.getElementById(id);

const apiStatusDot = el("apiStatusDot");
const apiStatusText = el("apiStatusText");

const teamSelect = el("teamSelect");
const daysInput = el("daysInput");
const poolInput = el("poolInput");
const limitInput = el("limitInput");

const refreshRosterBtn = el("refreshRosterBtn");
const bestSwapsBtn = el("bestSwapsBtn");

const rosterMeta = el("rosterMeta");
const rosterList = el("rosterList");
const selectedDropPill = el("selectedDropPill");

const recsMeta = el("recsMeta");
const recsList = el("recsList");

// If user accidentally opens index.html via file://, fetch("/league/...") will fail.
// This makes it still work.
const API_BASE = (() => {
  const origin = window.location.origin;
  if (!origin || origin === "null" || window.location.protocol === "file:") {
    return "http://127.0.0.1:8000";
  }
  return origin;
})();

let state = {
  teamId: null,
  teamName: null,
  days: 21,
  pool: 300,
  limit: 10,
  roster: [],
  selectedDropId: null,
  selectedDropName: null,
};

function clampNumberInput(input, min, max) {
  const v = Number(input.value);
  if (Number.isNaN(v)) return min;
  return Math.max(min, Math.min(max, v));
}

async function apiGet(path) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} — ${text}`);
  }
  return res.json();
}

function setApiStatus(ok, msg) {
  apiStatusDot.classList.remove("ok", "bad");
  apiStatusDot.classList.add(ok ? "ok" : "bad");
  apiStatusText.textContent = msg;
}

function fmt(num) {
  if (num === null || num === undefined) return "—";
  const n = Number(num);
  if (Number.isNaN(n)) return "—";
  return n.toFixed(2);
}

function renderRoster() {
  rosterList.innerHTML = "";
  if (!state.roster || state.roster.length === 0) {
    rosterList.innerHTML = `<div class="muted">No roster data yet.</div>`;
    rosterMeta.textContent = "—";
    selectedDropPill.textContent = "Drop: none";
    return;
  }

  rosterMeta.textContent = `${state.teamName} • ${state.days} day window • ${state.roster.length} active players`;

  for (const p of state.roster) {
    const pid = p.playerId;
    const isSelected = state.selectedDropId === pid;

    const row = document.createElement("div");
    row.className = "row" + (isSelected ? " selected" : "");
    row.dataset.playerId = String(pid ?? "");

    const main = document.createElement("div");
    main.className = "rowMain";

    const top = document.createElement("div");
    top.className = "rowTop";
    top.innerHTML = `
      <span class="name">${p.name ?? "Unknown"}</span>
      <span class="pos">(${p.position ?? "—"})</span>
      <span class="team">• ${p.proTeam ?? "—"}</span>
    `;

    const sub = document.createElement("div");
    sub.className = "rowSub";
    sub.textContent = `Games next ${state.days}d: ${p.games_next_n_days} • PPG used: ${fmt(p.fantasy_ppg_used)}`;

    main.appendChild(top);
    main.appendChild(sub);

    const metric = document.createElement("div");
    metric.className = "metric";
    metric.innerHTML = `
      <div class="big">${fmt(p.projected_points_next_n_days)}</div>
      <div class="small">proj pts</div>
    `;

    row.appendChild(main);
    row.appendChild(metric);

    row.addEventListener("click", async () => {
      state.selectedDropId = pid;
      state.selectedDropName = p.name ?? "Selected player";
      selectedDropPill.innerHTML = `Drop: <strong>${state.selectedDropName}</strong>`;
      renderRoster();
      await loadRecommendations(true);
    });

    rosterList.appendChild(row);
  }

  if (!state.selectedDropId) {
    selectedDropPill.textContent = "Drop: none";
  }
}

function renderRecs(data) {
  recsList.innerHTML = "";

  const count = data?.recommendations?.length ?? 0;
  const mode = state.selectedDropId ? `Dropping: ${state.selectedDropName}` : "Best overall swaps";
  recsMeta.textContent = `${state.teamName} • ${state.days} day window • ${count} recs • ${mode}`;

  if (!count) {
    recsList.innerHTML = `<div class="muted">No positive-gain recommendations found for this window.</div>`;
    return;
  }

  for (const rec of data.recommendations) {
    const add = rec.add;
    const drop = rec.drop;

    const card = document.createElement("div");
    card.className = "recCard";

    card.innerHTML = `
      <div class="recTop">
        <div>
          <div class="swapLabel">Suggested swap</div>
          <div class="recNames">
            <div class="line">
              <div class="addTag">ADD</div>
              <div class="who">${add.name} (${add.position})</div>
              <div class="meta">Team: ${add.proTeam} • Games: ${add.games_next_n_days} • Proj: ${fmt(add.projected_points_next_n_days)}</div>
            </div>

            <div class="line">
              <div class="dropTag">DROP</div>
              <div class="who">${drop.name} (${drop.position})</div>
              <div class="meta">Team: ${drop.proTeam} • Games: ${drop.games_next_n_days} • Proj: ${fmt(drop.projected_points_next_n_days)}</div>
            </div>
          </div>
        </div>

        <div class="gainBox">
          <div class="swapLabel">Expected gain</div>
          <div class="gain">${fmt(rec.expected_gain_next_n_days)}</div>
          <div class="unit">points / window</div>
        </div>
      </div>
    `;

    recsList.appendChild(card);
  }
}

async function loadTeams() {
  const data = await apiGet(`/league/nba/teams?days=${state.days}`);
  const teams = data.teams ?? [];

  teamSelect.innerHTML = "";
  for (const t of teams) {
    const opt = document.createElement("option");
    opt.value = String(t.team_id);
    opt.textContent = `${t.team_name}`;
    teamSelect.appendChild(opt);
  }

  const first = teams[0];
  if (first) {
    state.teamId = first.team_id;
    state.teamName = first.team_name;
    teamSelect.value = String(first.team_id);
  }
}

async function loadRoster() {
  if (!state.teamId) return;

  const data = await apiGet(`/league/nba/roster?team_id=${state.teamId}&days=${state.days}`);
  state.teamName = data.team ?? state.teamName;
  state.roster = data.roster ?? [];

  if (state.selectedDropId) {
    const stillThere = state.roster.some((p) => p.playerId === state.selectedDropId);
    if (!stillThere) {
      state.selectedDropId = null;
      state.selectedDropName = null;
      selectedDropPill.textContent = "Drop: none";
    }
  }

  renderRoster();
}

async function loadRecommendations(useSelectedDrop) {
  if (!state.teamId) return;

  recsMeta.textContent = "Loading recommendations…";
  recsList.innerHTML = `<div class="muted">Fetching waiver pool and scoring…</div>`;

  const params = new URLSearchParams({
    team_id: String(state.teamId),
    days: String(state.days),
    pool_size: String(state.pool),
    limit: String(state.limit),
  });

  if (useSelectedDrop && state.selectedDropId) {
    params.set("drop_player_id", String(state.selectedDropId));
  }

  const data = await apiGet(`/league/nba/recommendations/waivers?${params.toString()}`);
  renderRecs(data);
}

function syncInputsToState() {
  state.days = clampNumberInput(daysInput, 1, 60);
  state.pool = clampNumberInput(poolInput, 25, 1000);
  state.limit = clampNumberInput(limitInput, 1, 50);

  daysInput.value = state.days;
  poolInput.value = state.pool;
  limitInput.value = state.limit;
}

async function init() {
  try {
    await apiGet("/health");
    setApiStatus(true, "API OK");
  } catch (e) {
    console.error(e);
    setApiStatus(false, "API not reachable");
  }

  syncInputsToState();

  try {
    await loadTeams();
    await loadRoster();
    await loadRecommendations(false);
  } catch (e) {
    console.error(e);
    recsList.innerHTML = `<div class="muted">Error: ${String(e.message || e)}</div>`;
  }

  teamSelect.addEventListener("change", async () => {
    state.teamId = Number(teamSelect.value);

    state.selectedDropId = null;
    state.selectedDropName = null;
    selectedDropPill.textContent = "Drop: none";

    await loadRoster();
    await loadRecommendations(false);
  });

  daysInput.addEventListener("change", async () => {
    syncInputsToState();
    await loadTeams();
    await loadRoster();
    await loadRecommendations(!!state.selectedDropId);
  });

  poolInput.addEventListener("change", async () => {
    syncInputsToState();
    await loadRecommendations(!!state.selectedDropId);
  });

  limitInput.addEventListener("change", async () => {
    syncInputsToState();
    await loadRecommendations(!!state.selectedDropId);
  });

  refreshRosterBtn.addEventListener("click", async () => {
    await loadRoster();
    await loadRecommendations(!!state.selectedDropId);
  });

  bestSwapsBtn.addEventListener("click", async () => {
    state.selectedDropId = null;
    state.selectedDropName = null;
    selectedDropPill.textContent = "Drop: none";
    renderRoster();
    await loadRecommendations(false);
  });
}

init();