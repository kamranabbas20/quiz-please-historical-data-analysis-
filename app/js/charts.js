/* SVG chart primitives.
 *
 * Hand-rolled rather than pulled from a library: the app ships as static files
 * with no build step and no network dependency, and the six forms here are
 * small. Shared rules, applied by construction:
 *   - one y-axis per chart, never two;
 *   - colour follows the entity, so a filter that drops a series never
 *     repaints the survivors;
 *   - hairline solid grid, 2px lines, >=8px markers with a 2px surface ring;
 *   - every chart has a hover/focus readout AND a table twin, so no value is
 *     reachable only by hovering.
 */

const NS = 'http://www.w3.org/2000/svg';
export const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    node.setAttribute(key, String(value));
  }
  if (parent) parent.appendChild(node);
  return node;
}

function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (min === max) return [min];
  const span = max - min;
  const rawStep = span / Math.max(1, count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude)
    .find((candidate) => candidate >= rawStep) || magnitude * 10;
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let value = start; value <= max + step / 2; value += step) {
    ticks.push(Number(value.toFixed(10)));
  }
  return ticks;
}

/* Whole-number ticks inside a domain -- places and counts have no half-steps,
 * and a tick outside the domain would draw a gridline off the plot. */
function integerTicksIn(min, max, count = 6) {
  const low = Math.ceil(min);
  const high = Math.floor(max);
  if (!Number.isFinite(low) || !Number.isFinite(high) || high < low) return [Math.round(min)];
  const step = Math.max(1, Math.ceil((high - low + 1) / count));
  const ticks = [];
  for (let value = low; value <= high; value += step) ticks.push(value);
  if (ticks[ticks.length - 1] !== high) ticks.push(high);
  return ticks;
}

/* A chart card: title, optional legend, the plot, and a table-view toggle. */
export function chartCard({ title, note, legend, render, table }) {
  const card = document.createElement('div');
  card.className = 'card';

  if (title) {
    const heading = document.createElement('h3');
    heading.textContent = title;
    card.appendChild(heading);
  }
  if (note) {
    const noteEl = document.createElement('div');
    noteEl.className = 'card-note';
    noteEl.textContent = note;
    card.appendChild(noteEl);
  }
  if (legend && legend.length > 1) card.appendChild(legendRow(legend));

  const holder = document.createElement('div');
  holder.className = 'chart';
  card.appendChild(holder);

  const tooltip = document.createElement('div');
  tooltip.className = 'tooltip';
  tooltip.hidden = true;
  holder.appendChild(tooltip);

  render(holder, tooltip);

  if (table) {
    const toggle = document.createElement('button');
    toggle.className = 'table-toggle';
    toggle.type = 'button';
    toggle.textContent = 'Показать таблицу';
    const view = document.createElement('div');
    view.className = 'table-view';
    view.hidden = true;
    toggle.addEventListener('click', () => {
      view.hidden = !view.hidden;
      toggle.textContent = view.hidden ? 'Показать таблицу' : 'Скрыть таблицу';
      if (!view.hidden && !view.childElementCount) view.appendChild(table());
    });
    card.appendChild(toggle);
    card.appendChild(view);
  }
  return card;
}

function legendRow(items) {
  const row = document.createElement('div');
  row.className = 'legend';
  for (const item of items) {
    const key = document.createElement('span');
    key.className = 'key';
    const mark = document.createElement('span');
    mark.className = item.shape === 'rect' ? 'rect' : 'line';
    mark.style.background = item.color;
    key.appendChild(mark);
    key.appendChild(document.createTextNode(item.label));   // labels are data
    row.appendChild(key);
  }
  return row;
}

function frame(holder, { width = 720, height = 260, padding }) {
  const pad = { top: 14, right: 18, bottom: 34, left: 46, ...padding };
  const svg = el('svg', {
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: 'none',
    role: 'img',
  }, holder);
  svg.style.height = `${height}px`;
  return { svg, pad, width, height, plotW: width - pad.left - pad.right, plotH: height - pad.top - pad.bottom };
}

function yAxis(ctx, scale, ticks, formatTick = String) {
  const group = el('g', { class: 'axis' }, ctx.svg);
  for (const tick of ticks) {
    const y = scale(tick);
    el('line', { class: 'grid-line', x1: ctx.pad.left, x2: ctx.width - ctx.pad.right, y1: y, y2: y }, group);
    const label = el('text', { x: ctx.pad.left - 8, y: y + 4, 'text-anchor': 'end' }, group);
    label.textContent = formatTick(tick);
  }
}

