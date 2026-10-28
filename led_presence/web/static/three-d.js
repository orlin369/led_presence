/* Local canvas renderer. World coordinates remain X/Y/Z millimetres; camera
   projection never changes installation data or device geometry. */
'use strict';
const canvas = document.getElementById('scene');
const ctx = canvas.getContext('2d');
const status = document.getElementById('scene-status');
const camera = document.getElementById('scene-camera');
const palette = ['#b7ed86', '#6cdbec', '#f2b57d', '#c9a1ff', '#ff94bc', '#f0de7d'];
let state, yaw = -0.65, tilt = 0.65, zoom = 1, pan = [0, 0], drag;
let width = 1, height = 1, center = [0, 0, 0], radius = 1, primitives = [];
const rgb = values => `rgb(${values.join(',')})`;
const mount = m => [m.left_wall, m.front_wall, m.height];
function project(point) {
  const [x, y, z] = point.map((v, i) => (v - center[i]) / radius);
  const horizontal = x * Math.cos(yaw) - y * Math.sin(yaw);
  const depth = x * Math.sin(yaw) + y * Math.cos(yaw);
  const vertical = z * Math.cos(tilt) - depth * Math.sin(tilt);
  const distance = depth * Math.cos(tilt) + z * Math.sin(tilt);
  const perspective = 4 / (4 - distance);
  const scale = Math.min(width, height) * 0.33 * zoom * perspective;
  return [width / 2 + horizontal * scale + pan[0],
    height / 2 - vertical * scale + pan[1], distance];
}
function line(a, b, color, size = 1) {
  primitives.push({points: [a, b], color, size});
}
function marker(point, color, label, size = 5) {
  primitives.push({points: [point], color, label, size});
}
function row(name, description, position) {
  const tr = document.createElement('tr');
  for (const value of [name, description, position.map(Math.round).join(' / ')]) {
    const td = document.createElement('td'); td.textContent = value; tr.append(td);
  }
  document.getElementById('scene-objects').append(tr);
}
function objects(updateTable) {
  const c = state.config, live = state.sensors;
  for (const [i, sensor] of c.sensors.entries()) {
    const point = mount(sensor.mounting), color = sensor.enabled ? palette[i % palette.length] : '#71818a';
    const current = live.find(s => s.id === sensor.id);
    const description = !sensor.enabled ? 'Disabled' : !current?.connected ? 'Offline' : current.stale ? 'Stale' : 'Online';
    marker(point, color, `${sensor.name} · ${description}`, 7);
    line([point[0], point[1], 0], point, '#2c405c');
    const angle = sensor.mounting.yaw * Math.PI / 180;
    const reach = radius * 0.13;
    const tip = [point[0] + Math.sin(angle) * reach, point[1] + Math.cos(angle) * reach, point[2]];
    line(point, tip, color, 2);
    for (const offset of [-0.45, 0.45]) {
      line(tip, [tip[0] - Math.sin(angle + offset) * reach * 0.3,
        tip[1] - Math.cos(angle + offset) * reach * 0.3, tip[2]], color, 2);
    }
    if (updateTable) row(sensor.name, description, point);
  }
  for (const controller of state.lighting.controllers) {
    const color = controller.controller_on === true ? '#4cd964' : controller.controller_on === false ? '#ff4d4d' : '#8a9aa2';
    marker(controller.controller, color, controller.name, 7);
    if (updateTable) row(controller.name, !controller.enabled ? 'Disabled' : controller.controller_on === true ? 'On' : controller.controller_on === false ? 'Off' : 'Unknown / offline', controller.controller);
    for (const bar of controller.bars) {
      line(bar.start, bar.end, '#577394', 7);
      const pixels = bar.pixels || [bar.color || [160, 190, 200]];
      for (let i = 0; i < pixels.length; i++) {
        const index = bar.reversed ? pixels.length - 1 - i : i;
        const a = bar.start.map((v, axis) => v + (bar.end[axis] - v) * i / pixels.length);
        const b = bar.start.map((v, axis) => v + (bar.end[axis] - v) * (i + 1) / pixels.length);
        line(a, b, rgb(pixels[index]), 4);
      }
      marker(bar.end, '#b3c2d5', `${controller.name} / ${bar.id}`, 2);
      if (updateTable) row(`${controller.name} / ${bar.id}`, 'Bar start', bar.start);
    }
  }
  for (const track of state.tracks) {
    const point = [track.room_x_mm, track.room_y_mm, 0];
    marker(point, rgb(track.color || [183, 237, 134]), `${track.sensor_name} / ${track.id}`, 9);
    if (updateTable) row(`${track.sensor_name} / ${track.id}`, 'Live track · floor projection', point);
  }
}
function draw(updateTable = false) {
  if (!state || !ctx) return;
  primitives = [];
  const room = state.config.room, w = room.width_mm, d = room.depth_mm, h = room.height_mm;
  // Include bars and devices outside the room without clipping their geometry.
  const bounds = [[0, 0, 0], [w, d, h], ...state.config.sensors.map(s => mount(s.mounting)),
    ...state.lighting.controllers.map(c => c.controller),
    ...state.lighting.bars.flatMap(b => [b.start, b.end]),
    ...state.tracks.map(t => [t.room_x_mm, t.room_y_mm, 0])];
  const low = [0, 1, 2].map(i => Math.min(...bounds.map(p => p[i])));
  const high = [0, 1, 2].map(i => Math.max(...bounds.map(p => p[i])));
  center = low.map((v, i) => (v + high[i]) / 2);
  radius = Math.max(1, Math.hypot(...high.map((v, i) => v - low[i])) / 2);
  ctx.clearRect(0, 0, width, height);
  const floor = [[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0]];
  ctx.beginPath();
  floor.forEach((p, i) => { const q = project(p); if (i) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); });
  ctx.closePath(); ctx.fillStyle = '#122a4f'; ctx.fill();
  for (let i = 0; i <= 10; i++) {
    line([w * i / 10, 0, 0], [w * i / 10, d, 0], '#2c405c');
    line([0, d * i / 10, 0], [w, d * i / 10, 0], '#2c405c');
  }
  for (let i = 0; i < 4; i++) {
    const a = floor[i], b = floor[(i + 1) % 4];
    line(a, b, '#577394', 2);
    line(a, [a[0], a[1], h], '#577394');
    line([a[0], a[1], h], [b[0], b[1], h], '#577394');
  }
  marker([w / 2, 0, 0], '#b3c2d5', `FRONT · ${w} mm`, 0);
  marker([w / 2, d, 0], '#b3c2d5', 'BACK', 0);
  marker([0, d / 2, 0], '#b3c2d5', `LEFT · ${d} mm`, 0);
  marker([w, d / 2, 0], '#b3c2d5', 'RIGHT', 0);
  marker([0, 0, h], '#b3c2d5', `Height ${h} mm`, 0);
  if (updateTable) document.getElementById('scene-objects').replaceChildren();
  objects(updateTable);
  primitives.sort((a, b) => a.points.reduce((s, p) => s + project(p)[2], 0) / a.points.length - b.points.reduce((s, p) => s + project(p)[2], 0) / b.points.length);
  const labels = document.getElementById('scene-labels').checked;
  for (const item of primitives) {
    const points = item.points.map(project);
    ctx.strokeStyle = item.color; ctx.fillStyle = item.color; ctx.lineWidth = item.size; ctx.lineCap = 'round';
    ctx.beginPath();
    if (points.length === 2) {
      ctx.moveTo(points[0][0], points[0][1]); ctx.lineTo(points[1][0], points[1][1]); ctx.stroke();
    } else {
      const [x, y] = points[0];
      if (item.size) { ctx.arc(x, y, item.size, 0, Math.PI * 2); ctx.fill(); }
      if (labels && item.label) {
        ctx.font = '12px Roboto, sans-serif'; ctx.lineWidth = 4;
        ctx.strokeStyle = '#0b1524'; ctx.strokeText(item.label, x + 10, y - 10); ctx.fillText(item.label, x + 10, y - 10);
      }
    }
  }
}
function resize() {
  const rect = canvas.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
  width = rect.width; height = rect.height;
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  ctx?.setTransform(ratio, 0, 0, ratio, 0, 0); draw();
}
function reset() {
  yaw = camera.value === 'side' ? -Math.PI / 2 : camera.value === 'orbit' ? -0.65 : 0;
  tilt = camera.value === 'top' ? Math.PI / 2 : camera.value === 'orbit' ? 0.65 : 0;
  zoom = 1; pan = [0, 0]; draw();
}
function orbit(dx, dy) {
  yaw += dx * 0.008; tilt = Math.max(-1.55, Math.min(1.55, tilt + dy * 0.008)); camera.value = 'orbit';
}
function magnify(factor) { zoom = Math.max(0.25, Math.min(8, zoom * factor)); draw(); }
canvas.addEventListener('contextmenu', event => event.preventDefault());
canvas.addEventListener('pointerdown', event => {
  if (drag) return;
  drag = {id: event.pointerId, x: event.clientX, y: event.clientY, pan: event.shiftKey || event.button === 2};
  canvas.setPointerCapture(event.pointerId); canvas.focus();
});
canvas.addEventListener('pointermove', event => {
  if (!drag || drag.id !== event.pointerId) return;
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
  if (drag.pan) { pan[0] += dx; pan[1] += dy; } else orbit(dx, dy);
  drag.x = event.clientX; drag.y = event.clientY; draw();
});
canvas.addEventListener('lostpointercapture', () => { drag = null; });
canvas.addEventListener('pointerup', event => { if (drag?.id === event.pointerId) { canvas.releasePointerCapture(event.pointerId); drag = null; } });
canvas.addEventListener('pointercancel', () => { drag = null; });
canvas.addEventListener('wheel', event => { event.preventDefault(); magnify(Math.exp(-event.deltaY * 0.001)); }, {passive: false});
canvas.addEventListener('keydown', event => {
  const delta = {ArrowLeft: [-12, 0], ArrowRight: [12, 0], ArrowUp: [0, -12], ArrowDown: [0, 12]}[event.key];
  if (delta) {
    event.preventDefault();
    if (event.shiftKey) { pan[0] += delta[0]; pan[1] += delta[1]; } else orbit(...delta);
    draw();
  } else if (['+', '=', '-', 'Home'].includes(event.key)) {
    event.preventDefault(); if (event.key === 'Home') reset(); else magnify(event.key === '-' ? 0.85 : 1.15);
  }
});
camera.addEventListener('change', reset);
document.getElementById('scene-reset').addEventListener('click', reset);
document.getElementById('scene-in').addEventListener('click', () => magnify(1.2));
document.getElementById('scene-out').addEventListener('click', () => magnify(1 / 1.2));
document.getElementById('scene-labels').addEventListener('change', () => draw());
// Canvas text must be redrawn when the bundled web font finishes loading.
document.fonts.load('12px Roboto').then(() => draw()).catch(() => draw());
new ResizeObserver(resize).observe(canvas);
async function poll() {
  try {
    const response = await fetch('/api/state', {signal: AbortSignal.timeout(5000), cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state = await response.json();
    status.textContent = 'Live · updates ~1 s';
    document.getElementById('scene-summary').textContent = `${state.config.installation_name} · ${state.config.sensors.length} sensors · ${state.lighting.controllers.length} controllers · ${state.tracks.length} live tracks · ${state.config.calibrated ? 'Calibrated' : 'Uncalibrated'}`;
    draw(true);
  } catch (error) {
    status.textContent = `Connection lost · ${state ? 'showing last snapshot' : 'retrying'}`;
  } finally { window.setTimeout(poll, 1000); }
}
if (ctx) poll(); else status.textContent = 'Canvas is unavailable in this browser.';
