/* Points of Interest: a lower-right map overlay listing user-chosen grid points
   by (ew,ns). Insert/delete points; click one for a detail page that combines the
   hover details and the windfield isotach + time-series plots, with a Print /
   Save-PDF button. The list persists to localStorage (reset restores defaults). */

const POI_DEFAULTS = [[9, 15], [15, 0], [60, 0], [12, -12], [6, 45]];
const POI_KEY = "formS6_poi";

const poi = { list: [], markers: {}, panel: null };

function poiLoad() {
  try {
    const s = JSON.parse(localStorage.getItem(POI_KEY));
    if (Array.isArray(s)) return s.filter(p => Array.isArray(p) && p.length === 2)
                                  .map(p => [p[0] | 0, p[1] | 0]);
  } catch (e) { /* fall through to defaults */ }
  return POI_DEFAULTS.map(p => [p[0], p[1]]);
}

function poiSave() { localStorage.setItem(POI_KEY, JSON.stringify(poi.list)); }

// index of the grid vertex at (ew,ns), or -1 if none
function poiGridIdx(ew, ns) {
  return state.grid.points.findIndex(p => p.ew === ew && p.ns === ns);
}

function poiPlace(idx) {
  const p = state.grid.points[idx];
  return p.place || (p.land ? "land" : "water");
}

function poiSetErr(msg) { document.getElementById("poiErr").textContent = msg || ""; }

function poiAdd(ew, ns) {
  if (!Number.isInteger(ew) || !Number.isInteger(ns)) {
    poiSetErr("Enter two integers, e.g. 9,15"); return;
  }
  if (poiGridIdx(ew, ns) < 0) {
    poiSetErr(`(${ew},${ns}) is not a grid vertex (ew 0..117, ns -15..45, step 3)`); return;
  }
  if (poi.list.some(p => p[0] === ew && p[1] === ns)) {
    poiSetErr(`(${ew},${ns}) is already in the list`); return;
  }
  poiSetErr("");
  poi.list.push([ew, ns]);
  poiSave(); poiRender();
}

function poiRemove(ew, ns) {
  poi.list = poi.list.filter(p => !(p[0] === ew && p[1] === ns));
  poiSave(); poiRender();
}

function poiReset() {
  poi.list = POI_DEFAULTS.map(p => [p[0], p[1]]);
  poiSave(); poiSetErr(""); poiRender();
}

// gold star markers on the map, one per POI, click -> detail page
function poiSyncMarkers() {
  Object.values(poi.markers).forEach(m => state.map.removeLayer(m));
  poi.markers = {};
  poi.list.forEach(([ew, ns]) => {
    const idx = poiGridIdx(ew, ns);
    if (idx < 0) return;
    const p = state.grid.points[idx];
    const m = L.marker([p.lat, p.lon], {
      icon: L.divIcon({ className: "poi-pin", html: "★", iconSize: [20, 20], iconAnchor: [10, 10] }),
    }).addTo(state.map);
    m.bindTooltip(`POI (${ew},${ns})`, { direction: "top", offset: [0, -8] });
    m.on("click", () => poiOpenDetail(idx));
    poi.markers[`${ew},${ns}`] = m;
  });
}

function poiRender() {
  const ul = document.getElementById("poiList");
  ul.textContent = "";
  poi.list.forEach(([ew, ns]) => {
    const idx = poiGridIdx(ew, ns);
    const li = document.createElement("li");
    li.className = "poi-row";

    const view = document.createElement("button");
    view.className = "poi-view";
    view.textContent = `(${ew},${ns})`;
    if (idx >= 0) {
      view.title = poiPlace(idx);
      view.addEventListener("click", () => poiOpenDetail(idx));
    } else {
      view.disabled = true; view.title = "off grid";
    }

    const place = document.createElement("span");
    place.className = "poi-place";
    place.textContent = idx >= 0 ? poiPlace(idx) : "off grid";

    const del = document.createElement("button");
    del.className = "poi-del"; del.textContent = "×"; del.title = "remove";
    del.addEventListener("click", () => poiRemove(ew, ns));

    li.append(view, place, del);
    ul.appendChild(li);
  });
  poiSyncMarkers();
}

