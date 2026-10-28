/* Pure geometry helpers shared by the editor's pointer and numeric tools.
   Coordinates stay in mm; angles stay in degrees, as in the Python backend. */
export const clone = value => structuredClone(value);
export const outline = room => room.outline_mm || [[0, 0], [room.width_mm, 0],
  [room.width_mm, room.depth_mm], [0, room.depth_mm]];
export const mountingPoint = m => [m.left_wall, m.front_wall, m.height];
export function cross(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}
function onSegment(a, b, p) {
  return Math.abs(cross(a, b, p)) < 1e-6 &&
    p[0] >= Math.min(a[0], b[0]) && p[0] <= Math.max(a[0], b[0]) &&
    p[1] >= Math.min(a[1], b[1]) && p[1] <= Math.max(a[1], b[1]);
}
function intersects(a, b, c, d) {
  return (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) ||
    onSegment(a, b, c) || onSegment(a, b, d) || onSegment(c, d, a) || onSegment(c, d, b);
}
export function polygonError(room) {
  const points = outline(room), n = points.length;
  if (n < 3 || n > 64) return 'Use 3–64 corners.';
  if (points.some(p => p.some((v, i) => !Number.isFinite(v) || v < 0 || v > [room.width_mm, room.depth_mm][i]))) return 'Corners must be within the room width and depth.';
  if (new Set(points.map(p => p.join(','))).size !== n) return 'Corners must be distinct.';
  let area = 0;
  for (let i = 0; i < n; i++) {
    const a = points[i], b = points[(i + 1) % n], c = points[(i + 2) % n];
    area += a[0] * b[1] - a[1] * b[0];
    if (Math.abs(cross(a,b,c)) < 1e-6 && (b[0]-a[0])*(c[0]-b[0])+(b[1]-a[1])*(c[1]-b[1]) <= 0) return 'Adjacent walls must not overlap.';
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue;
      if (intersects(a,b,points[j],points[(j+1)%n])) return 'Walls must not cross or touch.';
    }
  }
  return Math.abs(area) < 1e-6 ? 'The outline must enclose an area.' : '';
}
export function pointInside(point, points) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const a = points[i], b = points[j];
    if (onSegment(a, b, point)) return true;
    if ((a[1] > point[1]) !== (b[1] > point[1]) && point[0] <
      (b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0]) inside = !inside;
  }
  return inside;
}
export function multiply(a, b) {
  return a.map(row => b[0].map((_, j) => row.reduce((sum, value, k) => sum + value*b[k][j], 0)));
}
export function rotation(yaw, pitch, roll) {
  const [y,p,r] = [yaw,pitch,roll].map(v => v*Math.PI/180);
  const cy=Math.cos(y),sy=Math.sin(y),cp=Math.cos(p),sp=Math.sin(p),cr=Math.cos(r),sr=Math.sin(r);
  return multiply(multiply([[cy,-sy,0],[sy,cy,0],[0,0,1]], [[1,0,0],[0,cp,-sp],[0,sp,cp]]), [[cr,0,sr],[0,1,0],[-sr,0,cr]]);
}
export function buildLayouts(config) {
  return config.lighting.controllers.map(group => {
    const product = config.lighting.products.find(p => p.id === group.product_id);
    const m = group.controller.mounting;
    // Same fixed NOLU product-frame alignment as lights/lighting.py.
    const root = {end: mountingPoint(m), matrix: multiply([[1,0,0],[0,-1,0],[0,0,1]], rotation(m.yaw,m.pitch,m.roll))};
    const resolved = new Map([['controller', root]]);
    function resolve(id) {
      if (resolved.has(id)) return resolved.get(id);
      const bar = group.bars.find(b => b.id === id), parent = resolve(bar.connected_to);
      const matrix = multiply(parent.matrix, rotation(bar.yaw,bar.pitch,bar.roll));
      const result = {...bar, start: parent.end, end: parent.end.map((v,i) => v+matrix[i][1]*product.length_mm), matrix, parentMatrix: parent.matrix};
      resolved.set(id,result);return result;
    }
    return {...group, position: root.end, bars: group.bars.map(b => resolve(b.id))};
  });
}
export function barYawAt(bar, target, length) {
  // Project the actual parent basis, preserving pitch and roll. Searching a
  // single yaw avoids treating an elevated/rolled joint as a flat room angle.
  let best = bar.yaw, error = Infinity;
  for (let yaw = -180; yaw <= 180; yaw++) {
    const matrix = multiply(bar.parentMatrix, rotation(yaw, bar.pitch, bar.roll));
    const distance = Math.hypot(...target.slice(0,2).map((v,i) => v-bar.start[i]-matrix[i][1]*length));
    if (distance < error) {error=distance;best=yaw;}
  }
  return best;
}
export function normalizedAngle(angle) {
  return ((angle + 180) % 360 + 360) % 360 - 180;
}
