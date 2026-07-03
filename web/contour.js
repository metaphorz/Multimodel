/* Filled contour rendering of the windfield on the 21x40 grid lattice.
   Uses vendored d3-contour (marching squares) to build banded polygons, then
   maps grid-index coordinates -> lat/lon via bilinear interp over the lattice.
   Produces a Leaflet layerGroup styled like ROA Figs 6-8.

   The 40x21 lattice is coarse, so marching squares straight off it gives
   faceted bands and diamond-shaped islands. We bilinearly upsample the field by
   UPSAMPLE and apply a light box blur before contouring, so the bands follow
   smooth curves. (Cheap: ~50k cells, rebuilt only when the field changes.) */

const UPSAMPLE = 8;       // bilinear refinement factor before marching squares
const BLUR_PASSES = 2;    // light separable box-blur passes on the refined grid
const BLUR_RADIUS = 1;    // box-blur half-width (in refined-grid cells)

let LATTICE = null;   // { width, height, ewAsc, nsAsc, pointAt[y][x] }

// bilinear refinement of a width x height field to ((w-1)*k+1) x ((h-1)*k+1)
function upsampleBilinear(data, w, h, k) {
  const W = (w - 1) * k + 1, H = (h - 1) * k + 1;
  const out = new Float64Array(W * H);
  for (let Y = 0; Y < H; Y++) {
    const gy = Y / k, y0 = Math.min(h - 2, Math.floor(gy)), fy = gy - y0;
    for (let X = 0; X < W; X++) {
      const gx = X / k, x0 = Math.min(w - 2, Math.floor(gx)), fx = gx - x0;
      const v00 = data[y0 * w + x0],       v10 = data[y0 * w + x0 + 1];
      const v01 = data[(y0 + 1) * w + x0], v11 = data[(y0 + 1) * w + x0 + 1];
      out[Y * W + X] = (1 - fy) * ((1 - fx) * v00 + fx * v10) +
                             fy  * ((1 - fx) * v01 + fx * v11);
    }
  }
  return { data: out, width: W, height: H };
}

// refine a w x h field for smooth contouring: bilinear upsample by k, then a
// light box blur. Returns { data, width, height }. Shared by the map contour
// (contour.js) and the grid-point isotach popup (popup.js) so both look alike.
function refineField(data, w, h, k = UPSAMPLE) {
  const up = upsampleBilinear(data, w, h, k);
  const out = BLUR_PASSES > 0
    ? boxBlur(up.data, up.width, up.height, BLUR_PASSES, BLUR_RADIUS) : up.data;
  return { data: out, width: up.width, height: up.height };
}

// separable box blur (edge-clamped), `passes` times, half-width r
function boxBlur(data, w, h, passes, r) {
  let src = data;
  for (let p = 0; p < passes; p++) {
    const tmp = new Float64Array(w * h), out = new Float64Array(w * h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        let s = 0, c = 0;
        for (let dx = -r; dx <= r; dx++) { const xx = x + dx; if (xx >= 0 && xx < w) { s += src[y * w + xx]; c++; } }
        tmp[y * w + x] = s / c;
      }
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        let s = 0, c = 0;
        for (let dy = -r; dy <= r; dy++) { const yy = y + dy; if (yy >= 0 && yy < h) { s += tmp[yy * w + x]; c++; } }
        out[y * w + x] = s / c;
      }
    src = out;
  }
  return src;
}

// build a lattice from a grid-like object ({ew_values, ns_values, points}); does
// not touch the module global, so callers (e.g. the storm animation) can hold
// their own extended lattice alongside the static map's.
function buildLatticeFrom(grid) {
  const ewAsc = [...grid.ew_values].sort((a, b) => a - b);  // east(0)->west(117)
  const nsAsc = [...grid.ns_values].sort((a, b) => a - b);  // south(-15)->north(45)
  const width = ewAsc.length, height = nsAsc.length;
  const ewIdx = new Map(ewAsc.map((v, i) => [v, i]));
  const nsIdx = new Map(nsAsc.map((v, i) => [v, i]));
  const pointAt = Array.from({ length: height }, () => new Array(width));
  const order = new Int32Array(width * height);  // data index -> grid.points index
  grid.points.forEach((p, i) => {
    const x = ewIdx.get(p.ew), y = nsIdx.get(p.ns);
    pointAt[y][x] = p;
    order[y * width + x] = i;
  });
  return { width, height, ewAsc, nsAsc, pointAt, order };
}

function buildLattice(grid) { LATTICE = buildLatticeFrom(grid); }

// fractional grid coords (x=col, y=row, d3 space) -> [lat, lon]
function gridToLatLng(x, y, latt) {
  const { width, height, pointAt } = latt || LATTICE;
  const x0 = Math.max(0, Math.min(width - 1, Math.floor(x)));
  const y0 = Math.max(0, Math.min(height - 1, Math.floor(y)));
  const x1 = Math.min(width - 1, x0 + 1), y1 = Math.min(height - 1, y0 + 1);
  const fx = Math.max(0, Math.min(1, x - x0)), fy = Math.max(0, Math.min(1, y - y0));
  const p00 = pointAt[y0][x0], p10 = pointAt[y0][x1];
  const p01 = pointAt[y1][x0], p11 = pointAt[y1][x1];
  const lat = (1 - fy) * ((1 - fx) * p00.lat + fx * p10.lat) +
                    fy  * ((1 - fx) * p01.lat + fx * p11.lat);
  const lon = (1 - fy) * ((1 - fx) * p00.lon + fx * p10.lon) +
                    fy  * ((1 - fx) * p01.lon + fx * p11.lon);
  return [lat, lon];
}

/* Build the filled-contour layer.
   wind: per-point array (grid.json order), thresholds + colorFn from viewer.
   opts.lattice: use a caller-supplied lattice (else the module global from `grid`).
   opts.upsample: refinement factor (default UPSAMPLE); the animation uses a coarser
   value so its larger extended domain stays fast per frame.
   opts.fillOpacity: band opacity (default 0.78); the animation uses a lower value so
   the grid points show through the moving windfield. */
function buildContourLayer(grid, wind, thresholds, colorFn, opts = {}) {
  const lat = opts.lattice || (LATTICE || (buildLattice(grid), LATTICE));
  const up = opts.upsample || UPSAMPLE;
  const fillOpacity = opts.fillOpacity != null ? opts.fillOpacity : 0.78;
  const { width, height, order } = lat;

  // data array in d3 order (index = y*width + x)
  const data = new Float64Array(width * height);
  for (let k = 0; k < order.length; k++) data[k] = wind[order[k]];

  // refine + lightly blur so marching squares yields smooth bands, not facets
  const ref = refineField(data, width, height, up);

  const contours = window.d3.contours().size([ref.width, ref.height]).thresholds(thresholds)(ref.data);

  const group = L.layerGroup();
  // draw low->high so higher bands sit on top (filled-band look). Refined coords
  // map back to lattice index space by dividing out the upsample factor.
  contours.forEach(c => {
    if (!c.coordinates.length) return;
    const col = colorFn(c.value);
    c.coordinates.forEach(poly => {            // poly = [outerRing, ...holes]
      const rings = poly.map(ring => ring.map(
        ([x, y]) => gridToLatLng((x - 0.5) / up, (y - 0.5) / up, lat)));
      L.polygon(rings, {
        stroke: false, fillColor: col, fillOpacity, interactive: false,
      }).addTo(group);
    });
  });
  return group;
}
