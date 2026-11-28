const point = value => Array.isArray(value) && value.length === 2 && value.every(Number.isFinite);

function outlineOf(raw) {
  if (!Array.isArray(raw) || raw.length < 3 || !raw.every(point)) return undefined;
  const [x,y] = raw[0];
  const area = raw.reduce((sum, [ax,ay], index) => {
    const [bx,by] = raw[(index + 1) % raw.length];
    return sum + (ax-x) * (by-y) - (bx-x) * (ay-y);
  }, 0);
  return Number.isFinite(area) && area !== 0 ? raw : undefined;
}

function bounds(points) {
  let left=Infinity, right=-Infinity, bottom=Infinity, top=-Infinity;
  for (const [x,y] of points) {
    left=Math.min(left,x); right=Math.max(right,x);
    bottom=Math.min(bottom,y); top=Math.max(top,y);
  }
  return {left, right, bottom, top};
}

export function printObjectsOnPlate(status) {
  const raw = status.exclude_object?.objects;
  if (!Array.isArray(raw)) return [];
  const result = new Map();
  for (const item of raw) {
    if (typeof item?.name !== 'string' || !item.name || result.has(item.name)) continue;
    const outline = outlineOf(item.polygon);
    const box = outline && bounds(outline);
    const supplied = point(item.center) && item.center;
    const center = box ? [(box.left+box.right)/2, (box.bottom+box.top)/2] : supplied;
    result.set(item.name, {name:item.name, outline, center});
  }
  const rows = [...result.values()];
  const counts = new Map();
  for (const {center} of rows) {
    if (!center) continue;
    const key = center.join(',');
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return rows.map(row => !row.outline && row.center && counts.get(row.center.join(',')) > 1
    ? {...row, center:undefined} : row);
}

function footprint(points) {
  const box = bounds(points);
  const width=box.right-box.left, height=box.top-box.bottom;
  if (width <= 0 || height <= 0) return undefined;
  return {
    left:box.left, top:box.bottom, width, height,
    clipPath:'polygon(' + points.map(([x,y]) =>
      (100*(x-box.left)/width) + '% ' + (100*(y-box.bottom)/height) + '%').join(',') + ')'
  };
}

function position(node, box) {
  const {left, top, width, height, clipPath} = box;
  Object.assign(node.style, {left:left+'%', top:top+'%', width:width+'%', height:height+'%'});
  if (clipPath) node.style.clipPath=clipPath;
}

export function createPlatePreview(el, rows, select) {
  const plot = el('div', {class:'print-plate', role:'group', 'aria-label':'模型俯视预览'});
  const visible = rows.filter(row => row.center);
  const all = visible.flatMap(row => row.outline || [row.center]);
  const {left, right, top, bottom} = bounds(all);
  const size = Math.max(right-left, top-bottom, 16) * 1.26;
  const x0 = (left+right-size)/2, yMax = (bottom+top+size)/2;
  const project = ([x,y]) => [100*(x-x0)/size, 100*(yMax-y)/size];
  const views = visible.map(row => ({
    ...row, location:project(row.center), shape:row.outline && footprint(row.outline.map(project))
  }));
  for (const [index,row] of views.entries()) {
    const [x,y] = row.location;
    const {shape} = row;
    const state = row.skipped ? '已跳过' : row.pending ? '等待设备确认' : row.selected ? '已勾选'
      : row.current ? '正在打印' : row.allowed ? '可选择' : '不可选择';
    const target = el('button', {type:'button', class:'print-plate-target',
      disabled:!row.allowed, 'aria-label':'模型 ' + (index+1) + ' · ' + state,
      'aria-pressed':String(row.selected), 'data-object':row.name,
      onclick:() => select(row.name)});
    target.dataset.state = row.skipped ? 'skipped' : row.pending ? 'pending'
      : row.selected ? 'selected' : row.current ? 'current' : 'available';
    const spacing = Math.min(18, ...views.filter(view => view !== row)
      .map(view => Math.hypot(x-view.location[0],y-view.location[1])));
    const narrow = !shape || (spacing > 0 && Math.min(shape.width,shape.height) < 9);
    if (narrow && shape) {
      const silhouette = el('button', {type:'button', class:'print-plate-target print-plate-outline',
        disabled:!row.allowed, tabindex:-1, 'aria-hidden':'true', onclick:() => select(row.name)});
      silhouette.dataset.state = target.dataset.state;
      position(silhouette, shape);
      plot.append(silhouette);
    }
    if (narrow) {
      const diameter=Math.min(10, spacing * 0.68);
      target.classList.add('print-plate-marker-target');
      position(target, {left:x-diameter/2, top:y-diameter/2, width:diameter, height:diameter});
    } else {
      position(target, shape);
    }
    plot.append(target);
    const marker = el('span', {class:'print-plate-mark', 'aria-hidden':'true'},
      row.skipped ? '×' : row.pending ? '…' : row.selected ? '✓' : String(index+1));
    marker.dataset.state = target.dataset.state;
    marker.style.left=x+'%';
    marker.style.top=y+'%';
    plot.append(marker);
  }
  return plot;
}
