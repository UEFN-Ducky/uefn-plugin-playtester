/* Play Tester tab: agents start runs (playtester_run), people watch them here.
   One inner tab per tester (a game: Fortnite, Godot, a web page, any window); the view below
   shows the selected tester. Follows runs live (push events, with a slow poll as a safety net)
   and shows what the agent asked for, the exact questions and rules, every step's frame, keys,
   Jev's answers and where the player is. People can stop a run and nudge the player by hand. */
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
  const TONE = {
    running: "run", stopping: "warn", success: "good", done: "good", failed: "bad",
    blocked: "warn", timeout: "warn", stopped: "info", manual: "info",
  };
  const BADGE = { run: "dk-badge--accent", good: "dk-badge--success", bad: "dk-badge--danger", warn: "dk-badge--warn", info: "dk-badge--info" };
  const ORIGIN = { tool: "Agent tool call", workflow: "Workflow node", manual: "Single actions" };
  const RULES = {
    approach: "target seen: turn toward it and walk", scan: "target not seen: turn and look", lost: "lost sight of it: turn back",
    explore: "not found after a full look: walk somewhere new", "free-explore": "no target: walk and look around",
    "jev-pick": "no target: Jev picks the next move", nudge: "next to it, no use prompt: step closer", use: "use prompt shown: hold E",
    settle: "used it: waiting for the success check", reached: "reached the target", done: "success check passed twice",
    "done-once": "success check passed once: look again", "wait-success": "at the target: waiting for the success check",
    unstick: "the view stopped changing: back up and turn", loading: "loading screen: wait", eliminated: "the player was eliminated",
    blocked: "a menu or popup covers the game", "coord-turn": "world position: turn toward the target", "coord-walk": "world position: walk toward the target",
    "coord-detour": "world position: walk around what blocks the way", "floor-climb": "target on another floor: take the way up or down",
    "floor-stairs": "stairs seen: walk to them", "floor-scan": "target on another floor: look for stairs", "floor-explore": "no stairs seen: explore",
    "jev-error": "Jev didn't answer",
  };
  const NAMES = {
    screen: "Screen", seen: "Target seen", where: "Where", near: "How close", moved: "Moved",
    path: "Path", prompt: "Use prompt", done: "Success", next: "Jev's move", stairs_seen: "Way up/down",
  };

  // Ducky's model picker: Jev under TypeSafe, Jev under Ducky AI only with a Ducky AI plan, and
  // the gateways' vision models. Ducky AI's chat model is left out: frames through Ducky AI go to Jev.
  const JEV_MODELS = ["jev-1.14.0", "jev-preview", "jev-latest", "jev-1.13.0"];
  const jevRows = (plan) => [
    ...JEV_MODELS.map((m) => ({ id: `typesafe:${m}`, name: `Jev ${m.replace(/^jev-/, "")}`, group: "TypeSafe", note: "Not for chat", vision: true })),
    ...(plan ? [{ id: "uefn_ducky:jev-1.14.0", name: "Jev 1.14.0", group: "Ducky AI", note: "Not for chat", vision: true }] : []),
  ];

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (v) => (v == null ? "—" : `${Math.round(Number(v) * 100)}%`);
  const num = (v, d = 1) => Number(v || 0).toFixed(d);
  const icon = (id) => `<svg class="ico"><use href="#${id}"/></svg>`;
  const kilo = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n || 0));
  const clock = (t) => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");

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
  // Calls about one tester's game, runs and frames carry the selected tester.
  const trpc = (method, params = {}) => rpc(method, { tester: state.tester, ...params });
  const remember = (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* no storage */ } };
  const recall = (key) => { try { return localStorage.getItem(key) || ""; } catch (_) { return ""; } };
  const KIND_ICON = { fortnite: "i-pad", godot: "i-pad", unreal: "i-pad", roblox: "i-pad", web: "i-globe", window: "i-window" };

  const state = {
    tester: recall("pt.tester") || "default",
    testers: [],        // every open tester's summary (status.testers)
    news: new Set(),    // testers with steps since their tab was last shown
    tabsHtml: "",       // the tab strip as last drawn
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
    orders: false,      // "Show exact orders" open
    tab: "call",
    stepOpen: false,    // "Step data" open
  };
  const running = () => !!(state.run && (state.run.status === "running" || state.run.status === "stopping"));
  const conf = () => Number((state.run && state.run.settings && state.run.settings.confidence)
    || (state.status && state.status.settings && state.status.settings.confidence) || 0.6);

  // JSON with keys, strings and numbers marked for color.
  function code(obj) {
    const text = JSON.stringify(obj, null, 2) ?? "null";
    const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
    let out = "", last = 0, m;
    while ((m = re.exec(text))) {
      out += esc(text.slice(last, m.index));
      if (m[1]) out += m[2] ? `<span class="k">${esc(m[1])}</span>${m[2]}` : `<span class="s">${esc(m[1])}</span>`;
      else out += `<span class="n">${m[0]}</span>`;
      last = re.lastIndex;
    }
    return out + esc(text.slice(last));
  }

  // ------------------------------------------------------------------ actions in words

  function actChips(step) {
    const out = [];
    for (const act of step.actions || []) {
      if (act.look) out.push(`turn ${act.look > 0 ? "R" : "L"} ${Math.abs(act.look).toFixed(0)}°`);
      else if (act.keys) out.push(act.keep ? `${act.keys.join("+")} held` : `${act.keys.join("+")} ${num(act.seconds)} s`);
      else if (act.click) out.push(`${act.click} click`);
      else if (act.wait) out.push(`wait ${num(act.wait)} s`);
    }
    return out.length ? out : ["look"];
  }

  function moveText(step) {
    if (!step) return "";
    const said = [];
    for (const a of step.actions || []) {
      if (a.look) said.push(`turn ${a.look > 0 ? "right" : "left"} ${Math.abs(a.look).toFixed(0)}°`);
      if (a.look_y) said.push(`look ${a.look_y > 0 ? "down" : "up"} ${Math.abs(a.look_y).toFixed(0)}°`);
      if (a.keys) {
        const k = a.keys.join("+"), s = num(a.seconds);
        if (a.keep) said.push(`keep ${k} down`);
        else if (k === "W") said.push(`walk forward ${s} s`);
        else if (k === "S") said.push(`walk back ${s} s`);
        else if (k === "SPACE") said.push("jump");
        else said.push(`hold ${k} ${s} s`);
      }
      if (a.click) said.push(`${a.click} click`);
      if (a.wait && !a.look && !a.keys) said.push(`wait ${num(a.wait)} s`);
    }
    if (!said.length) return step.error ? "Jev didn't answer this step" : "Look only, no keys";
    const text = said.join(", then ");
    return text[0].toUpperCase() + text.slice(1);
  }

  function pressedText(step) {
    if (step.dry_run) return "Dry run: nothing was pressed.";
    const lines = [];
    for (const d of step.done || []) {
      const cut = (part) => (part && part.cut_short ? `  (cut short: ${part.cut_short})` : "");
      if (d.look) lines.push(`mouse ${d.look.dx_counts} counts = ${d.look.dx_deg}°${d.look.dy_deg ? `, ${d.look.dy_counts} counts = ${d.look.dy_deg}° up/down` : ""}${cut(d.look)}`);
      else if (d.press) lines.push(`${(d.press.keys || []).join("+")} down ${num(d.press.seconds, 2)} s${cut(d.press)}`);
      else if (d.keep) lines.push(`${(d.keep.keys || []).join("+")} kept down`);
      else if (d.click) lines.push(`${d.click.button} click ${num(d.click.seconds, 2)} s${cut(d.click)}`);
      else if (d.wait != null) lines.push(`waited ${d.wait} s`);
      else lines.push(JSON.stringify(d));
    }
    return lines.length ? lines.join("\n") : "Nothing was pressed.";
  }

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
        b.title = b.dataset.tip = `Press ${KEY_LABEL[k] || k} for 0.3 s`;
        b.innerHTML = `${esc(KEY_LABEL[k] || k)}<small></small>`;
        b.addEventListener("click", () => nudge({ keys: k }));
        line.appendChild(b);
      }
      wrap.appendChild(line);
    }
    $("dial").dataset.tip = $("dial").title;
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
        ? await trpc("press", { keys: what.keys, seconds: 0.3 })
        : await trpc("look", { dx_degrees: what.look });
      if (out && out.ok === false) showErr(out.error);
      else showErr("");
    } catch (err) {
      showErr(err.message);
    }
  }

  function showPressing(step) {
    const held = new Map();
    let turn = 0;
    const clicks = [];
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
    $("dialArrow").style.setProperty("--turn", `${Math.max(-90, Math.min(90, turn))}deg`);
    $("dial").classList.toggle("is-on", !!turn);
    const label = turn ? `turn ${turn > 0 ? "right" : "left"} ${Math.abs(turn).toFixed(0)}°` : "no turn";
    $("dialLabel").textContent = dry && (turn || held.size) ? `${label} (dry run)` : label;
  }

  // ------------------------------------------------------------------ position (map heading up)

  function sayDir(rel) {
    const t = Math.abs(Number(rel.turn_deg) || 0).toFixed(0);
    let text = rel.side === "ahead" ? "straight ahead"
      : rel.side === "behind" ? `behind (${t}° ${rel.turn_deg > 0 ? "right" : "left"})`
      : `${t}° ${rel.side}`;
    if (rel.floor === "up") text += `, ${num(rel.height_m)} m up`;
    else if (rel.floor === "down") text += `, ${num(Math.abs(Number(rel.height_m)))} m down`;
    else text += ", same floor";
    return text;
  }
  const sayRel = (rel) => (rel ? `${num(rel.distance_m)} m, ${sayDir(rel)}` : "");

  function showXyz(pose, target) {
    const el = $("xyz");
    if (!pose && !target) { el.classList.add("pt-hidden"); return; }
    const cell = (v) => `<span>${v == null ? "—" : Math.round(Number(v))}</span>`;
    const name = (state.run && state.run.target_actor) || "Target";
    el.innerHTML = `<span class="h l">cm</span><span class="h">x</span><span class="h">y</span><span class="h">z</span><span class="h">yaw</span>`
      + (pose ? `<span class="l">Player</span>${cell(pose.x)}${cell(pose.y)}${cell(pose.z)}<span>${Math.round(Number(pose.yaw))}°</span>` : "")
      + (target ? `<span class="l" title="${esc(name)}">${esc(name)}</span>${cell(target.x)}${cell(target.y)}${cell(target.z)}<span>—</span>` : "");
    el.classList.remove("pt-hidden");
  }

  function showMap(step) {
    const where = (step && step.where) || null;
    const pose = where && where.pose;
    const target = state.run && state.run.target_point;
    const trailEl = $("mapTrail");
    const dot = $("mapTarget");
    const line = $("mapLine");
    const b = (state.status && state.status.beacon) || {};
    $("beaconTag").textContent = b.none ? "by the picture" : b.on ? "beacon on" : b.seen ? `beacon quiet ${Math.round(b.age_s || 0)} s` : "beacon off";
    if (!pose) {
      $("mapMarks").innerHTML = "";
      $("mapNear").textContent = "";
      trailEl.setAttribute("points", "");
      dot.classList.add("pt-hidden");
      line.classList.add("pt-hidden");
      $("mapBig").textContent = b.none ? "By the picture" : b.on ? "Waiting" : "No beacon";
      $("mapSub").textContent = b.none ? `${gameName(state.status)} has no world positions` : b.on ? "No run yet" : "Image only";
      showXyz(null, target);
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
    $("mapBig").textContent = rel ? `${num(rel.distance_m)} m` : "No target position";
    $("mapSub").textContent = rel ? sayDir(rel) : "Beacon on, but the run has no target coordinates";
    showXyz(pose, target);
    const bits = [];
    if ((where.nearby || []).length) {
      bits.push("Near: " + where.nearby.map((n) => `${n.name} ${Number(n.distance_m).toFixed(0)} m ${n.side}${Math.abs(n.height_m) > 2.5 ? (n.height_m > 0 ? " up" : " down") : ""}`).join(" · "));
    }
    if (pose.state) bits.push(pose.state === "ground" ? "on ground" : pose.state);
    if (where.climb_m != null && Math.abs(where.climb_m) >= 0.2) bits.push(`${where.climb_m > 0 ? "climbing" : "dropping"} ${num(Math.abs(where.climb_m))} m`);
    if (where.moved_m != null) bits.push(`moved ${where.moved_m} m`);
    if (where.calib) bits.push(`mouse ${where.calib.counts}/90°`);
    $("mapNear").textContent = bits.join(" · ");
  }

  // ------------------------------------------------------------------ Jev's move + answers

  function bar(value, c) {
    const v = Math.max(0, Math.min(1, Number(value) || 0));
    return `<div class="pt-bar${v < c ? " is-low" : ""}"><i style="--w:${(v * 100).toFixed(0)}%"></i></div>`;
  }

  function showAnswers(step) {
    const view = (step && step.view) || {};
    const c = conf();
    const rows = [];
    for (const key of ["screen", "seen", "where", "near", "moved", "path", "prompt", "stairs_seen", "done", "next"]) {
      const v = view[key];
      if (v == null) continue;
      if (typeof v === "object" && v.choice) {
        const all = v.all || {};
        const chips = Object.keys(all).length ? Object.keys(all) : [v.choice];
        rows.push(`<div class="pt-ans"><span class="pt-ans__name">${esc(NAMES[key] || key)}</span>
          <div class="pt-chips">${chips.map((k) => `<span class="pt-choice${k === v.choice ? " is-pick" : ""}">${esc(k.replace(/_/g, " "))}${all[k] != null ? " " + pct(all[k]) : ""}</span>`).join("")}</div>
          <span class="pt-ans__val">${pct(v.p)}</span></div>`);
      } else {
        rows.push(`<div class="pt-ans"><span class="pt-ans__name">${esc(NAMES[key] || key)}</span>${bar(v, c)}<span class="pt-ans__val">${pct(v)}</span></div>`);
      }
    }
    for (const [key, label] of [["box", "Box"], ["stairs_box", "Stairs box"]]) {
      const b = view[key];
      if (!b) continue;
      rows.push(`<div class="pt-ans"><span class="pt-ans__name">${label}</span><span class="dk-muted pt-mono">x ${b.left.toFixed(2)}–${b.right.toFixed(2)}, y ${b.top.toFixed(2)}–${b.bottom.toFixed(2)}</span><span class="pt-ans__val">${pct(b.confidence)}</span></div>`);
    }
    if (view.geo) rows.push(`<div class="pt-ans"><span class="pt-ans__name">Coordinates</span><span class="pt-ans__value">${esc(sayRel(view.geo))}</span></div>`);

    const box = $("answers");
    box.innerHTML = rows.length ? rows.join("") : `<div class="pt-empty-small">No Jev answers for this step.</div>`;
    box.classList.toggle("pt-hidden", !step);
    $("jevMs").textContent = step && step.jev_ms ? `Jev ${step.jev_ms} ms` : "";
    const rule = step && (step.rule || (step.error ? "jev-error" : step.kind || ""));
    $("rule").innerHTML = rule ? `<span class="pt-rule" title="${esc(RULES[rule] || rule)}">${esc(rule)}</span>` : "";
    const moveEl = $("move");
    moveEl.className = "pt-move" + (!step ? " is-none" : step.error ? " is-error" : "");
    moveEl.textContent = step ? moveText(step) : "The move Jev picks for each step shows here";
    $("why").textContent = (step && step.why) || "";
    $("why").classList.toggle("pt-hidden", !(step && step.why));
    $("stepData").classList.toggle("pt-hidden", !step);
    showStepData(step);
  }

  function showStepData(step) {
    $("stepBtn").setAttribute("aria-expanded", String(state.stepOpen));
    const body = $("stepBody");
    body.classList.toggle("pt-hidden", !state.stepOpen);
    if (!step || !state.stepOpen) return;
    const timing = [`capture ${step.capture_ms || 0} ms`, `Jev ${step.jev_ms || 0} ms`, `whole step ${step.ms} ms`];
    if (step.model) timing.push(step.model);
    body.innerHTML = `<h4>Planned actions</h4><pre class="pt-code">${code(step.actions || [])}</pre>
      <h4>Pressed</h4><pre class="pt-code">${esc(pressedText(step))}${step.cut ? `\n<span class="c">${esc(step.cut)}</span>` : ""}</pre>
      ${step.q && step.q.length ? `<h4>Asked Jev</h4><pre class="pt-code">${esc(step.q.join(", "))}</pre>` : ""}
      <h4>Jev answers</h4><pre class="pt-code">${code(step.raw && Object.keys(step.raw).length ? step.raw : step.view || {})}</pre>
      ${step.where ? `<h4>Position</h4><pre class="pt-code">${code(step.where)}</pre>` : ""}
      <h4>Timing</h4><pre class="pt-code">${esc(timing.join(" · "))}</pre>`;
  }

  // ------------------------------------------------------------------ frame + overlay

  function showOverlay(step) {
    const view = (step && step.view) || {};
    const b = view.box;
    const seen = view.seen == null ? 1 : Number(view.seen);
    const el = $("box");
    if (b && seen >= conf()) {
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
    const tl = step ? [`Step ${step.n}`, `${step.ms} ms`] : [];
    if (step && step.kind) tl.push(step.kind);
    if (step && step.dry_run) tl.push("dry run");
    $("ovTL").innerHTML = step ? `<span class="pt-chip">${esc(tl.join(" · "))}</span>` : "";
    $("ovTR").innerHTML = !step || !running() ? ""
      : state.follow ? `<span class="pt-chip"><span class="pt-live__dot is-rec pt-pulse"></span>Live</span>`
      : `<button type="button" class="pt-chip pt-chip--btn" data-live="1">Back to live</button>`;
    $("ovBL").innerHTML = !step ? ""
      : step.error ? `<span class="pt-chip pt-chip--warn">Jev error</span>`
      : actChips(step).map((c) => `<span class="pt-chip${step.dry_run || c === "look" ? "" : " pt-chip--accent"}">${esc(c)}</span>`).join("");
  }

  async function showFrame(step) {
    const img = $("frame");
    if (!step) {
      img.removeAttribute("src");
      return;
    }
    const key = `${state.runId}:${step.n}`;
    const thumb = state.thumbs.get(key);
    if (thumb && !state.frames.has(key)) img.src = thumb;
    let full = state.frames.get(key);
    if (!full) {
      try {
        const out = await trpc("frame", { run_id: state.runId, step: step.n });
        if (out && out.ok) {
          full = out.src;
          state.frames.set(key, full);
          if (state.frames.size > 40) state.frames.delete(state.frames.keys().next().value);
        }
      } catch (_) { /* the thumbnail stays */ }
    }
    if (full && state.selected === step.n && `${state.runId}:${state.selected}` === key) img.src = full;
  }

  // The frame area before there is a step: what's missing, or that it waits for an agent.
  function showSetup(step) {
    const el = $("setup");
    el.classList.toggle("pt-hidden", !!step);
    if (step) return;
    const st = state.status || {};
    const c = st.client || {}, j = st.jev || {};
    const game = gameName(st);
    const [ic, text] = running() ? ["i-play", "Starting…"]
      : !c.playing || !j.key ? ["i-pad", c.loading ? `${game} is loading` : `Waiting for ${game}`]
      : ["i-bot", "Waiting for an agent"];
    el.innerHTML = `<span class="pt-setup__icon">${icon(ic)}</span><div class="pt-setup__title">${text}</div>`;
  }

  function select(n) {
    state.selected = n;
    const step = state.steps.find((s) => s.n === n) || null;
    showSetup(step);
    showPressing(step);
    showAnswers(step);
    showOverlay(step);
    showMap(step);
    showFrame(step);
    const track = $("film");
    let cur = null;
    for (const b of track.querySelectorAll(".pt-thumb")) {
      const on = Number(b.dataset.n) === n;
      b.classList.toggle("is-selected", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      if (on) cur = b;
    }
    if (cur) {
      const left = cur.offsetLeft - 8, right = cur.offsetLeft + cur.offsetWidth + 8;
      if (left < track.scrollLeft) track.scrollLeft = left;
      else if (right > track.scrollLeft + track.clientWidth) track.scrollLeft = right - track.clientWidth;
    }
  }

  // ------------------------------------------------------------------ filmstrip

  function stepThumb(step) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pt-thumb" + (step.error ? " is-error" : "") + (step.dry_run ? " is-dry" : "");
    b.dataset.n = String(step.n);
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", "false");
    b.title = step.why || "";
    const thumb = state.thumbs.get(`${state.runId}:${step.n}`) || "";
    b.innerHTML = `<img alt=""${thumb ? ` src="${thumb}"` : ""}>
      <span class="pt-thumb__foot"><span class="pt-thumb__n">${step.n}</span><span class="pt-thumb__act">${esc(step.error ? "Jev error" : actChips(step).join(" + "))}</span></span>`;
    b.addEventListener("click", () => { setFollow(false); select(step.n); });
    if (!thumb) lazyThumb(b, step.n);
    return b;
  }

  async function lazyThumb(el, n) {
    const runId = state.runId;
    try {
      const out = await trpc("frame", { run_id: runId, step: n, width: 160 });
      if (out && out.ok && runId === state.runId) {
        state.thumbs.set(`${runId}:${n}`, out.src);
        const img = el.querySelector("img");
        if (img) img.src = out.src;
      }
    } catch (_) { /* no thumbnail */ }
  }

  function renderTimeline() {
    const track = $("film");
    track.innerHTML = "";
    if (!state.steps.length) {
      track.innerHTML = `<div class="pt-film__empty">Steps show here as Jev plays.</div>`;
      return;
    }
    for (const s of state.steps) track.appendChild(stepThumb(s));
  }

  function addStep(step) {
    if (state.steps.some((s) => s.n === step.n)) return;
    state.steps.push(step);
    state.steps.sort((a, b) => a.n - b.n);
    const track = $("film");
    const empty = track.querySelector(".pt-film__empty");
    if (empty) empty.remove();
    const after = [...track.querySelectorAll(".pt-thumb")].find((b) => Number(b.dataset.n) > step.n) || null;
    track.insertBefore(stepThumb(step), after);
    if (state.follow) select(step.n);
  }

  function move(delta) {
    const i = state.steps.findIndex((s) => s.n === state.selected);
    const next = state.steps[i + delta];
    if (!next) return;
    setFollow(running() && i + delta === state.steps.length - 1);
    select(next.n);
    const b = $("film").querySelector(".pt-thumb.is-selected");
    if (b && $("film").contains(document.activeElement)) b.focus();
  }

  // ------------------------------------------------------------------ the agent's brief

  function paintBrief() {
    const r = state.run;
    const st = state.status || {};
    const el = $("brief");
    const tone = r ? TONE[r.status] || "" : "";
    el.className = "dk-card pt-brief" + (tone ? ` is-${tone}` : "");
    const active = !!st.active_run;
    $("stopBtn").classList.toggle("pt-hidden", !(running() || active));
    const badge = $("briefStatus");
    badge.classList.toggle("pt-hidden", !r || running() || r.id === "manual");
    if (r) {
      badge.className = `dk-badge ${BADGE[tone] || ""}` + (running() || r.id === "manual" ? " pt-hidden" : "");
      badge.textContent = r.status;
    }
    const full = !!r && r.id !== "manual";
    for (const id of ["briefFacts", "briefMeta", "briefBar"]) $(id).classList.toggle("pt-hidden", !full);
    $("ordersBtn").classList.toggle("pt-hidden", !r);
    $("orders").classList.toggle("pt-hidden", !r || !state.orders);
    $("ordersBtn").setAttribute("aria-expanded", String(!!r && state.orders));

    if (!r) {
      $("briefIcon").className = "pt-brief__icon";
      $("briefIcon").innerHTML = icon("i-bot");
      $("briefBy").textContent = "";
      $("briefGoal").textContent = active ? "Loading the run…" : "No play test yet";
      $("briefOutcome").textContent = active ? "" : "Agents start them from chat";
      return;
    }
    $("briefIcon").className = "pt-brief__icon" + (running() ? " pt-pulse" : "");
    $("briefIcon").innerHTML = icon(running() ? "i-play" : tone === "good" ? "i-check" : tone === "bad" ? "i-x" : r.id === "manual" ? "i-pad" : "i-dot");
    const by = [`${icon("i-bot")}${esc(ORIGIN[r.origin] || r.origin || "Agent")}`];
    if (r.started) by.push(`started ${esc(clock(r.started))}`);
    if (r.island) by.push(esc(r.island));
    $("briefBy").innerHTML = by.join(" · ");
    $("briefGoal").textContent = r.goal || r.target || r.target_actor || r.id;
    const out = $("briefOutcome");
    if (r.id === "manual") out.textContent = `${r.n_steps || 0} single action${r.n_steps === 1 ? "" : "s"} from agent tools and this tab.`;
    else if (r.status === "stopping") out.textContent = "Stopping…";
    else if (running()) out.textContent = "Playing now. You can stop it at any time.";
    else out.innerHTML = `<span class="pt-outcome--${tone}">${esc(r.status)}</span>${r.reason ? `: ${esc(r.reason)}` : ""}`;
    if (!full) return;

    const p = r.target_point;
    const facts = [];
    if (r.target) facts.push(["Target", r.target]);
    if (r.target_actor || p) facts.push(["Actor", `${r.target_actor || "point"}${p ? ` (${Math.round(p.x)}, ${Math.round(p.y)}, ${Math.round(p.z)})` : ""}`]);
    if (r.success) facts.push(["Passes when", r.success]);
    if (r.success_print) facts.push(["Or Verse prints", r.success_print]);
    if (r.interact) facts.push(["Use it", "hold E", true]);
    facts.push(["Limit", `${r.max_steps || "?"} steps${r.max_seconds ? ` / ${Math.round(r.max_seconds)} s` : ""}`]);
    facts.push(["Steers by", r.beacon && p ? "world position" : "the image"]);
    if (r.dry_run) facts.push(["Dry run", "nothing pressed", true]);
    $("briefFacts").innerHTML = facts.map(([k, v, on]) => `<span class="pt-fact${on ? " is-on" : ""}" title="${esc(k)}: ${esc(v)}"><b>${esc(k)}</b>${esc(v)}</span>`).join("");
    const meta = [`step ${r.n_steps || 0} of ${r.max_steps || "?"}`, `${num(r.seconds)} s${r.max_seconds ? ` of ${Math.round(r.max_seconds)} s` : ""}`];
    if (r.avg_ms) meta.push(`avg ${r.avg_ms} ms (Jev ${r.jev_ms} ms)`);
    if (r.tokens) meta.push(`${kilo(r.tokens.input)} in / ${kilo(r.tokens.output)} out tokens`);
    $("briefMeta").innerHTML = meta.map((m) => `<span>${esc(m)}</span>`).join("");
    const share = running() ? Math.min(1, (r.n_steps || 0) / (r.max_steps || 1)) : 1;
    $("briefBar").firstElementChild.style.setProperty("--w", `${(share * 100).toFixed(1)}%`);
  }

  // "Show exact orders": what the run was given, the questions, the rules, the log, the settings.
  function paintOrders() {
    const r = state.run;
    if (!r || !state.orders) return;
    for (const b of $("orderTabs").children) b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
    const body = $("orderBody");
    const row = (k, v, sub) => `<div class="pt-row"><span class="pt-row__k">${esc(k)}</span><span class="pt-row__v">${v}${sub ? `<div class="pt-row__sub">${sub}</div>` : ""}</span></div>`;
    if (state.tab === "call") {
      const given = {};
      for (const k of ["goal", "target", "target_actor", "target_point", "success", "success_print", "interact", "dry_run", "max_steps", "max_seconds"]) {
        if (r[k] !== undefined && r[k] !== null && r[k] !== "") given[k] = r[k];
      }
      const head = r.origin === "workflow" ? "// the workflow node's inputs, as the run stored them" : r.origin === "manual" ? "// single actions, no orders" : "// playtester_run, as the run stored it (defaults filled in)";
      const m = r.memory_used;
      const lesson = m ? [
        `${m.tries} earlier run${m.tries === 1 ? "" : "s"} on this target, ${m.wins || 0} passed`,
        [m.scan_side ? `It was first seen on the ${esc(m.scan_side)}, so scans turn ${esc(m.scan_side)} first.` : "",
         m.point ? `Target position ${Math.round(m.point.x)}, ${Math.round(m.point.y)}, ${Math.round(m.point.z)} from ${esc(m.point_from || "memory")}.` : "",
         m.warn ? esc(m.warn) : "", m.source ? `Last run: ${esc(m.source)}.` : ""].filter(Boolean).join(" "),
      ] : null;
      body.innerHTML = `<pre class="pt-code"><span class="c">${esc(head)}</span>\n${code(given)}</pre>
        <div class="pt-rows">
          ${row("steering", r.beacon ? "beacon on: the player's world position is known" : "beacon off: Jev steers by the image only")}
          ${lesson ? row("lessons used", esc(lesson[0]), lesson[1]) : row("lessons used", "none: no earlier run on this target")}
          ${row("run id", `<span class="pt-mono">${esc(r.id)}</span>`)}
        </div>`;
    } else if (state.tab === "questions") {
      const qs = Object.entries(r.question_text || {});
      body.innerHTML = !qs.length ? `<div class="pt-note">No questions asked yet.</div>`
        : `<div class="pt-note">Asked about every frame, in one request.</div>
          <div class="pt-rows">${qs.map(([k, q]) => row(k, esc(q))).join("")}</div>`;
    } else if (state.tab === "rules") {
      const t = r.totals || {};
      const rules = Object.entries(t.rules || {}).sort((a, b) => b[1] - a[1]);
      const max = Math.max(1, ...rules.map(([, c]) => c));
      const stats = [];
      if (t.turns != null) stats.push(`${t.turns} turn${t.turns === 1 ? "" : "s"} (${num(t.turn_deg, 0)}°)`);
      if (t.walked_m) stats.push(`walked ${num(t.walked_m)} m`);
      if (t.first_seen) stats.push(`first seen at step ${t.first_seen}`);
      const many = (n, one, more) => `${n} ${n === 1 ? one : more}`;
      if (t.uses) stats.push(many(t.uses, "use", "uses"));
      if (t.stuck) stats.push(`stuck ${many(t.stuck, "time", "times")}`);
      if (t.cut_short) stats.push(`${many(t.cut_short, "move", "moves")} cut short`);
      if (t.jev_errors) stats.push(many(t.jev_errors, "Jev error", "Jev errors"));
      body.innerHTML = !rules.length ? `<div class="pt-note">No steps yet.</div>`
        : `<div class="pt-rows">${rules.map(([name, c]) => `<div class="pt-row is-rule"><span><span class="pt-rule">${esc(name)}</span></span>
          <span>${bar(c / max, 0)}<div class="pt-row__sub">${esc(RULES[name] || "")}</div></span><span class="pt-row__n">${c}</span></div>`).join("")}</div>
          ${stats.length ? `<div class="pt-stats">${stats.map((s) => `<span>${esc(s)}</span>`).join("")}</div>` : ""}`;
    } else if (state.tab === "log") {
      const rows = [{ t: 0, kind: "start", text: `${ORIGIN[r.origin] || r.origin || "run"} started it` }];
      for (const e of r.events || []) rows.push({ t: e.t, kind: e.kind, text: eventText(e) });
      if (r.ended && r.started) rows.push({ t: r.ended - r.started, kind: "end", text: `${r.status}${r.reason ? ": " + r.reason : ""}` });
      const tone = { start: "dk-badge--info", end: BADGE[TONE[r.status]] || "", print: "dk-badge--info", cut_short: "dk-badge--warn", jev_error: "dk-badge--danger", released: "dk-badge--warn" };
      body.innerHTML = `<div class="pt-rows">${rows.map((e) => `<div class="pt-row is-log"><span class="pt-mono dk-muted">${num(e.t)} s</span>
        <span><span class="dk-badge ${tone[e.kind] || ""}">${esc(e.kind)}</span></span><span class="pt-row__v">${esc(e.text)}</span></div>`).join("")}</div>`;
    } else {
      const s = r.settings || {};
      const tk = r.tokens || {};
      body.innerHTML = `<div class="pt-rows">
        ${row("model", esc(r.model || ""), "the Jev model that reads each frame")}
        ${s.confidence != null ? row("confidence", pct(s.confidence), "answers below this count as no") : ""}
        ${s.counts != null ? row("mouse", `${esc(s.counts)} counts per 90°`, "the beacon corrects it while turning") : ""}
        ${s.size != null ? row("frame size", `${esc(s.size)} px`, "the screenshot width sent to Jev") : ""}
        ${r.smooth != null ? row("smooth walk", r.smooth ? "on" : "off", "walk and turn at the same time") : ""}
        ${r.click_focus != null ? row("click to focus", r.click_focus ? "on" : "off", "clicks the game to bring it to the front") : ""}
        ${r.profile ? row("played as", esc(`${r.profile.view} · ${r.profile.look}`), r.profile.keys && Object.keys(r.profile.keys).length ? esc(Object.entries(r.profile.keys).map(([k, v]) => `${k}→${v}`).join(", ")) : "the game's own keys") : ""}
        ${row("tokens", `${esc(tk.input || 0)} in, ${esc(tk.output || 0)} out`)}
      </div>`;
    }
  }

  function eventText(e) {
    if (e.kind === "print") return `Verse printed: ${e.text}`;
    if (e.kind === "focus") return e.ok ? `Brought the game to the front (${e.method || "focus"}${e.ms != null ? `, ${e.ms} ms` : ""})` : `Couldn't bring the game to the front (${e.method || "focus"})`;
    if (e.kind === "jev_error") return `${e.status ? `HTTP ${e.status}: ` : ""}${e.error || ""}`;
    if (e.kind === "cut_short") return `step ${e.step}: ${e.why}`;
    if (e.text || e.why) return e.text || e.why;
    const { t, kind, ...rest } = e;
    return JSON.stringify(rest);
  }

  // ------------------------------------------------------------------ runs + status

  function runLabel(r) {
    if (r.id === "manual") return "Single actions";
    const when = r.started ? new Date(r.started * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    return `${when} · ${r.goal || r.target || r.id} · ${r.status}`;
  }

  async function loadRuns(keep) {
    try {
      const out = await trpc("list_runs", { limit: 15 });
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
    if (state.runId) {
      try {
        const out = await trpc("get_run", { run_id: state.runId });
        if (out && out.ok) {
          const { steps, ...meta } = out.run;
          state.run = meta;
          state.steps = steps || [];
        }
      } catch (err) {
        showErr(err.message);
      }
    }
    renderTimeline();
    paintBrief();
    paintOrders();
    const last = state.steps[state.steps.length - 1];
    select(last ? last.n : 0);
  }

  function gameName(st) {
    return (st && st.tester && st.tester.label) || (st && st.target && st.target.label) || "the game";
  }

  // The header's model button: the gateway's logo, like the model button in a chat.
  function paintModel(j) {
    const btn = $("brainPick");
    const say = j.key ? `${j.name || "Jev"}${j.via ? ` · ${j.via}` : ""} reads the frames. Click to change.`
      : "No model can read the frames. Click to pick one.";
    btn.title = say;
    btn.setAttribute("aria-label", say);
    btn.classList.toggle("is-bad", !j.key);
    const logo = $("brainLogo");
    if (j.logo && logo.getAttribute("src") !== j.logo) logo.src = j.logo;
    logo.classList.toggle("pt-hidden", !j.logo);
    $("brainIcon").classList.toggle("pt-hidden", !!j.logo);
  }

  // The same three cards on every tab, each one short line and a "?" that explains it:
  // the game, the model that reads the frames, and where the player's position comes from.
  function paintReady(st) {
    const c = st.client || {}, j = st.jev || {}, b = st.beacon || {}, t = st.target || {};
    const kind = t.kind || "fortnite";
    const game = esc(gameName(st));
    const logo = j.logo ? `<img alt="" src="${esc(j.logo)}">` : "";
    const checks = [
      st.waiting && c.playing ? ["game", "is-warn", "i-dot", "Waiting for its turn", "Another tester is pressing keys"]
        : c.playing ? ["game", "is-ok", "i-check", kind === "fortnite" ? "In the island" : `${game} is open`, kind === "fortnite" ? esc(st.island || "") : "Ready to play test"]
        : c.loading ? ["game", "is-warn", "i-dot", `${game} is loading`, kind === "fortnite" ? "Wait for the island" : "Wait a moment"]
        : ["game", "is-bad", "i-x", esc(plainErr(c.title) || `${gameName(st)} isn't open`), esc(plainErr(c.fix))],
      !j.key ? ["jev", "is-bad", "i-x", "No model reads the frames", "Add a TypeSafe key in Settings → LLMs, or pick a model above"]
        : ["jev", "is-ok", "i-check", esc(j.name || "Jev"), `${esc(j.via || "")}${j.box ? "" : `${j.via ? " · " : ""}no box`}`, logo],
      kind === "fortnite"
        ? (b.on ? ["positions", "is-ok", "i-pin", "Beacon on", "World positions"]
          : b.seen ? ["positions", "is-warn", "i-pin", "Beacon quiet", `No position for ${Math.round(b.age_s || 0)} s`]
          : ["positions", "is-warn", "i-pin", "Beacon off", "Steers by the picture"])
        : t.positions ? ["positions", "is-ok", "i-pin", "Node positions", "From the player node"]
        : ["positions", "is-info", "i-eye", "By the picture", "No world positions in this game"],
    ];
    $("ready").innerHTML = checks.map(([key, cls, ic, name, detail, pic]) => `<div class="pt-check ${cls}"><span class="pt-check__icon">${pic || icon(ic)}</span>
      <span class="pt-check__text"><span class="pt-check__name" title="${name}">${name}</span>${detail ? `<span class="pt-check__detail" title="${detail}">${detail}</span>` : ""}</span>
      <button type="button" class="pt-help" popovertarget="help-${key}" aria-label="What is this?">?</button></div>`).join("");
    paintModel(j);
    // Keys and the dial press into the game: only when it is open and no run is playing.
    const off = !c.open || !!st.active_run;
    $("keys").closest(".pt-pad").classList.toggle("is-off", off);
    for (const b of document.querySelectorAll(".pt-key, #dial")) {
      b.disabled = off;
      b.title = off ? (c.open ? "A run is playing" : `Open ${gameName(st)} first`) : b.dataset.tip || "";
    }
  }

  async function refreshStatus() {
    try {
      const st = await trpc("status");
      if (st && st.ok === false && /No tester/.test(st.error || "")) {
        await switchTester("default");  // its tester was closed (here, by an agent or in another window)
        return;
      }
      state.status = st;
      if (Array.isArray(st.testers)) { state.testers = st.testers; renderTabs(); }
      paintReady(st);
      paintBrief();
      if (!state.steps.length) select(0);
    } catch (err) {
      showErr(err.message);
    }
  }

  // ------------------------------------------------------------------ tester tabs

  function tabDot(t) {
    if (t.waiting) return "is-wait";
    if (t.running) return "is-run";
    const last = (t.last && t.last.status) || "";
    return ["success", "done"].includes(last) ? "is-good" : ["failed", "blocked", "timeout"].includes(last) ? "is-bad" : "";
  }

  // Rebuilt only when a tab changed: a rebuild between mouse down and up would swallow a click on a tab or its x.
  function renderTabs() {
    const wrap = $("tabs");
    const rows = state.testers.length ? state.testers : [{ id: "default", label: "Fortnite", default: true, target: { kind: "fortnite" } }];
    const html = rows.map((t) => {
      const on = t.id === state.tester;
      const kind = (t.target && t.target.kind) || "window";
      const fresh = state.news.has(t.id) && !on ? " is-new" : "";
      return `<div class="pt-tab" role="tab" tabindex="${on ? 0 : -1}" aria-selected="${on}" data-tester="${esc(t.id)}" title="${esc(t.label)}${t.target && t.target.exe ? ` (${esc(t.target.exe)})` : ""}">
        <svg class="ico"><use href="#${KIND_ICON[kind] || "i-window"}"/></svg>
        <span class="pt-tab__name">${esc(t.label)}</span>
        ${t.waiting ? `<span class="pt-tab__wait">waiting</span>` : ""}
        <span class="pt-tab__dot ${tabDot(t)}${fresh}"></span>
        ${t.default ? "" : `<button type="button" class="pt-tab__x" data-close="${esc(t.id)}" aria-label="Close ${esc(t.label)}" title="Close ${esc(t.label)}"><svg class="ico"><use href="#i-x"/></svg></button>`}
      </div>`;
    }).join("");
    if (html !== state.tabsHtml) {
      state.tabsHtml = html;
      wrap.innerHTML = html;
      const on = wrap.querySelector('[aria-selected="true"]');
      if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
      paintTabsThumb();
    }
  }

  // The strip's own scrollbar: a thin thumb over its bottom edge (CSS shows it while hovered).
  function paintTabsThumb() {
    const bar = $("tabs"), track = $("tabsTrack"), thumb = $("tabsThumb");
    const over = bar.scrollWidth > bar.clientWidth + 1;
    track.classList.toggle("is-on", over);
    if (!over) return;
    const width = Math.max(24, (bar.clientWidth / bar.scrollWidth) * bar.clientWidth);
    const left = (bar.scrollLeft / (bar.scrollWidth - bar.clientWidth)) * (bar.clientWidth - width);
    thumb.style.width = `${width}px`;
    thumb.style.transform = `translateX(${left}px)`;
  }

  function wireTabsScroll() {
    const bar = $("tabs"), thumb = $("tabsThumb");
    bar.addEventListener("scroll", paintTabsThumb, { passive: true });
    new ResizeObserver(paintTabsThumb).observe(bar);
    bar.addEventListener("wheel", (ev) => {
      if (ev.ctrlKey || bar.scrollWidth <= bar.clientWidth + 1) return;
      ev.preventDefault();
      bar.scrollLeft += Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : ev.deltaY;
    }, { passive: false });
    let drag = null;
    thumb.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      drag = { x: ev.clientX, left: bar.scrollLeft };
      thumb.setPointerCapture(ev.pointerId);
      thumb.classList.add("is-drag");
    });
    thumb.addEventListener("pointermove", (ev) => {
      if (!drag) return;
      const room = bar.clientWidth - thumb.offsetWidth;
      bar.scrollLeft = drag.left + (ev.clientX - drag.x) * ((bar.scrollWidth - bar.clientWidth) / Math.max(1, room));
    });
    const end = () => { drag = null; thumb.classList.remove("is-drag"); };
    thumb.addEventListener("pointerup", end);
    thumb.addEventListener("pointercancel", end);
  }

  async function switchTester(id) {
    if (!id) return;
    state.tester = id;
    remember("pt.tester", id);
    state.news.delete(id);
    state.status = null;
    showErr("");  // an error belongs to the tab it happened in
    setFollow(true);
    renderTabs();
    await refreshStatus();
    const run = await loadRuns("");
    await openRun(run);
  }

  async function closeTester(id) {
    const t = state.testers.find((x) => x.id === id);
    try {
      const ok = await call("ui.confirm", { title: "Close tester", message: `Close ${t ? t.label : id}? Its run stops and its runs are forgotten.`, confirm: "Close", danger: true });
      if (!ok || !ok.confirmed) return;
      const out = await rpc("close", { tester: id });
      if (out && out.ok === false) return showErr(out.error);
      state.testers = (out && out.testers) || state.testers.filter((x) => x.id !== id);
      if (state.tester === id) await switchTester("default");
      else renderTabs();
    } catch (err) {
      showErr(err.message);
    }
  }

  function optRow(pic, name, sub, data) {
    const attrs = Object.entries(data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ");
    return `<button type="button" class="pt-opt" ${attrs}><span class="pt-opt__pic">${pic}</span>
      <span class="pt-opt__text"><span class="pt-opt__name">${esc(name)}</span><span class="pt-opt__sub">${esc(sub)}</span></span></button>`;
  }

  async function openNewMenu() {
    const menu = $("newMenu");
    const r = $("addTester").getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 392))}px`;
    menu.style.top = `${r.bottom + 4}px`;
    $("newWeb").classList.add("pt-hidden");
    $("newWindows").innerHTML = `<div class="pt-note">Looking for open game windows…</div>`;
    menu.showPopover();
    try {
      const out = await rpc("targets", { thumbnails: true });
      const taken = new Set(state.testers.map((t) => (t.target && t.target.kind) === "fortnite" ? "fortnite" : ""));
      $("newPresets").innerHTML = (out.presets || []).filter((p) => !(p.kind === "fortnite" && taken.has("fortnite")))
        .map((p) => optRow(icon(KIND_ICON[p.kind] || "i-pad"), p.label, p.hint || "", { target: p.target, web: p.needs_url ? "1" : "" })).join("")
        || `<div class="pt-note">Every preset is open.</div>`;
      // A game that already has a tab goes to that tab; the Fortnite client or Roblox opens as itself.
      const wins = out.windows || [];
      const labelOf = (id) => ((state.testers.find((t) => t.id === id) || {}).label || "its tab");
      $("newWindows").innerHTML = wins.length
        ? wins.map((w) => optRow(w.thumb ? `<img alt="" src="${w.thumb}">` : icon(KIND_ICON[w.kind] || "i-window"), w.title,
          w.tester ? `Open in ${labelOf(w.tester)}` : `${w.exe || ""} · ${w.width}×${w.height}`,
          w.tester ? { tester: w.tester } : { target: w.target || `window:${w.exe || w.title}`, label: w.kind === "window" ? w.title : "" })).join("")
        : `<div class="pt-note">No game windows are open. Start the game, then press + again.</div>`;
    } catch (err) {
      $("newWindows").innerHTML = `<div class="pt-note">${esc(err.message)}</div>`;
    }
  }

  async function openTester(target, label) {
    try {
      const out = await rpc("open", { target, label: label || "", profile: $("newProfile").value });
      if (!out || out.ok === false) return showErr((out && out.error) || "Could not open the tester.");
      $("newMenu").hidePopover();
      showErr("");
      await switchTester(out.tester.id);
    } catch (err) {
      showErr(err.message);
    }
  }

  // An error in words: a tool's JSON (even JSON inside JSON) gives its message, and sentences
  // that tell agents which tool to call are left out.
  function plainErr(msg) {
    let text = String(msg || "").trim();
    for (let i = 0; i < 4 && /^[[{]/.test(text); i++) {
      try {
        const d = JSON.parse(text);
        const inner = d && typeof d === "object" ? d.error || d.message : "";
        if (!inner) break;
        text = String(inner).trim();
      } catch (_) {
        break;
      }
    }
    const said = text.split(/(?<=[.!?])\s+/).filter((s) => !/\b[a-z]+_[a-z_]+\s*\(|\bplaytester_[a-z_]+\b/.test(s));
    return (said.join(" ") || text).replace(/[:\s]+$/, "");
  }

  function showErr(msg) {
    const text = plainErr(msg);
    $("errText").textContent = text;
    $("err").classList.toggle("pt-hidden", !text);
  }

  function setFollow(on) {
    state.follow = on;
    $("follow").setAttribute("aria-pressed", on ? "true" : "false");
  }

  // ------------------------------------------------------------------ live

  // Events from other testers only light up their tab.
  function mine(ev) {
    const from = ev.tester || "default";
    if (from === state.tester) return true;
    const t = state.testers.find((x) => x.id === from);
    if (t) {
      state.news.add(from);
      if (ev.type === "playtester_run" && ev.run) {
        t.running = ev.run.status === "running" ? ev.run.id : null;
        t.last = { id: ev.run.id, status: ev.run.status, goal: ev.run.goal };
      } else if (ev.type === "playtester_step") {
        t.running = t.running || ev.run_id;
      }
      renderTabs();
    } else {
      refreshStatus();  // a tester opened elsewhere
    }
    return false;
  }

  function onStep(ev) {
    if (!mine(ev)) return;
    if (ev.thumb) state.thumbs.set(`${ev.run_id}:${ev.step.n}`, ev.thumb);
    if (ev.run_id !== state.runId) {
      if (state.follow && ev.run_id !== "manual") loadRuns(ev.run_id).then(openRun);
      else if (state.follow && ev.run_id === "manual" && !running()) loadRuns("manual").then(openRun);
      return;
    }
    if (state.run) {
      state.run.n_steps = ev.n_steps;
      state.run.status = ev.status;
    }
    addStep(ev.step);
    paintBrief();
  }

  function onRun(ev) {
    if (!mine(ev)) return;
    const r = ev.run || {};
    if (r.id === state.runId) {
      state.run = { ...(state.run || {}), ...r };
      paintBrief();
      showOverlay(state.steps.find((s) => s.n === state.selected) || null);  // the Live chip goes when it ends
      if (DONE.includes(r.status)) {
        loadRuns(state.runId);
        refetchRun();  // the finished doc has the end, the totals and the full log
      }
    } else if (state.follow && r.status === "running") {
      loadRuns(r.id).then(openRun);
    } else {
      loadRuns(state.runId);
    }
    refreshStatus();
  }

  async function refetchRun() {
    const last = state.steps.length ? state.steps[state.steps.length - 1].n : 0;
    try {
      const out = await trpc("get_run", { run_id: state.runId, from_step: last });
      if (out && out.ok) {
        const { steps, ...meta } = out.run;
        state.run = { ...(state.run || {}), ...meta };
        for (const s of steps || []) addStep(s);
        paintBrief();
        paintOrders();
      }
    } catch (_) { /* next tick */ }
  }

  async function poll() {
    if (!state.visible) return;
    const now = Date.now();
    if (running() && state.runId && now - state.lastPoll > 1400) {
      state.lastPoll = now;
      await refetchRun();
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

  // ------------------------------------------------------------------ controls

  $("stopBtn").addEventListener("click", async () => {
    try {
      const out = await trpc("stop", { run_id: running() ? state.runId : "" });
      if (out && out.ok === false) showErr(out.error);
    } catch (err) {
      showErr(err.message);
    }
  });

  $("runPick").addEventListener("change", (ev) => { setFollow(false); openRun(ev.target.value); });
  // Ducky's own model picker, dropped down from the logo button like the chat's.
  $("brainPick").addEventListener("click", async () => {
    try {
      const j = (state.status && state.status.jev) || {};
      const r = $("brainPick").getBoundingClientRect();
      const anchor = { left: r.left, top: r.top, width: r.width, height: r.height };
      const out = await call("ui.pickModel", { purpose: "video", current: j.now || j.pick || "", title: "The model that reads the frames",
                                               extra: jevRows(j.plan), hide: ["uefn_ducky"], anchor });
      if (!out || !out.model) return;
      const st = await trpc("set_brain", { pick: out.model });
      if (st && st.ok === false && st.error) return showErr(st.error);
      state.status = st;
      paintReady(st);
    } catch (err) {
      showErr(err.message);
    }
  });
  $("errX").addEventListener("click", () => showErr(""));
  $("tabs").addEventListener("click", (ev) => {
    const x = ev.target.closest("[data-close]");
    if (x) { ev.stopPropagation(); closeTester(x.dataset.close); return; }
    const tab = ev.target.closest("[data-tester]");
    if (tab && tab.dataset.tester !== state.tester) switchTester(tab.dataset.tester);
  });
  $("tabs").addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    const ids = state.testers.map((t) => t.id);
    const i = ids.indexOf(state.tester) + (ev.key === "ArrowRight" ? 1 : -1);
    if (ids[i]) { ev.preventDefault(); switchTester(ids[i]).then(() => $("tabs").querySelector('[aria-selected="true"]')?.focus()); }
  });
  $("addTester").addEventListener("click", openNewMenu);
  $("newMenu").addEventListener("click", (ev) => {
    const opt = ev.target.closest(".pt-opt");
    if (!opt) return;
    if (opt.dataset.tester) {
      $("newMenu").hidePopover();
      switchTester(opt.dataset.tester);
      return;
    }
    if (opt.dataset.web) {
      $("newWeb").classList.remove("pt-hidden");
      $("newUrl").focus();
      return;
    }
    openTester(opt.dataset.target, opt.dataset.label);
  });
  $("newWeb").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const url = $("newUrl").value.trim();
    if (url) openTester(`web:${url}`, "");
  });
  $("follow").addEventListener("click", () => {
    setFollow(!state.follow);
    if (state.follow) loadRuns("").then(openRun);
  });
  $("ovTR").addEventListener("click", (ev) => {
    if (!ev.target.closest("[data-live]")) return;
    setFollow(true);
    const last = state.steps[state.steps.length - 1];
    if (last) select(last.n);
  });
  $("ordersBtn").addEventListener("click", () => { state.orders = !state.orders; paintBrief(); paintOrders(); });
  $("orderTabs").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-tab]");
    if (b) { state.tab = b.dataset.tab; paintOrders(); }
  });
  $("stepBtn").addEventListener("click", () => {
    state.stepOpen = !state.stepOpen;
    showStepData(state.steps.find((s) => s.n === state.selected) || null);
  });
  $("prev").addEventListener("click", () => move(-1));
  $("next").addEventListener("click", () => move(1));
  $("film").addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowLeft") { ev.preventDefault(); move(-1); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); move(1); }
  });

  // ------------------------------------------------------------------ start

  async function boot() {
    buildKeys();
    wireTabsScroll();
    showPressing(null);
    showAnswers(null);
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