// detail content: hover details + windfield plots, for the current selection
function poiDetailHTML(idx) {
  const { model, cat, vIdx } = currentSelection();
  return `<div class="poi-info">${pointInfoHTML(idx)}</div>` +
         `<h4 class="poi-h">Windfield — ${model} ${cat.toUpperCase()} v${vIdx + 1}</h4>` +
         windfieldBodyHTML(idx);
}

function ensurePoiPanel() {
  if (poi.panel) return poi.panel;
  const el = document.createElement("div");
  el.className = "analysis-panel poi-detail";
  el.style.left = "120px"; el.style.top = "60px";
  el.style.width = "450px"; el.style.height = "660px";
  el.innerHTML =
    `<div class="ap-header"><span class="ap-title">Point of Interest</span>` +
    `<button class="ap-close" title="close">&times;</button></div>` +
    `<div class="ap-body"></div>`;
  document.getElementById("map").appendChild(el);
  if (window.L) { L.DomEvent.disableClickPropagation(el); L.DomEvent.disableScrollPropagation(el); }
  el.querySelector(".ap-close").addEventListener("click", () => { el.style.display = "none"; });
  el.addEventListener("mousedown", () => bringFront(el));
  makeDraggable(el, el.querySelector(".ap-header"));
  poi.panel = { el, body: el.querySelector(".ap-body"), title: el.querySelector(".ap-title") };
  return poi.panel;
}

function poiOpenDetail(idx) {
  const p = ensurePoiPanel();
  const pt = state.grid.points[idx];
  p.title.textContent = `POI (${pt.ew},${pt.ns})${pt.place ? " — " + pt.place : ""}`;
  const actions = `<div class="poi-actions"><button class="poi-print">Print / Save PDF</button></div>`;
  p.body.innerHTML = actions + poiDetailHTML(idx);
  p.body.querySelector(".poi-print").addEventListener("click", () => poiPrint(idx));
  p.el.style.display = "flex";
  bringFront(p.el);
}

// open a clean window with the same detail content and trigger the print dialog
// (which can print to a printer or "Save as PDF")
function poiPrint(idx) {
  const pt = state.grid.points[idx];
  const { model, cat, vIdx } = currentSelection();
  const title = `Form S-6 — Point of Interest (${pt.ew},${pt.ns})`;
  const w = window.open("", "_blank", "width=760,height=960");
  if (!w) { poiSetErr("Pop-up blocked — allow pop-ups to print/save."); return; }
  w.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title><style>` +
    `body{font:13px/1.5 -apple-system,system-ui,sans-serif;color:#1c2530;margin:24px;}` +
    `h1{font-size:18px;margin:0 0 4px;} h4{margin:14px 0 4px;font-size:14px;}` +
    `.sub{color:#64748b;font-size:12px;margin-bottom:14px;}` +
    `.poi-info{font-size:13px;line-height:1.6;} hr{border:none;border-top:1px solid #e2e8f0;margin:6px 0;}` +
    `svg{width:100%;max-width:540px;height:auto;} .note{color:#64748b;font-size:11px;}` +
    `.ax{font-size:10px;fill:#475569;}` +
    `</style></head><body>` +
    `<h1>${title}${pt.place ? " — " + pt.place : ""}</h1>` +
    `<div class="sub">${model} · ${cat.toUpperCase()} · v${vIdx + 1} · ` +
    `generated ${new Date().toLocaleString()}</div>` +
    poiDetailHTML(idx) +
    `<script>window.onload=function(){setTimeout(function(){window.print();},300);};<\/script>` +
    `</body></html>`);
  w.document.close();
}

function setupPoi() {
  poi.list = poiLoad();
  const inp = document.getElementById("poiInput");
  const doAdd = () => {
    const m = inp.value.split(/[ ,]+/).filter(Boolean).map(s => parseInt(s, 10));
    poiAdd(m[0], m[1]);
    inp.value = "";
  };
  document.getElementById("poiAdd").addEventListener("click", doAdd);
  inp.addEventListener("keydown", e => { if (e.key === "Enter") doAdd(); });
  document.getElementById("poiReset").addEventListener("click", poiReset);
  const panel = document.getElementById("poiPanel");
  if (window.L) L.DomEvent.disableClickPropagation(panel);
  // draggable by its header, like the floating analysis/windfield panels
  panel.addEventListener("mousedown", () => bringFront(panel));
  makeDraggable(panel, panel.querySelector(".poi-head"));
  poiRender();
}