function xAxisDates(ctx, points, scale) {
  const group = el('g', { class: 'axis' }, ctx.svg);
  el('line', {
    class: 'axis-line', x1: ctx.pad.left, x2: ctx.width - ctx.pad.right,
    y1: ctx.height - ctx.pad.bottom, y2: ctx.height - ctx.pad.bottom,
  }, group);

  const maxLabels = Math.max(2, Math.floor(ctx.plotW / 84));
  const step = Math.max(1, Math.ceil(points.length / maxLabels));
  points.forEach((point, index) => {
    if (index % step !== 0 && index !== points.length - 1) return;
    const label = el('text', {
      x: scale(index), y: ctx.height - ctx.pad.bottom + 16, 'text-anchor': 'middle',
    }, group);
    label.textContent = shortDate(point.date);
  });
}

function shortDate(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso).slice(0, 10);
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' });
}

/* Tooltip content is built with textContent -- team and game names are data. */
function showTooltip(tooltip, holder, x, y, title, rows) {
  tooltip.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'tt-title';
  heading.textContent = title;
  tooltip.appendChild(heading);
  for (const row of rows) {
    if (row.value === null || row.value === undefined) continue;
    const line = document.createElement('div');
    line.className = 'tt-row';
    const label = document.createElement('span');
    if (row.color) {
      const key = document.createElement('span');
      key.className = 'tt-key';
      key.style.background = row.color;
      label.appendChild(key);
    }
    label.appendChild(document.createTextNode(row.label));
    const value = document.createElement('span');
    value.className = 'tt-value';
    value.textContent = String(row.value);
    line.appendChild(label);
    line.appendChild(value);
    tooltip.appendChild(line);
  }
  const bounds = holder.getBoundingClientRect();
  tooltip.style.left = `${Math.min(Math.max(x * bounds.width, 70), bounds.width - 70)}px`;
  tooltip.style.top = `${y * bounds.height}px`;
  tooltip.hidden = false;
}

/* Line chart over an ordered sequence of games.
 *  series: [{ key, label, color, values: (number|null)[] }]
 *  invert: draw 1 at the top (finishing position)
 */
