/* Checksum — client.
 *
 * The server streams one event per reading as its query returns, so the page paints
 * the verification while it is happening. The stagger you see is real query timing;
 * nothing here is on a timer pretending to be work.
 */

const el = (id) => document.getElementById(id);

/* An element carrying text, never markup. Warehouse values reach this page as
   data -- on the public GitHub dataset, repository names are written by strangers
   -- so nothing that came back from a query is ever interpolated into HTML. */
function field(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

/* Gemini answers in markdown, so a raw **title** reaches the page with its
   asterisks showing. Rendering the markdown as HTML would put model output into
   the DOM as markup; instead only bold is honoured, and only by building nodes.
   An unbalanced run of asterisks falls back to plain text rather than bolding
   everything after it. */
function writeText(node, text) {
  node.textContent = "";
  const parts = String(text).split("**");
  if (parts.length % 2 === 0) {
    node.textContent = text;
    return;
  }
  parts.forEach((part, i) => {
    if (!part) return;
    node.appendChild(i % 2 ? field("strong", "", part) : document.createTextNode(part));
  });
}

/* --------------------------------------------------------------- sql tinting */

/* Eight full queries sit on this page at once, because hiding SQL behind an
   accordion would undercut the whole claim. Tinting them is what keeps that from
   reading as a wall of grey: keywords, strings and numbers carry the shape of a
   query, so two readings can be compared at a glance. The text is untouched --
   only wrapped -- and the copy button still copies the original string. */
/* Two-word forms come first: "GROUP BY" must win over a bare "GROUP". */
const SQL_PHRASES = "GROUP\\s+BY|ORDER\\s+BY|PARTITION\\s+BY|LEFT\\s+JOIN|INNER\\s+JOIN";
const SQL_WORDS =
  "SELECT|FROM|PREWHERE|WHERE|HAVING|LIMIT|OFFSET|AND|OR|NOT|AS|IN|ON|JOIN|WITH|" +
  "CASE|WHEN|THEN|ELSE|END|DESC|ASC|UNION|ALL|DISTINCT|BETWEEN|IS|NULL|INTERVAL|" +
  "OVER|USING|SETTINGS|ARRAY";

const SQL_TOKENS = new RegExp(
  "(--[^\\n]*)" +                              // 1 comment to end of line
  "|('(?:[^']|'')*')" +                        // 2 string literal
  `|\\b(${SQL_PHRASES})\\b` +                  // 3 two-word keyword
  `|\\b(${SQL_WORDS})\\b` +                    // 4 keyword
  "|(\\b\\d+(?:\\.\\d+)?\\b)" +                // 5 number
  "|([A-Za-z_][A-Za-z0-9_]*)(?=\\s*\\()",      // 6 function call
  "gi");

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };
const esc = (s) => s.replace(/[&<>]/g, (c) => ESCAPES[c]);

function highlightSql(sql) {
  const classes = ["t-com", "t-str", "t-kw", "t-kw", "t-num", "t-fn"];
  let out = "";
  let last = 0;
  let match;
  SQL_TOKENS.lastIndex = 0;
  while ((match = SQL_TOKENS.exec(sql)) !== null) {
    out += esc(sql.slice(last, match.index));
    const group = classes.findIndex((_, g) => match[g + 1] !== undefined);
    out += `<span class="${classes[group]}">${esc(match[0])}</span>`;
    last = match.index + match[0].length;
  }
  return out + esc(sql.slice(last));
}

/* The rank chart is drawn when the reader reaches it, not when the data lands,
   so the observer and its fallback timer outlive a single render and have to be
   torn down before the next one. */
let slopeWatch = null;
let slopeTimer = 0;

const state = {
  running: false,
  dataset: "",
  landed: 0,
  readings: [],
  receipts: [],
  policyLeader: "",
  naiveLeader: "",
};

/* ------------------------------------------------------------------ presets */

