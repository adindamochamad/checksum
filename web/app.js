/* Checksum — client.
 *
 * The server streams one event per reading as its query returns, so the page paints
 * the verification while it is happening. The stagger you see is real query timing;
 * nothing here is on a timer pretending to be work.
 */

const el = (id) => document.getElementById(id);

const state = {
  running: false,
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
    el("presets").innerHTML = "";
    presets.questions.forEach((q) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "preset";
      b.textContent = q;
      if (cached.has(q)) b.dataset.cached = "1";
      b.addEventListener("click", () => {
        el("question").value = q;
        run(q);
      });
      el("presets").appendChild(b);
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
  el("movement").hidden = true;
  el("receipts-section").hidden = true;
  el("landed").textContent = "0";
  document.querySelectorAll(".panel-foot").forEach((f) => (f.hidden = true));
}

/* --------------------------------------------------------------- rendering */

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
  p.textContent = ev.answer;
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

  li.innerHTML = `
    <span class="reading-index">${String(state.landed).padStart(2, "0")}</span>
    <span class="reading-name">${ev.title}<span class="reading-cat">${ev.category}</span></span>
    <span class="reading-leader">${right}</span>`;

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

  el("slope").innerHTML = "";
  // Ordered by where the title lands, so the drop reads as a cliff rather than as
  // an arbitrary list. Arrival order is query timing and means nothing here.
  const ordered = rankings
    .map((r) => ({ r, rank: r.ranking.indexOf(title) + 1 }))
    .filter((x) => x.rank > 0)
    .sort((a, b) => a.rank - b.rank);

  ordered.forEach(({ r, rank }) => {
    const row = document.createElement("div");
    row.className = "slope-row";
    if (r.key === "completion_90") row.classList.add("is-mover");
    const pct = ((rank - 1) / Math.max(1, r.ranking.length - 1)) * 100;
    row.innerHTML = `
      <span class="slope-label">${r.title}</span>
      <span class="slope-track"><span class="slope-dot" style="left:0%"></span></span>
      <span class="slope-rank">#${rank}</span>`;
    el("slope").appendChild(row);
    requestAnimationFrame(() => {
      row.querySelector(".slope-dot").style.left = `${pct}%`;
    });
  });
  el("movement").hidden = false;
}

function renderReceipts(items) {
  state.receipts = items;
  const list = el("receipts");
  list.innerHTML = "";

  items.forEach((item) => {
    const box = document.createElement("article");
    box.className = "receipt";
    box.dataset.key = item.key;

    const top = document.createElement("div");
    top.className = "receipt-top";
    top.innerHTML = `
      <span class="receipt-name">${item.title}</span>
      <span class="receipt-cat">${item.category}</span>
      <span class="receipt-rows">${item.ok ? `${item.row_count} rows` : "failed"}</span>`;
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
    sql.textContent = item.sql;
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
      item.rows.slice(0, 3).forEach((row, i) => {
        const span = document.createElement("span");
        span.innerHTML = `${i + 1}. ${item.ranking[i]} — <b>${Number(
          row[1]
        ).toLocaleString()}</b>`;
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

async function run(question) {
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
      body: JSON.stringify({ question }),
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
    case "model_unavailable": return renderModelDown(ev);
    case "naive_done":   return renderNaive(ev);
    case "naive_audit":  return renderAudit(ev);
    case "reading":      return renderReading(ev);
    case "verdict":      state.pendingVerdict = ev; return renderVerdict(ev);
    case "explanation": {
      const box = el("explanation");
      box.textContent = ev.text;
      box.hidden = false;
      return;
    }
    case "receipts":     return renderReceipts(ev.items);
    default:             return;
  }
}

/* -------------------------------------------------------------------- boot */

el("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = el("question").value.trim();
  if (q) run(q);
});

/* Deep link: /?q=<question>&auto=1 . Lets the demo recording start mid-flight
   instead of opening on a form, and makes a specific run shareable. */
const params = new URLSearchParams(location.search);
if (params.get("q")) el("question").value = params.get("q");
loadPresets().then(() => {
  if (params.get("auto")) run(el("question").value.trim());
});