export function lineChart(holder, tooltip, {
  points, series, invert = false, yMin, yMax, formatValue = (v) => v,
  formatTick = String, onSelect, band, integerTicks = false,
}) {
  const ctx = frame(holder, { height: 250 });
  const values = series.flatMap((s) => s.values).filter((v) => typeof v === 'number' && Number.isFinite(v));
  const bandValues = band ? band.filter((v) => Number.isFinite(v)) : [];
  const all = values.concat(bandValues);
  if (!all.length) {
    const empty = el('text', { x: ctx.width / 2, y: ctx.height / 2, 'text-anchor': 'middle', class: 'series-label' }, ctx.svg);
    empty.textContent = 'Нет данных';
    return;
  }

  const low = yMin ?? Math.min(...all);
  const high = yMax ?? Math.max(...all);
  const pad = (high - low) * 0.12 || 1;
  const domainMin = yMin ?? low - pad;
  const domainMax = yMax ?? high + pad;
  const ticks = (integerTicks
    ? integerTicksIn(domainMin, domainMax, 6)
    : niceTicks(domainMin, domainMax, 5)
  ).filter((tick) => tick >= domainMin - 1e-9 && tick <= domainMax + 1e-9);
  const scaleY = (value) => {
    const t = (value - domainMin) / (domainMax - domainMin || 1);
    const y = ctx.pad.top + (1 - t) * ctx.plotH;
    return invert ? ctx.pad.top + t * ctx.plotH : y;
  };
  const scaleX = (index) => points.length === 1
    ? ctx.pad.left + ctx.plotW / 2
    : ctx.pad.left + (index / (points.length - 1)) * ctx.plotW;

  yAxis(ctx, scaleY, ticks, formatTick);
  xAxisDates(ctx, points, scaleX);

  // Context band (e.g. the size of the field a position was achieved in).
  if (band) {
    const top = band.map((value, index) => `${scaleX(index)},${scaleY(1)}`);
    const bottom = band.map((value, index) => `${scaleX(index)},${scaleY(value)}`).reverse();
    el('polygon', {
      points: top.concat(bottom).join(' '),
      fill: 'var(--series-1)', 'fill-opacity': 0.08, stroke: 'none',
    }, ctx.svg);
  }

  for (const line of series) {
    const segments = [];
    let current = [];
    line.values.forEach((value, index) => {
      if (typeof value === 'number' && Number.isFinite(value)) {
        current.push(`${scaleX(index)},${scaleY(value)}`);
      } else if (current.length) {
        segments.push(current);
        current = [];
      }
    });
    if (current.length) segments.push(current);
    for (const segment of segments) {
      el('polyline', {
        points: segment.join(' '), fill: 'none', stroke: line.color,
        'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
        'stroke-dasharray': line.dashed ? '5 4' : null,
      }, ctx.svg);
    }
    const showMarkers = line.markers !== false && points.length <= 60;
    if (showMarkers) {
      line.values.forEach((value, index) => {
        if (!Number.isFinite(value)) return;
        el('circle', {
          cx: scaleX(index), cy: scaleY(value), r: 4.5, fill: line.color,
          stroke: 'var(--surface-1)', 'stroke-width': 2,
        }, ctx.svg);
      });
    }
  }

  // Crosshair: the reader aims at a date, not at a 2px line.
  const crosshair = el('line', {
    class: 'axis-line', y1: ctx.pad.top, y2: ctx.height - ctx.pad.bottom,
    stroke: 'var(--border-strong)', opacity: 0,
  }, ctx.svg);

  const hit = el('rect', {
    x: ctx.pad.left, y: ctx.pad.top, width: ctx.plotW, height: ctx.plotH,
    fill: 'transparent', tabindex: onSelect ? 0 : null,
  }, ctx.svg);

  let active = -1;
  const activate = (index) => {
    if (index < 0 || index >= points.length) return;
    active = index;
    crosshair.setAttribute('x1', scaleX(index));
    crosshair.setAttribute('x2', scaleX(index));
    crosshair.setAttribute('opacity', 1);
    const point = points[index];
    const rows = series.map((line) => ({
      label: line.label,
      color: line.color,
      value: Number.isFinite(line.values[index]) ? formatValue(line.values[index], line) : null,
    }));
    if (point.extra) rows.push(...point.extra);
    showTooltip(tooltip, holder, scaleX(index) / ctx.width, scaleY(
      series.map((line) => line.values[index]).find(Number.isFinite) ?? domainMin,
    ) / ctx.height, point.label, rows);
  };

  hit.addEventListener('pointermove', (event) => {
    const bounds = hit.getBoundingClientRect();
    const ratio = (event.clientX - bounds.left) / (bounds.width || 1);
    activate(Math.round(ratio * (points.length - 1)));
  });
  hit.addEventListener('pointerleave', () => {
    tooltip.hidden = true;
    crosshair.setAttribute('opacity', 0);
  });
  if (onSelect) {
    hit.addEventListener('click', () => { if (active >= 0) onSelect(points[active], active); });
    hit.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowRight') activate(Math.min(points.length - 1, active + 1));
      else if (event.key === 'ArrowLeft') activate(Math.max(0, active - 1));
      else if (event.key === 'Enter' && active >= 0) onSelect(points[active], active);
      else return;
      event.preventDefault();
    });
  }
}