async function loadPresets() {
  try {
    const [presets, health] = await Promise.all([
      fetch("/api/presets").then((r) => r.json()),
      fetch("/api/health").then((r) => r.json()),
    ]);
    const cached = new Set(health.cached || []);
    const box = el("presets");
    box.innerHTML = "";

    // Grouped by dataset, because which warehouse a question runs against is the
    // point rather than a detail: one was built here, the other was not.
    const groups = new Map();
    presets.presets.forEach((p) => {
      if (!groups.has(p.dataset)) groups.set(p.dataset, { label: p.label, items: [] });
      groups.get(p.dataset).items.push(p);
    });

    groups.forEach(({ label, items }, key) => {
      const group = document.createElement("div");
      group.className = "preset-group";
      const name = document.createElement("span");
      name.className = "preset-group-name";
      name.textContent = label;
      group.appendChild(name);

      const row = document.createElement("div");
      row.className = "preset-group-items";
      group.appendChild(row);

      items.forEach((p) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "preset";
        b.textContent = p.question;
        if (cached.has(`${p.dataset}::${p.question}`)) b.dataset.cached = "1";
        b.addEventListener("click", () => {
          el("question").value = p.question;
          state.dataset = p.dataset;
          run(p.question, p.dataset);
        });
        row.appendChild(b);
      });
      box.appendChild(group);
    });
  } catch {
    /* Presets are a convenience; the input still works without them. */
  }
}

/* -------------------------------------------------------------------- reset */

function reset() {
  state.landed = 0;
  state.readings = [];
  state.receipts = [];
  el("readings").innerHTML = "";
  el("panel-standard").querySelector(".panel-body").innerHTML =
    '<p class="idle">Querying…</p>';
  el("verdict").hidden = true;
  el("explanation").hidden = true;
  if (slopeWatch) { slopeWatch.disconnect(); slopeWatch = null; }
  clearTimeout(slopeTimer);
  el("movement").classList.remove("is-armed", "is-drawn");
  el("movement").hidden = true;
  el("receipts-section").hidden = true;
  el("scope-banner").hidden = true;
  el("landed").textContent = "0";
  document.querySelectorAll(".panel-foot").forEach((f) => (f.hidden = true));
}

/* --------------------------------------------------------------- rendering */

function renderScope(ev) {
  const banner = el("scope-banner");
  banner.innerHTML = "";
  const name = document.createElement("b");
  name.textContent = ev.dataset_label;
  banner.appendChild(name);
  banner.appendChild(document.createTextNode(` — ${ev.blurb}`));
  if (!ev.has_policy) {
    const note = document.createElement("i");
    note.textContent =
      " No metric policy exists here, so Checksum can report disagreement but " +
      "cannot name a definition as correct.";
    banner.appendChild(note);
  }
  banner.hidden = false;
}

/* The free Gemini tier allows twenty requests a day. When it is spent, the
   verification still runs -- it never needed a model -- so say so plainly rather
   than showing a broken page. */
function renderModelDown(ev) {
  const target =
    ev.where === "adjudicator"
      ? el("panel-checksum")
      : el("panel-standard");
  // Drop the "Querying…" placeholder; nothing is going to replace it.
  const idle = target.querySelector(".idle");
  if (idle) idle.remove();
  const box = document.createElement("div");
  box.className = "audit";
  box.innerHTML = "<b>Model quota spent</b>";
  const p = document.createElement("p");
  p.style.margin = "0";
  p.textContent =
    ev.where === "adjudicator"
      ? "The written summary needs Gemini and the daily free-tier quota is used up. " +
        "The verdict below was computed from the numbers, not written by a model, so " +
        "it stands unchanged."
      : "The standard agent needs Gemini and the daily free-tier quota is used up. " +
        "The verification on the right does not use a model at all — it still runs.";
  box.appendChild(p);
  (target.querySelector(".panel-body") || target).appendChild(box);
}

function renderNaive(ev) {
  if (!ev.answer) return;
  const body = el("panel-standard").querySelector(".panel-body");
  body.innerHTML = "";
  const p = document.createElement("p");
  p.className = "answer";
  writeText(p, ev.answer);
  body.appendChild(p);

  el("naive-time").textContent = ev.seconds;
  el("naive-queries").textContent = (ev.queries || []).length;
  el("panel-standard").querySelector(".panel-foot").hidden = false;
}

function renderAudit(ev) {
  if (!ev.note) return;
  const body = el("panel-standard").querySelector(".panel-body");
  const box = document.createElement("div");
  box.className = "audit";
  box.innerHTML = `<b>What it actually ran</b>`;
  const note = document.createElement("p");
  note.style.margin = "0";
  note.textContent = ev.note;
  box.appendChild(note);
  body.appendChild(box);
}

