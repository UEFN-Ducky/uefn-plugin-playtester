/* Play Tester tab: follows runs live (push events, with a slow poll as a safety net),
   shows the frame with Jev's box, the keys and mouse of each step, Jev's answers and why. */
(function () {
  "use strict";

  const CHANNEL = "uefn-plugin-ui";
  const KEY_ROWS = [
    ["Q", "W", "E", "R"],
    ["A", "S", "D", "F"],
    ["SHIFT", "CTRL", "SPACE"],
    ["1", "2", "3", "4", "5"],
  ];
  const KEY_LABEL = { SHIFT: "Shift", CTRL: "Ctrl", SPACE: "Space" };
  const DONE = ["success", "failed", "blocked", "stopped", "timeout", "done"];
  const STATUS_BADGE = {
    running: "dk-badge--accent", stopping: "dk-badge--warn", success: "dk-badge--success",
    failed: "dk-badge--danger", blocked: "dk-badge--warn", stopped: "dk-badge--info",
    timeout: "dk-badge--warn", done: "dk-badge--success", manual: "dk-badge--info",
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (v) => (v == null ? "—" : `${Math.round(Number(v) * 100)}%`);

  function call(method, params = {}) {
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      function onMsg(ev) {
        const d = ev.data;
        if (!d || d.channel !== CHANNEL || d.id !== id) return;
        window.removeEventListener("message", onMsg);
        d.ok ? resolve(d.result) : reject(new Error(d.error || "bridge error"));
      }
      window.addEventListener("message", onMsg);
      parent.postMessage({ channel: CHANNEL, id, method, params }, "*");
    });
  }
  const rpc = (method, params = {}) => call("plugin.call", { method, params });

  const state = {
    visible: true,
    runId: "",          // run shown
    run: null,          // its doc without steps
    steps: [],          // its steps
    selected: 0,        // step number shown (0 = none)
    follow: true,       // jump to each new step
    thumbs: new Map(),  // "run:n" -> data URI
    frames: new Map(),  // "run:n" -> full frame data URI
    status: null,
    lastPoll: 0,
  };

  // ------------------------------------------------------------------ keyboard + mouse

  function buildKeys() {
    const wrap = $("keys");
    wrap.innerHTML = "";
    for (const row of KEY_ROWS) {
      const line = document.createElement("div");
      line.className = "pt-keys__row";
      for (const k of row) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "pt-key" + (k === "SPACE" ? " is-wide" : k.length > 1 ? " is-mid" : "");
        b.dataset.key = k;
        b.title = `Press ${KEY_LABEL[k] || k} for 0.3 s`;
        b.innerHTML = `${esc(KEY_LABEL[k] || k)}<small></small>`;
        b.addEventListener("click", () => nudge({ keys: k }));
        line.appendChild(b);
      }
      wrap.appendChild(line);
    }
    $("dial").addEventListener("click", (ev) => {
      const r = ev.currentTarget.getBoundingClientRect();
      nudge({ look: ev.clientX < r.left + r.width / 2 ? -30 : 30 });
    });
  }

  async function nudge(what) {
    if (state.status && state.status.active_run) {
      showErr("A run is playing: stop it before pressing keys by hand.");
      return;
    }
    try {
      const out = what.keys
        ? await rpc("press", { keys: what.keys, seconds: 0.3 })
        : await rpc("look", { dx_degrees: what.look });
      if (out && out.ok === false) showErr(out.error);
      else showErr("");
    } catch (err) {
      showErr(err.message);
    }
  }

  function showPressing(step) {
    const held = new Map();
    let turn = 0;
    let clicks = [];
    const planned = new Set();
    for (const act of (step && step.actions) || []) {
      if (act.keys) for (const k of act.keys) held.set(k, (held.get(k) || 0) + Number(act.seconds || 0));
      if (act.look) turn += Number(act.look);
      if (act.click) clicks.push(act.click);
    }
    const dry = !!(step && step.dry_run);
    const didRun = step && Array.isArray(step.done) && step.done.length > 0;
    const keptKeys = new Set();
    for (const d of (step && step.done) || []) if (d && d.keep) for (const k of d.keep.keys || []) keptKeys.add(k);
    if (dry || !didRun) for (const k of held.keys()) planned.add(k);
    for (const b of document.querySelectorAll(".pt-key")) {
      const k = b.dataset.key;
      const on = held.has(k);
      b.classList.toggle("is-on", on && !planned.has(k));
      b.classList.toggle("is-planned", on && planned.has(k));
      b.querySelector("small").textContent = on ? (keptKeys.has(k) ? "held" : `${held.get(k).toFixed(1)} s`) : "";
    }
    $("mLeft").classList.toggle("is-on", clicks.includes("left"));
    $("mRight").classList.toggle("is-on", clicks.includes("right"));
    const shown = Math.max(-90, Math.min(90, turn));
    $("dialArrow").style.setProperty("--turn", `${shown}deg`);
    $("dial").classList.toggle("is-on", !!turn);
    const label = turn ? `turn ${turn > 0 ? "right" : "left"} ${Math.abs(turn).toFixed(0)}°` : "no turn";
    $("dialLabel").textContent = dry && (turn || held.size) ? `${label} (dry run)` : label;
  }

  // ------------------------------------------------------------------ Jev answers

  const NAMES = {
    screen: "Screen", seen: "Target seen", where: "Where", near: "How close", moved: "Moved",
    path: "Path", prompt: "Use prompt", done: "Success", next: "Jev's move", stairs_seen: "Way up/down",
  };

  // ------------------------------------------------------------------ map (heading up)

  function sayRel(rel) {
    if (!rel) return "";
    const t = Math.abs(Number(rel.turn_deg) || 0).toFixed(0);
    const side = rel.side === "ahead" ? "straight ahead"
      : rel.side === "behind" ? `behind (${t}° ${rel.turn_deg > 0 ? "right" : "left"})`
      : `${t}° ${rel.side}`;
    let text = `${Number(rel.distance_m).toFixed(1)} m, ${side}`;
    if (rel.floor === "up") text += `, ${Number(rel.height_m).toFixed(1)} m up`;
    else if (rel.floor === "down") text += `, ${Math.abs(Number(rel.height_m)).toFixed(1)} m down`;
    return text;
  }

  function showMap(step) {
    const where = (step && step.where) || null;
    const pose = where && where.pose;
    const target = state.run && state.run.target_point;
    const trailEl = $("mapTrail");
    const dot = $("mapTarget");
    const line = $("mapLine");
    if (!pose) {
      $("mapMarks").innerHTML = "";
      $("mapNear").textContent = "";
      trailEl.setAttribute("points", "");
      dot.classList.add("pt-hidden");
      line.classList.add("pt-hidden");
      const on = state.status && state.status.beacon && state.status.beacon.on;
      $("mapText").textContent = on ? "Beacon on: start a run to see the map" : "No beacon: Jev steers by the image only";
      $("mapPose").textContent = target ? `target x ${target.x.toFixed(0)} y ${target.y.toFixed(0)} z ${target.z.toFixed(0)}` : "";
      return;
    }
    const yaw = (Number(pose.yaw) || 0) * Math.PI / 180;
    const fx = Math.cos(yaw), fy = Math.sin(yaw);   // forward
    const rx = -Math.sin(yaw), ry = Math.cos(yaw);  // right
    const local = (p) => {
      const dx = p.x - pose.x, dy = p.y - pose.y;
      return [dx * rx + dy * ry, -(dx * fx + dy * fy)];  // svg x = right, svg y = -ahead
    };
    const upto = state.steps.filter((s) => s.n <= step.n && s.where && s.where.pose).slice(-30).map((s) => local(s.where.pose));
    let reach = 500;  // cm shown to the ring at least
    for (const [x, y] of upto) reach = Math.max(reach, Math.hypot(x, y));
    let tl = null;
    if (target) {
      tl = local(target);
      reach = Math.max(reach, Math.min(Math.hypot(tl[0], tl[1]), 6000));
    }
    const scale = 88 / reach;
    trailEl.setAttribute("points", upto.map(([x, y]) => `${(x * scale).toFixed(1)},${(y * scale).toFixed(1)}`).join(" "));
    if (tl) {
      let [x, y] = [tl[0] * scale, tl[1] * scale];
      const r = Math.hypot(x, y);
      const edge = r > 92;
      if (edge) { x = x / r * 92; y = y / r * 92; }
      dot.setAttribute("cx", x.toFixed(1));
      dot.setAttribute("cy", y.toFixed(1));
      dot.classList.toggle("is-edge", edge);
      dot.classList.remove("pt-hidden");
      line.setAttribute("x2", x.toFixed(1));
      line.setAttribute("y2", y.toFixed(1));
      line.classList.remove("pt-hidden");
    } else {
      dot.classList.add("pt-hidden");
      line.classList.add("pt-hidden");
    }
    const marksEl = $("mapMarks");
    marksEl.innerHTML = "";
    for (const n of where.nearby || []) {
      let [x, y] = local(n);
      x *= scale; y *= scale;
      const r = Math.hypot(x, y);
      if (r > 92) { x = x / r * 92; y = y / r * 92; }
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("class", "pt-map__mark"); c.setAttribute("r", "4");
      c.setAttribute("cx", x.toFixed(1)); c.setAttribute("cy", y.toFixed(1));
      const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
      t.textContent = `${n.name} · ${Number(n.distance_m).toFixed(0)} m`;
      c.appendChild(t);
      marksEl.appendChild(c);
    }
    const rel = where.rel;
    $("mapText").textContent = rel ? `Target ${sayRel(rel)}` : "Beacon on (no target position)";
    const calib = where.calib ? ` · mouse ${where.calib.counts}/90°` : "";
    const climb = where.climb_m != null && Math.abs(where.climb_m) >= 0.2 ? ` · ${where.climb_m > 0 ? "climbing" : "dropping"} ${Math.abs(where.climb_m).toFixed(1)} m` : "";
    const stance = pose.state ? ` · ${pose.state === "ground" ? "on ground" : pose.state}` : "";
    $("mapPose").textContent = `height ${(pose.z / 100).toFixed(1)} m${climb}${stance} · facing ${pose.yaw.toFixed(0)}°${where.moved_m != null ? ` · moved ${where.moved_m} m` : ""}${calib}`;
    $("mapNear").textContent = (where.nearby || []).length
      ? "Near: " + where.nearby.map((n) => `${n.name} ${Number(n.distance_m).toFixed(0)} m ${n.side}${Math.abs(n.height_m) > 2.5 ? (n.height_m > 0 ? " up" : " down") : ""}`).join(" · ")
      : "";
  }

  function bar(value, conf) {
    const v = Math.max(0, Math.min(1, Number(value) || 0));
    return `<div class="pt-bar${v < conf ? " is-low" : ""}"><i style="--w:${(v * 100).toFixed(0)}%"></i></div>`;
  }

  function showAnswers(step) {
    const box = $("answers");
    const view = (step && step.view) || {};
    const conf = Number((state.status && state.status.settings && state.status.settings.confidence) || 0.6);
    const rows = [];
    for (const key of ["screen", "seen", "where", "near", "moved", "path", "prompt", "stairs_seen", "done", "next"]) {
      const v = view[key];
      if (v == null) continue;
      if (typeof v === "object" && v.choice) {
        const all = v.all || {};
        const chips = Object.keys(all).length ? Object.keys(all) : [v.choice];
        rows.push(`<div class="pt-ans"><span class="pt-ans__name">${esc(NAMES[key] || key)}</span>
          <div class="pt-chips">${chips.map((c) => `<span class="pt-choice${c === v.choice ? " is-pick" : ""}">${esc(c.replace(/_/g, " "))}${all[c] != null ? " " + pct(all[c]) : ""}</span>`).join("")}</div>
          <span class="pt-ans__val">${pct(v.p)}</span></div>`);
      } else {
        rows.push(`<div class="pt-ans"><span class="pt-ans__name">${esc(NAMES[key] || key)}</span>${bar(v, conf)}<span class="pt-ans__val">${pct(v)}</span></div>`);
      }
    }
    for (const [key, label] of [["box", "Box"], ["stairs_box", "Stairs box"]]) {
      const b = view[key];
      if (!b) continue;
      rows.push(`<div class="pt-ans"><span class="pt-ans__name">${label}</span><span class="dk-muted">x ${b.left.toFixed(2)}–${b.right.toFixed(2)}, y ${b.top.toFixed(2)}–${b.bottom.toFixed(2)}</span><span class="pt-ans__val">${pct(b.confidence)}</span></div>`);
    }
    if (view.geo) {
      rows.push(`<div class="pt-ans"><span class="pt-ans__name">Coordinates</span><span class="pt-ans__value">${esc(sayRel(view.geo))}</span></div>`);
    }
    box.innerHTML = rows.length ? rows.join("") : `<div class="pt-empty-small">No Jev answers for this step.</div>`;
    $("why").textContent = (step && step.why) || "—";
  }

  // ------------------------------------------------------------------ frame + overlay

  function showOverlay(step) {
    const view = (step && step.view) || {};
    const conf = Number((state.status && state.status.settings && state.status.settings.confidence) || 0.6);
    const b = view.box;
    const seen = view.seen == null ? 1 : Number(view.seen);
    const el = $("box");
    if (b && seen >= conf) {
      el.style.left = `${(b.left * 100).toFixed(2)}%`;
      el.style.top = `${(b.top * 100).toFixed(2)}%`;
      el.style.width = `${((b.right - b.left) * 100).toFixed(2)}%`;
      el.style.height = `${((b.bottom - b.top) * 100).toFixed(2)}%`;
      $("boxLabel").textContent = `${(state.run && state.run.target) || "target"} ${pct(seen)}`;
      el.classList.remove("pt-hidden");
    } else {
      el.classList.add("pt-hidden");
    }
    let turn = 0;
    for (const act of (step && step.actions) || []) if (act.look) turn += Number(act.look);
    const arrow = $("turn");
    if (turn) {
      const len = Math.min(45, Math.abs(turn) * 0.6 + 6);
      arrow.style.width = `${len}%`;
      arrow.style.left = turn > 0 ? "50%" : `${50 - len}%`;
      arrow.classList.toggle("is-right", turn > 0);
      arrow.classList.toggle("is-left", turn < 0);
      arrow.classList.remove("pt-hidden");
    } else {
      arrow.classList.add("pt-hidden");
    }
  }

  async function showFrame(step) {
    const img = $("frame");
    if (!step) {
      img.removeAttribute("src");
      $("frameEmpty").classList.remove("pt-hidden");
      return;
    }
    $("frameEmpty").classList.add("pt-hidden");
    const key = `${state.runId}:${step.n}`;
    const thumb = state.thumbs.get(key);
    if (thumb && !state.frames.has(key)) img.src = thumb;
    let full = state.frames.get(key);
    if (!full) {
      try {
        const out = await rpc("frame", { run_id: state.runId, step: step.n });
        if (out && out.ok) {
          full = out.src;
          state.frames.set(key, full);
          if (state.frames.size > 40) state.frames.delete(state.frames.keys().next().value);
        }
      } catch (_) { /* the thumbnail stays */ }
    }
    if (full && state.selected === step.n && `${state.runId}:${state.selected}` === key) img.src = full;
  }

  function caption(step) {
    if (!step) return "Waiting for a step";
    const bits = [`Step ${step.n}`, `${step.ms} ms`];
    if (step.jev_ms) bits.push(`Jev ${step.jev_ms} ms`);
    if (step.capture_ms) bits.push(`capture ${step.capture_ms} ms`);
    if (step.dry_run) bits.push("dry run: nothing pressed");
    if (step.kind) bits.push(step.kind);
    return bits.join(" · ");
  }

  function select(n) {
    state.selected = n;
    const step = state.steps.find((s) => s.n === n) || null;
    $("caption").textContent = caption(step);
    showPressing(step);
    showAnswers(step);
    showOverlay(step);
    showMap(step);
    showFrame(step);
    for (const li of document.querySelectorAll(".pt-step")) li.classList.toggle("is-selected", Number(li.dataset.n) === n);
  }

  // ------------------------------------------------------------------ timeline

  function actChips(step) {
    const out = [];
    for (const act of step.actions || []) {
      if (act.look) out.push(`turn ${act.look > 0 ? "R" : "L"} ${Math.abs(act.look).toFixed(0)}°`);
      else if (act.keys) out.push(`${act.keys.join("+")} ${Number(act.seconds || 0).toFixed(1)} s`);
      else if (act.click) out.push(`${act.click} click`);
      else if (act.wait) out.push(`wait ${Number(act.wait).toFixed(1)} s`);
    }
    return out.length ? out : ["look"];
  }

  function stepRow(step) {
    const li = document.createElement("li");
    li.className = "pt-step" + (step.error ? " is-error" : "");
    li.dataset.n = String(step.n);
    li.tabIndex = 0;
    const thumb = state.thumbs.get(`${state.runId}:${step.n}`) || "";
    li.innerHTML = `<span class="pt-step__n">${step.n}</span>
      <img class="pt-step__thumb" alt=""${thumb ? ` src="${thumb}"` : ""}>
      <div class="pt-step__body"><div class="pt-chips">${actChips(step).map((c) => `<span class="pt-act${step.dry_run ? " is-dry" : ""}">${esc(c)}</span>`).join("")}</div>
      <div class="pt-step__why" title="${esc(step.why)}">${esc(step.why)}</div></div>
      <span class="pt-step__ms">${step.ms} ms</span>`;
    const pick = () => { setFollow(false); select(step.n); };
    li.addEventListener("click", pick);
    li.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
    if (!thumb) lazyThumb(li, step.n);
    return li;
  }

  async function lazyThumb(li, n) {
    const runId = state.runId;
    try {
      const out = await rpc("frame", { run_id: runId, step: n, width: 160 });
      if (out && out.ok && runId === state.runId) {
        state.thumbs.set(`${runId}:${n}`, out.src);
        const img = li.querySelector("img");
        if (img) img.src = out.src;
      }
    } catch (_) { /* no thumbnail */ }
  }

  function renderTimeline() {
    const ol = $("timeline");
    ol.innerHTML = "";
    const steps = state.steps.slice().reverse();
    if (!steps.length) {
      ol.innerHTML = `<li class="pt-empty-small">No steps in this run yet.</li>`;
      return;
    }
    for (const s of steps) ol.appendChild(stepRow(s));
  }

  function addStep(step) {
    if (state.steps.some((s) => s.n === step.n)) return;
    state.steps.push(step);
    state.steps.sort((a, b) => a.n - b.n);
    const ol = $("timeline");
    const empty = ol.querySelector(".pt-empty-small");
    if (empty) empty.remove();
    ol.insertBefore(stepRow(step), ol.firstChild);
    if (state.follow) select(step.n);
  }

  // ------------------------------------------------------------------ runs + status

  function runLabel(r) {
    if (r.id === "manual") return "Single actions";
    const when = r.started ? new Date(r.started * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    return `${when} · ${r.goal || r.target || r.id} · ${r.status}`;
  }

  async function loadRuns(keep) {
    try {
      const out = await rpc("list_runs", { limit: 15 });
      const sel = $("runPick");
      const rows = (out && out.runs) || [];
      sel.innerHTML = rows.length ? rows.map((r) => `<option value="${esc(r.id)}">${esc(runLabel(r))}</option>`).join("") : `<option value="">No runs yet</option>`;
      const want = keep && rows.some((r) => r.id === keep) ? keep : (rows.find((r) => r.status === "running") || rows[0] || {}).id;
      if (want) sel.value = want;
      return want || "";
    } catch (err) {
      showErr(err.message);
      return "";
    }
  }

  async function openRun(runId) {
    state.runId = runId || "";
    state.steps = [];
    state.run = null;
    state.selected = 0;
    if (!state.runId) {
      renderTimeline();
      select(0);
      paintRun();
      return;
    }
    try {
      const out = await rpc("get_run", { run_id: state.runId });
      if (out && out.ok) {
        const { steps, ...meta } = out.run;
        state.run = meta;
        state.steps = steps || [];
      }
    } catch (err) {
      showErr(err.message);
    }
    renderTimeline();
    const last = state.steps[state.steps.length - 1];
    select(last ? last.n : 0);
    paintRun();
  }

  function paintRun() {
    const r = state.run;
    const badge = $("bRun");
    const running = !!(r && (r.status === "running" || r.status === "stopping"));
    badge.className = "dk-badge " + (STATUS_BADGE[r && r.status] || "");
    if (!r) badge.textContent = "Idle";
    else if (r.id === "manual") badge.textContent = "Single actions";
    else if (running) badge.textContent = `Playing · step ${r.n_steps || 0}/${r.max_steps || "?"} · ${Number(r.seconds || 0).toFixed(0)} s`;
    else badge.textContent = `${r.status}${r.n_steps ? ` · ${r.n_steps} steps` : ""}${r.seconds ? ` · ${Number(r.seconds).toFixed(1)} s` : ""}`;
    $("runInfo").textContent = r && r.id !== "manual"
      ? `${r.goal || ""}${r.reason ? " — " + r.reason : ""}${r.avg_ms ? ` · avg step ${r.avg_ms} ms (Jev ${r.jev_ms} ms)` : ""}${r.dry_run ? " · dry run" : ""}`
      : "";
    const active = !!(state.status && state.status.active_run);
    $("stopBtn").disabled = !(running || active);
    $("startBtn").disabled = running || active;
  }

  async function refreshStatus() {
    try {
      const st = await rpc("status");
      state.status = st;
      const c = st.client || {};
      const bc = $("bClient");
      bc.className = "dk-badge " + (c.playing ? "dk-badge--success" : c.loading ? "dk-badge--warn" : "dk-badge--danger");
      bc.textContent = c.playing ? "Fortnite: in the island" : c.loading ? "Fortnite: loading" : "Fortnite: not open";
      bc.title = c.error || "";
      const j = st.jev || {};
      const bj = $("bJev");
      bj.className = "dk-badge " + (j.key ? "dk-badge--success" : "dk-badge--danger");
      bj.textContent = j.key ? `Jev: ${j.model}${j.box ? "" : " (no box)"}` : "Jev: no TypeSafe key";
      bj.title = j.key ? "" : "Paste the TypeSafe key in Settings → LLMs → TypeSafe";
      const b = st.beacon || {};
      const bb = $("bBeacon");
      bb.className = "dk-badge " + (b.on ? "dk-badge--success" : b.seen ? "dk-badge--warn" : "");
      bb.textContent = b.on ? "Beacon: on" : b.seen ? `Beacon: quiet ${Math.round(b.age_s || 0)} s` : "Beacon: off";
      bb.title = b.on ? "World coordinates on: targets behind buildings or upstairs are found" : "Add the Play Tester beacon device to the island to steer by world coordinates";
      $("formMsg").textContent = st.ready ? "" : st.next || "";
      paintRun();
      if (!state.steps.length) showMap(null);
    } catch (err) {
      showErr(err.message);
    }
  }

  function showErr(msg) {
    const el = $("formErr");
    el.textContent = msg || "";
    el.classList.toggle("pt-hidden", !msg);
  }

  function setFollow(on) {
    state.follow = on;
    $("follow").setAttribute("aria-pressed", on ? "true" : "false");
    $("follow").className = "dk-btn " + (on ? "dk-btn--secondary" : "dk-btn--ghost");
  }

  // ------------------------------------------------------------------ live

  function onStep(ev) {
    if (ev.thumb) state.thumbs.set(`${ev.run_id}:${ev.step.n}`, ev.thumb);
    if (ev.run_id !== state.runId) {
      if (state.follow && ev.run_id !== "manual") loadRuns(ev.run_id).then(openRun);
      else if (state.follow && ev.run_id === "manual" && !(state.run && state.run.status === "running")) loadRuns("manual").then(openRun);
      return;
    }
    if (state.run) {
      state.run.n_steps = ev.n_steps;
      state.run.status = ev.status;
    }
    addStep(ev.step);
    paintRun();
  }

  function onRun(ev) {
    const r = ev.run || {};
    if (r.id === state.runId) {
      state.run = { ...(state.run || {}), ...r };
      paintRun();
      if (DONE.includes(r.status)) loadRuns(state.runId);
    } else if (state.follow && r.status === "running") {
      loadRuns(r.id).then(openRun);
    } else {
      loadRuns(state.runId);
    }
    refreshStatus();
  }

  async function poll() {
    if (!state.visible) return;
    const now = Date.now();
    const running = state.run && (state.run.status === "running" || state.run.status === "stopping");
    if (running && state.runId && now - state.lastPoll > 1400) {
      state.lastPoll = now;
      const last = state.steps.length ? state.steps[state.steps.length - 1].n : 0;
      try {
        const out = await rpc("get_run", { run_id: state.runId, from_step: last });
        if (out && out.ok) {
          const { steps, ...meta } = out.run;
          state.run = { ...(state.run || {}), ...meta };
          for (const s of steps || []) addStep(s);
          paintRun();
        }
      } catch (_) { /* next tick */ }
    }
  }

  window.addEventListener("message", (ev) => {
    const e = ev.data && ev.data.channel === CHANNEL ? ev.data.event : null;
    if (!e) return;
    if (e.type === "playtester_step") onStep(e);
    else if (e.type === "playtester_run") onRun(e);
    else if (e.type === "panel.visibility") {
      state.visible = !!e.visible;
      if (state.visible) { refreshStatus(); loadRuns(state.runId).then((id) => (id !== state.runId ? openRun(id) : null)); }
    } else if (e.type === "plugin_scope_changed") {
      loadRuns("").then(openRun);
    }
  });

  // ------------------------------------------------------------------ form

  $("goalForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const goal = $("fGoal").value.trim();
    const target = $("fTarget").value.trim();
    const actor = $("fActor").value.trim();
    if (!goal && !target && !actor) {
      showErr("Give a goal, a target or a level actor.");
      $("fGoal").focus();
      return;
    }
    showErr("");
    $("startBtn").disabled = true;
    try {
      const out = await rpc("run", {
        goal, target, target_actor: actor, success: $("fSuccess").value.trim(), success_print: $("fPrint").value.trim(),
        interact: $("fInteract").checked, dry_run: $("fDry").checked,
      });
      if (out && out.ok === false) {
        showErr(out.error);
      } else if (out && out.run_id) {
        setFollow(true);
        call("prefs.set", { id: "last_goal", value: goal });
        call("prefs.set", { id: "last_target", value: target });
        call("prefs.set", { id: "last_actor", value: actor });
        await loadRuns(out.run_id);
        await openRun(out.run_id);
      }
    } catch (err) {
      showErr(err.message);
    }
    refreshStatus();
  });

  $("stopBtn").addEventListener("click", async () => {
    try {
      const out = await rpc("stop", { run_id: state.run && state.run.status === "running" ? state.runId : "" });
      if (out && out.ok === false) showErr(out.error);
    } catch (err) {
      showErr(err.message);
    }
  });

  $("runPick").addEventListener("change", (ev) => { setFollow(false); openRun(ev.target.value); });
  $("follow").addEventListener("click", () => {
    setFollow(!state.follow);
    if (state.follow) loadRuns("").then(openRun);
  });

  // ------------------------------------------------------------------ start

  async function boot() {
    buildKeys();
    showPressing(null);
    showAnswers(null);
    try {
      const prefs = await call("prefs.get");
      const p = (prefs && prefs.prefs) || {};
      if (typeof p.last_goal === "string") $("fGoal").value = p.last_goal;
      if (typeof p.last_target === "string") $("fTarget").value = p.last_target;
      if (typeof p.last_actor === "string") $("fActor").value = p.last_actor;
    } catch (_) { /* no saved goal */ }
    try {
      await call("plugin.subscribe", { types: ["playtester_step", "playtester_run"] });
    } catch (err) {
      showErr(err.message);
    }
    await refreshStatus();
    const id = await loadRuns("");
    await openRun(id);
    setInterval(poll, 500);
    setInterval(() => { if (state.visible) refreshStatus(); }, 5000);
  }

  boot();
})();