/* Horizontal bars -- categories with long Russian labels read better this way. */
export function barChart(holder, tooltip, { items, color = SERIES[0], formatValue = String, max }) {
  const rowHeight = 26;
  const height = Math.max(90, items.length * rowHeight + 30);
  const labelWidth = 168;
  const ctx = frame(holder, { height, padding: { left: labelWidth, top: 8, bottom: 22, right: 44 } });
  const domainMax = max ?? Math.max(...items.map((item) => item.value || 0), 1);
  const scale = (value) => (value / domainMax) * ctx.plotW;

  items.forEach((item, index) => {
    const y = ctx.pad.top + index * rowHeight;
    const barHeight = Math.min(18, rowHeight - 8);
    const width = Math.max(0, scale(item.value || 0));

    const label = el('text', {
      x: ctx.pad.left - 10, y: y + barHeight - 3, 'text-anchor': 'end', class: 'axis',
    }, ctx.svg);
    label.setAttribute('fill', 'var(--text-secondary)');
    label.style.fontSize = '11px';
    label.textContent = item.label.length > 26 ? `${item.label.slice(0, 25)}…` : item.label;

    el('rect', {
      x: ctx.pad.left, y, width, height: barHeight, rx: 4,
      fill: item.color || color,
    }, ctx.svg);
    // Square the baseline end: the 4px radius belongs to the data end only.
    if (width > 4) {
      el('rect', { x: ctx.pad.left, y, width: Math.min(4, width), height: barHeight, fill: item.color || color }, ctx.svg);
    }

    const value = el('text', { x: ctx.pad.left + width + 6, y: y + barHeight - 3, class: 'axis' }, ctx.svg);
    value.setAttribute('fill', 'var(--text-secondary)');
    value.style.fontSize = '11px';
    value.textContent = formatValue(item.value);

    const hit = el('rect', {
      x: ctx.pad.left - labelWidth, y: y - 4, width: ctx.width, height: rowHeight, fill: 'transparent',
    }, ctx.svg);
    hit.addEventListener('pointerenter', () => showTooltip(
      tooltip, holder, (ctx.pad.left + width) / ctx.width, y / ctx.height,
      item.label, item.rows || [{ label: 'Значение', value: formatValue(item.value), color: item.color || color }],
    ));
    hit.addEventListener('pointerleave', () => { tooltip.hidden = true; });
  });
}

/* Columns for distributions (score bins, finishing positions). */
export function columnChart(holder, tooltip, { items, color = SERIES[0], formatValue = String, xLabel }) {
  const ctx = frame(holder, { height: 200, padding: { bottom: 40 } });
  const dataMax = Math.max(...items.map((item) => item.value || 0), 1);
  const ticks = integerTicksIn(0, dataMax, 5);
  // The scale has to cover the top tick as well as the data, or a bar whose
  // value sits above the last tick is drawn outside the plot.
  const domainMax = Math.max(dataMax, ticks[ticks.length - 1] || dataMax);
  const scaleY = (value) => ctx.pad.top + (1 - value / domainMax) * ctx.plotH;
  yAxis(ctx, scaleY, ticks, (tick) => String(tick));

  el('line', {
    class: 'axis-line', x1: ctx.pad.left, x2: ctx.width - ctx.pad.right,
    y1: ctx.height - ctx.pad.bottom, y2: ctx.height - ctx.pad.bottom,
  }, ctx.svg);

  const slot = ctx.plotW / Math.max(1, items.length);
  const barWidth = Math.min(24, slot - 2);       // the 2px surface gap is the separator

  items.forEach((item, index) => {
    const x = ctx.pad.left + index * slot + (slot - barWidth) / 2;
    const y = scaleY(item.value || 0);
    const height = ctx.height - ctx.pad.bottom - y;
    if (height > 0) {
      el('rect', { x, y, width: barWidth, height, rx: 4, fill: item.color || color }, ctx.svg);
      el('rect', {
        x, y: ctx.height - ctx.pad.bottom - Math.min(4, height),
        width: barWidth, height: Math.min(4, height), fill: item.color || color,
      }, ctx.svg);
    }
    const label = el('text', {
      x: x + barWidth / 2, y: ctx.height - ctx.pad.bottom + 15, 'text-anchor': 'middle', class: 'axis',
    }, ctx.svg);
    label.setAttribute('fill', 'var(--text-muted)');
    label.style.fontSize = '11px';
    label.textContent = item.label;

    const hit = el('rect', {
      x: ctx.pad.left + index * slot, y: ctx.pad.top, width: slot, height: ctx.plotH, fill: 'transparent',
    }, ctx.svg);
    hit.addEventListener('pointerenter', () => showTooltip(
      tooltip, holder, (x + barWidth / 2) / ctx.width, y / ctx.height, item.title || item.label,
      item.rows || [{ label: 'Игр', value: formatValue(item.value), color: item.color || color }],
    ));
    hit.addEventListener('pointerleave', () => { tooltip.hidden = true; });
  });

  if (xLabel) {
    const caption = el('text', {
      x: ctx.pad.left + ctx.plotW / 2, y: ctx.height - 4, 'text-anchor': 'middle', class: 'axis',
    }, ctx.svg);
    caption.setAttribute('fill', 'var(--text-muted)');
    caption.style.fontSize = '11px';
    caption.textContent = xLabel;
  }
}