function renderReading(ev) {
  state.landed += 1;
  state.readings.push(ev);
  el("landed").textContent = String(state.landed);

  const li = document.createElement("li");
  li.className = "reading";
  if (ev.key === "completion_90") li.classList.add("is-policy");
  if (ev.category === "guard") li.classList.add("is-guard");
  if (!ev.ok) li.classList.add("is-error");
  li.dataset.key = ev.key;

  const right = ev.ok
    ? (ev.category === "guard" ? "checked" : ev.leader || "—")
    : "failed";

  // Built as nodes, not interpolated markup: the leader is a title or a repository
  // name that came out of the warehouse, and on the public GitHub dataset that
  // string is whatever a stranger named their repo.
  const index = document.createElement("span");
  index.className = "reading-index";
  index.textContent = String(state.landed).padStart(2, "0");

  const name = document.createElement("span");
  name.className = "reading-name";
  name.textContent = ev.title;
  const cat = document.createElement("span");
  cat.className = "reading-cat";
  cat.textContent = ev.category;
  name.appendChild(cat);

  const leader = document.createElement("span");
  leader.className = "reading-leader";
  leader.textContent = right;

  li.append(index, name, leader);

  li.querySelector(".reading-name").addEventListener("click", () => {
    const target = document.querySelector(`.receipt[data-key="${ev.key}"]`);
    if (!target) return;
    document.querySelectorAll(".receipt").forEach((r) => r.classList.remove("is-target"));
    target.classList.add("is-target");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  el("readings").appendChild(li);
}

function renderVerdict(ev) {
  state.policyLeader = ev.policy_leader || "";
  state.naiveLeader = ev.naive_leader || "";

  const box = el("verdict");
  box.className = "verdict";
  if (ev.stable) box.classList.add("is-stable");
  if (ev.refused) box.classList.add("is-refused");
  el("verdict-head").textContent = ev.headline;
  el("verdict-detail").textContent = ev.detail;
  box.hidden = false;

  el("checksum-time").textContent = ev.seconds;
  el("checksum-queries").textContent = state.readings.length;
  el("panel-checksum").querySelector(".panel-foot").hidden = false;

  renderMovement(ev);
}

/* The surprise: one title's rank walking across the readings. Everything else on
   the page is text; this is the only place the disagreement is a shape. */
function renderMovement(ev) {
  const displacement = ev.displacement || {};
  const mover = Object.entries(displacement).sort((a, b) => b[1] - a[1])[0];
  if (!mover || mover[1] < 2) return;

  const [title] = mover;
  const rankings = state.receipts.length
    ? state.receipts.filter((r) => r.ranking)
    : [];
  if (!rankings.length) return;

  el("movement-note").textContent =
    `${title} does not hold still. Each row is one defensible reading of the same ` +
    `question; the dot is where ${title} lands under it.`;

  const movement = el("movement");
  const slope = el("slope");
  slope.innerHTML = "";

  // renderMovement can run twice -- once on the verdict and again when the
  // receipts arrive -- so the previous run's observer must go before a second
  // one starts watching the same section.
  if (slopeWatch) { slopeWatch.disconnect(); slopeWatch = null; }
  clearTimeout(slopeTimer);
  movement.classList.remove("is-drawn");
  movement.classList.add("is-armed");
  // renderMovement runs again once the receipts arrive, so clear the previous
  // legend rather than stacking a second one under the chart.
  el("movement").querySelectorAll(".slope-legend").forEach((n) => n.remove());

  // Ordered by where the title lands, so the drop reads as a cliff rather than as
  // an arbitrary list. Arrival order is query timing and means nothing here.
  const ordered = rankings
    .map((r) => ({ r, rank: r.ranking.indexOf(title) + 1 }))
    .filter((x) => x.rank > 0)
    .sort((a, b) => a.rank - b.rank);
  if (!ordered.length) return;

  const widest = Math.max(...ordered.map(({ r }) => r.ranking.length));
  const at = ({ r, rank }) => ((rank - 1) / Math.max(1, r.ranking.length - 1)) * 100;

  // One gridline per rank position, so a dot lands on a tick instead of adrift.
  slope.style.setProperty("--slope-tick", `${100 / Math.max(1, widest - 1)}%`);

  el("slope-scale").innerHTML = "";
  const scaleEnds = document.createElement("span");
  scaleEnds.className = "slope-scale-ends";
  const best = document.createElement("span");
  best.textContent = "rank 1";
  const worst = document.createElement("span");
  worst.className = "slope-scale-right";
  worst.textContent = `rank ${widest}`;
  scaleEnds.append(best, worst);
  el("slope-scale").append(document.createElement("span"), scaleEnds,
                           document.createElement("span"));

  // The line joining the dots is what makes this a chart rather than a list of
  // rows. It is positioned against the same track the dots sit in, in percentages,
  // so nothing has to be measured and it survives a resize on its own.
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "slope-line");
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", ordered
    .map((o, i) => `${at(o)},${((i + 0.5) / ordered.length) * 100}`)
    .join(" "));
  svg.appendChild(line);
  const lane = document.createElement("div");
  lane.className = "slope-lane";
  lane.appendChild(svg);
  slope.appendChild(lane);

  const pending = [];

  ordered.forEach((o, i) => {
    const { r, rank } = o;
    const row = document.createElement("div");
    row.className = "slope-row";
    // Staggered against the wipe above, so each dot arrives as the line reaches
    // its row rather than all nine snapping across at once.
    row.style.setProperty("--draw-delay", `${((i / ordered.length) * 0.95).toFixed(3)}s`);
    if (r.key === "completion_90") row.classList.add("is-mover");

    const label = document.createElement("span");
    label.className = "slope-label";
    label.textContent = r.title;

    const track = document.createElement("span");
    track.className = "slope-track";
    const dot = document.createElement("span");
    dot.className = "slope-dot";
    dot.style.left = "0%";
    track.appendChild(dot);

    const rankLabel = document.createElement("span");
    rankLabel.className = "slope-rank";
    rankLabel.textContent = `#${rank}`;

    row.append(label, track, rankLabel);
    slope.appendChild(row);
    pending.push([dot, `${at(o)}%`]);
  });

  // State the size of the fall in words. A reader should not have to subtract two
  // rank labels to see the point of the chart.
  const legend = document.createElement("p");
  legend.className = "slope-legend";
  const span = document.createElement("span");
  const first = ordered[0].rank;
  const last = ordered[ordered.length - 1].rank;
  span.append(document.createTextNode(`${title} — `));
  const drop = document.createElement("b");
  drop.textContent = `#${first} → #${last}`;
  span.append(drop, document.createTextNode(
    ` across ${ordered.length} readings of one question`));
  legend.appendChild(span);
  slope.after(legend);

  movement.hidden = false;   // observable only once it has a box to intersect

  const draw = () => {
    if (slopeWatch) { slopeWatch.disconnect(); slopeWatch = null; }
    clearTimeout(slopeTimer);
    movement.classList.add("is-drawn");
    pending.forEach(([dot, left]) => { dot.style.left = left; });
  };

  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (still || !("IntersectionObserver" in window)) {
    requestAnimationFrame(draw);
    return;
  }

  // A bottom inset rather than a threshold: on a phone this section is taller
  // than the viewport, so "a quarter of it is visible" can never become true.
  slopeWatch = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) draw();
  }, { threshold: 0, rootMargin: "0px 0px -15% 0px" });
  slopeWatch.observe(movement);

  // Chromium's full-page screenshot captures beyond the viewport without
  // scrolling, so it never trips the observer and would photograph an empty
  // chart. Draw regardless after a beat -- by then a reader who is here has
  // already watched it move, and one who is not has lost nothing.
  slopeTimer = setTimeout(draw, 8000);
}