/* A map of venues, drawn from coordinates alone.
 *
 * There is no basemap: tiles are images from another host, which the page
 * cannot load offline and an embedded viewer blocks outright. So the map is
 * what the data can honestly support -- venues in their true relative
 * positions, north up, with a scale bar to read distances off. Longitude is
 * scaled by cos(latitude) so the city is not stretched sideways, and the two
 * axes share one scale so distances are comparable in every direction.
 */
export function mapChart(holder, tooltip, {
  points, width = 900, height = 470, onSelect, selectedKey,
}) {
  const usable = points.filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lon));
  if (!usable.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Нет площадок с координатами';
    holder.appendChild(empty);
    return;
  }

  const pad = { top: 20, right: 18, bottom: 42, left: 18 };
  // A map must not be stretched, so it keeps its aspect ratio and takes its
  // height from its width -- unlike the plots, which stretch to their box.
  const svg = el('svg', {
    viewBox: `0 0 ${width} ${height}`, role: 'img', preserveAspectRatio: 'xMidYMid meet',
  }, holder);

  const meanLat = usable.reduce((sum, p) => sum + p.lat, 0) / usable.length;
  const kx = Math.cos((meanLat * Math.PI) / 180);
  const project = (point) => ({ x: point.lon * kx, y: -point.lat });

  const projected = usable.map((point) => ({ point, ...project(point) }));
  const xs = projected.map((p) => p.x);
  const ys = projected.map((p) => p.y);
  const spanX = Math.max(...xs) - Math.min(...xs) || 1e-4;
  const spanY = Math.max(...ys) - Math.min(...ys) || 1e-4;

  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  // One scale for both axes, or the map would misreport distances.
  const scale = Math.min(plotW / (spanX * 1.12), plotH / (spanY * 1.12));
  const midX = (Math.max(...xs) + Math.min(...xs)) / 2;
  const midY = (Math.max(...ys) + Math.min(...ys)) / 2;
  const toX = (x) => pad.left + plotW / 2 + (x - midX) * scale;
  const toY = (y) => pad.top + plotH / 2 + (y - midY) * scale;

  const maxGames = Math.max(...usable.map((point) => point.value || 0), 1);
  const radius = (value) => 5 + 17 * Math.sqrt((value || 0) / maxGames);   // area ∝ games

  // Scale bar: pick a round number of kilometres that fits the plot.
  const kmPerDegree = 111.32;
  const pxPerKm = (scale / kmPerDegree);
  const candidates = [0.25, 0.5, 1, 2, 5, 10, 20];
  const km = candidates.find((value) => value * pxPerKm > plotW * 0.18) || candidates[candidates.length - 1];
  const barW = km * pxPerKm;
  const barY = height - 20;
  const barX = pad.left + 4;
  el('line', { x1: barX, x2: barX + barW, y1: barY, y2: barY, stroke: 'var(--text-muted)', 'stroke-width': 2 }, svg);
  for (const x of [barX, barX + barW]) {
    el('line', { x1: x, x2: x, y1: barY - 4, y2: barY + 4, stroke: 'var(--text-muted)', 'stroke-width': 2 }, svg);
  }
  const barLabel = el('text', { x: barX + barW + 8, y: barY + 4, class: 'axis' }, svg);
  barLabel.setAttribute('fill', 'var(--text-muted)');
  barLabel.style.fontSize = '11px';
  barLabel.textContent = km < 1 ? `${km * 1000} м` : `${km} км`;

  // North arrow: the map has no labels of its own, so say which way is up.
  const northX = width - pad.right - 10;
  const northY = pad.top + 6;
  el('line', { x1: northX, x2: northX, y1: northY + 20, y2: northY, stroke: 'var(--text-muted)', 'stroke-width': 1.5 }, svg);
  el('polygon', {
    points: `${northX},${northY - 4} ${northX - 4},${northY + 5} ${northX + 4},${northY + 5}`,
    fill: 'var(--text-muted)',
  }, svg);
  const northLabel = el('text', { x: northX, y: northY + 32, 'text-anchor': 'middle', class: 'axis' }, svg);
  northLabel.setAttribute('fill', 'var(--text-muted)');
  northLabel.style.fontSize = '10px';
  northLabel.textContent = 'С';

  // Biggest first, so small venues stay clickable on top of large ones -- and
  // so the busiest venue wins the label where two would overlap.
  const ordered = [...projected].sort((a, b) => (b.point.value || 0) - (a.point.value || 0));
  const placed = [];
  for (const entry of ordered) {
    const { point } = entry;
    const cx = toX(entry.x);
    const cy = toY(entry.y);
    const r = radius(point.value);
    const selected = selectedKey && point.key === selectedKey;

    const group = el('g', { class: 'map-pin', tabindex: onSelect ? 0 : null }, svg);
    el('circle', {
      cx, cy, r, fill: point.color || SERIES[0],
      'fill-opacity': point.dim ? 0.25 : 0.55,
      stroke: 'var(--surface-1)', 'stroke-width': 2,
    }, group);
    if (selected) {
      el('circle', {
        cx, cy, r: r + 5, fill: 'none', stroke: point.color || SERIES[0], 'stroke-width': 2,
      }, group);
    }
    // A hit area big enough to actually hit, whatever the circle's size.
    const hit = el('circle', { cx, cy, r: Math.max(r + 6, 14), fill: 'transparent' }, group);

    const show = () => showTooltip(tooltip, holder, cx / width, cy / height, point.label, point.rows || []);
    hit.addEventListener('pointerenter', show);
    group.addEventListener('focus', show);
    hit.addEventListener('pointerleave', () => { tooltip.hidden = true; });
    group.addEventListener('blur', () => { tooltip.hidden = true; });
    if (onSelect) {
      hit.addEventListener('click', () => onSelect(point));
      group.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(point); }
      });
    }

    // Label the venues that carry the history, and only where the label fits:
    // venues cluster downtown, and stacked labels are worse than none.
    if (point.name && (point.value >= maxGames * 0.06 || selected)) {
      const text = point.name.length > 22 ? `${point.name.slice(0, 21)}…` : point.name;
      const width = text.length * 6.1;
      const box = { x1: cx - width / 2, x2: cx + width / 2, y1: cy - r - 17, y2: cy - r - 4 };
      const clashes = placed.some((other) => !(
        box.x2 < other.x1 - 2 || box.x1 > other.x2 + 2
        || box.y2 < other.y1 - 2 || box.y1 > other.y2 + 2
      ));
      if (!clashes) {
        placed.push(box);
        const label = el('text', {
          x: cx, y: cy - r - 6, 'text-anchor': 'middle', class: 'axis',
        }, svg);
        label.setAttribute('fill', 'var(--text-secondary)');
        label.style.fontSize = '11px';
        label.textContent = text;
      }
    }
  }
}

/* When each venue was in use: one bar per venue, from first game to last. */
export function periodBars(holder, tooltip, { items, formatDate }) {
  const rowHeight = 24;
  const labelWidth = 150;
  const height = Math.max(80, items.length * rowHeight + 34);
  const ctx = frame(holder, { height, padding: { left: labelWidth, right: 20, top: 8, bottom: 26 } });

  const times = items.flatMap((item) => [item.from, item.to]).filter(Boolean).map((d) => new Date(d).getTime());
  const min = Math.min(...times);
  const max = Math.max(...times);
  const scale = (time) => ctx.pad.left + ((time - min) / (max - min || 1)) * ctx.plotW;

  // Year gridlines: the only tick that means anything over a multi-year span.
  const firstYear = new Date(min).getFullYear();
  const lastYear = new Date(max).getFullYear();
  for (let year = firstYear; year <= lastYear + 1; year += 1) {
    const time = new Date(`${year}-01-01T00:00:00`).getTime();
    if (time < min || time > max) continue;
    const x = scale(time);
    el('line', { class: 'grid-line', x1: x, x2: x, y1: ctx.pad.top, y2: ctx.height - ctx.pad.bottom }, ctx.svg);
    const label = el('text', { x, y: ctx.height - ctx.pad.bottom + 15, 'text-anchor': 'middle', class: 'axis' }, ctx.svg);
    label.setAttribute('fill', 'var(--text-muted)');
    label.style.fontSize = '11px';
    label.textContent = String(year);
  }

  items.forEach((item, index) => {
    const y = ctx.pad.top + index * rowHeight;
    const barHeight = 10;
    const x1 = scale(new Date(item.from).getTime());
    const x2 = scale(new Date(item.to).getTime());
    const width = Math.max(3, x2 - x1);

    const label = el('text', { x: ctx.pad.left - 10, y: y + barHeight, 'text-anchor': 'end', class: 'axis' }, ctx.svg);
    label.setAttribute('fill', 'var(--text-secondary)');
    label.style.fontSize = '11px';
    label.textContent = item.label.length > 20 ? `${item.label.slice(0, 19)}…` : item.label;

    el('rect', { x: x1, y, width, height: barHeight, rx: 4, fill: item.color || SERIES[0] }, ctx.svg);

    const hit = el('rect', {
      x: ctx.pad.left - labelWidth, y: y - 5, width: ctx.width, height: rowHeight, fill: 'transparent',
    }, ctx.svg);
    hit.addEventListener('pointerenter', () => showTooltip(
      tooltip, holder, (x1 + width / 2) / ctx.width, y / ctx.height, item.label,
      [
        { label: 'Игр', value: String(item.value), color: item.color || SERIES[0] },
        { label: 'Первая', value: formatDate(item.from) },
        { label: 'Последняя', value: formatDate(item.to) },
      ],
    ));
    hit.addEventListener('pointerleave', () => { tooltip.hidden = true; });
  });
}