function renderReceipts(items) {
  state.receipts = items;
  const list = el("receipts");
  list.innerHTML = "";

  items.forEach((item, i) => {
    const box = document.createElement("article");
    box.className = "receipt";
    box.dataset.key = item.key;

    const top = document.createElement("div");
    top.className = "receipt-top";
    // The index ties a receipt back to its row in the readings list above.
    top.append(
      field("span", "receipt-index", String(i + 1).padStart(2, "0")),
      field("span", "receipt-name", item.title),
      field("span", "receipt-cat", item.category),
      field("span", "receipt-rows", item.ok ? `${item.row_count} rows` : "failed"),
    );
    box.appendChild(top);

    if (item.rationale) {
      const why = document.createElement("p");
      why.className = "receipt-why";
      why.textContent = item.rationale;
      box.appendChild(why);
    }
    if (item.policy_source) {
      const src = document.createElement("p");
      src.className = "receipt-source";
      src.textContent = item.policy_source;
      box.appendChild(src);
    }

    const sql = document.createElement("pre");
    sql.className = "sql";
    sql.innerHTML = highlightSql(item.sql);
    sql.title = "Click to copy";
    sql.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(item.sql);
        sql.dataset.copied = "1";
        setTimeout(() => delete sql.dataset.copied, 1400);
      } catch {
        /* Clipboard can be blocked; the SQL is on screen either way. */
      }
    });
    box.appendChild(sql);

    if (item.ranking && item.ranking.length) {
      const rows = document.createElement("div");
      rows.className = "receipt-top-rows";
      item.rows.slice(0, 3).forEach((row, n) => {
        const span = document.createElement("span");
        // On the public GitHub dataset these names are whatever a stranger called
        // their repository, so they are set as text and never as markup.
        span.appendChild(field("span", "", `${n + 1}. ${item.ranking[n]}`));
        span.appendChild(field("b", "", Number(row[1]).toLocaleString()));
        rows.appendChild(span);
      });
      box.appendChild(rows);
    }

    list.appendChild(box);
  });

  el("receipts-section").hidden = false;
  if (state.pendingVerdict) renderMovement(state.pendingVerdict);
}

/* ---------------------------------------------------------------- streaming */

async function run(question, dataset = "") {
  if (state.running) return;
  state.running = true;
  reset();

  const button = el("run");
  button.disabled = true;
  button.querySelector(".run-label").textContent = "Verifying";
  button.querySelector(".run-count").hidden = false;

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, dataset }),
    });
    if (!response.ok) throw new Error(`server said ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      // sse-starlette terminates lines with CRLF, so the event separator on the
      // wire is "\r\n\r\n". Splitting on "\n\n" matched nothing and silently
      // discarded every event -- the page sat on "Querying…" while the server had
      // already sent the whole verification.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        handle(JSON.parse(line.slice(6)));
      }
    }
  } catch (err) {
    el("panel-standard").querySelector(".panel-body").innerHTML =
      `<p class="idle">Could not reach the warehouse: ${err.message}</p>`;
  } finally {
    state.running = false;
    button.disabled = false;
    button.querySelector(".run-label").textContent = "Verify";
    button.querySelector(".run-count").hidden = true;
    loadPresets();
  }
}

function handle(ev) {
  switch (ev.type) {
    case "scope":        return renderScope(ev);
    case "model_unavailable": return renderModelDown(ev);
    case "naive_done":   return renderNaive(ev);
    case "naive_audit":  return renderAudit(ev);
    case "reading":      return renderReading(ev);
    case "verdict":      state.pendingVerdict = ev; return renderVerdict(ev);
    case "explanation": {
      const box = el("explanation");
      writeText(box, ev.text);
      box.hidden = false;
      return;
    }
    case "receipts":     return renderReceipts(ev.items);
    default:             return;
  }
}

/* ---------------------------------------------------------- running head */

/* Wayfinding for a document that does not fit on a screen. Each entry is a
   section, its clause number, and the short form of its title -- short because
   this lands in a 0.7rem bar with 0.18em of tracking, next to the mark, on a
   390px phone. */
const RUNHEAD = [
  ["ask",              "01", "The question"],
  ["stage",            "02", "Both agents"],
  ["movement",         "04", "Movement"],
  ["receipts-section", "05", "Receipts"],
];

function mountRunhead() {
  const slot = el("runhead");
  if (!slot || !("IntersectionObserver" in window)) return;
  const masthead = slot.textContent;   // what the bar says at the top of the page

  const here = new Map();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => here.set(e.target.id, e.isIntersecting));

    // First match wins, in document order: with two sections crossing the band
    // the reader is in the upper one, and the head should not flicker to
    // whichever entry the observer happened to report last.
    const at = RUNHEAD.find(([id]) => here.get(id));

    slot.innerHTML = "";
    slot.classList.toggle("is-section", Boolean(at));
    if (!at) { slot.textContent = masthead; return; }
    const clause = document.createElement("span");
    clause.className = "runhead-clause";
    clause.textContent = at[1];
    slot.append(clause, document.createTextNode(" " + at[2]));
  },
  // A band across the upper third of the viewport: a section counts as the one
  // being read once it reaches roughly where the eye is, not when its first
  // pixel shows up at the bottom of the screen.
  { rootMargin: "-22% 0px -62% 0px" });

  RUNHEAD.forEach(([id]) => {
    const node = document.getElementById(id);
    if (node) io.observe(node);
  });
}

mountRunhead();

/* -------------------------------------------------------------------- boot */

el("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = el("question").value.trim();
  if (q) run(q, state.dataset);
});

/* Deep link: /?q=<question>&auto=1 . Lets the demo recording start mid-flight
   instead of opening on a form, and makes a specific run shareable. */
const params = new URLSearchParams(location.search);
if (params.get("q")) el("question").value = params.get("q");
loadPresets().then(() => {
  if (params.get("auto")) run(el("question").value.trim());
});