/* Grouped columns: the team's round scores beside the field's average. */
export function groupedColumns(holder, tooltip, { groups, series, formatValue = String }) {
  const ctx = frame(holder, { height: 230, padding: { bottom: 40 } });
  const values = groups.flatMap((group) => group.values).filter(Number.isFinite);
  const dataMax = Math.max(...values, 1);
  const ticks = niceTicks(0, dataMax, 4);
  const domainMax = Math.max(dataMax, ticks[ticks.length - 1] || dataMax);
  const scaleY = (value) => ctx.pad.top + (1 - value / domainMax) * ctx.plotH;
  yAxis(ctx, scaleY, ticks, (tick) => String(Number(tick.toFixed(1))));

  el('line', {
    class: 'axis-line', x1: ctx.pad.left, x2: ctx.width - ctx.pad.right,
    y1: ctx.height - ctx.pad.bottom, y2: ctx.height - ctx.pad.bottom,
  }, ctx.svg);

  const slot = ctx.plotW / Math.max(1, groups.length);
  const barWidth = Math.min(20, (slot - 6) / series.length - 2);

  groups.forEach((group, groupIndex) => {
    group.values.forEach((value, seriesIndex) => {
      if (!Number.isFinite(value)) return;
      const x = ctx.pad.left + groupIndex * slot + (slot - (barWidth + 2) * series.length) / 2
        + seriesIndex * (barWidth + 2);
      const y = scaleY(value);
      const height = ctx.height - ctx.pad.bottom - y;
      el('rect', { x, y, width: barWidth, height, rx: 4, fill: series[seriesIndex].color }, ctx.svg);
      el('rect', {
        x, y: ctx.height - ctx.pad.bottom - Math.min(4, height), width: barWidth,
        height: Math.min(4, height), fill: series[seriesIndex].color,
      }, ctx.svg);
    });

    const label = el('text', {
      x: ctx.pad.left + groupIndex * slot + slot / 2, y: ctx.height - ctx.pad.bottom + 15,
      'text-anchor': 'middle', class: 'axis',
    }, ctx.svg);
    label.setAttribute('fill', 'var(--text-muted)');
    label.style.fontSize = '11px';
    label.textContent = group.label;

    const hit = el('rect', {
      x: ctx.pad.left + groupIndex * slot, y: ctx.pad.top, width: slot, height: ctx.plotH, fill: 'transparent',
    }, ctx.svg);
    hit.addEventListener('pointerenter', () => showTooltip(
      tooltip, holder, (ctx.pad.left + groupIndex * slot + slot / 2) / ctx.width, ctx.pad.top / ctx.height,
      group.label,
      series.map((line, index) => ({
        label: line.label, color: line.color,
        value: Number.isFinite(group.values[index]) ? formatValue(group.values[index]) : null,
      })),
    ));
    hit.addEventListener('pointerleave', () => { tooltip.hidden = true; });
  });
}
