const state = {
  snapshot: null,
  reconnectDelay: 500,
  displayedWatts: 0,
  wattsAnimationFrame: null,
  alertsMuted: true,
  audioCtx: null,
};

const el = {
  totalWatts: document.getElementById("total-watts"),
  todayKwh: document.getElementById("today-kwh"),
  serverClock: document.getElementById("server-clock"),
  onlineDot: document.getElementById("online-dot"),
  onlineText: document.getElementById("online-text"),
  offlineBanner: document.getElementById("offline-banner"),
  bellToggle: document.getElementById("bell-toggle"),
  rooms: document.getElementById("rooms"),
  deviceList: document.getElementById("device-list"),
  alertsList: document.getElementById("alerts-list"),
};

const roomCardTemplate = document.getElementById("room-card-template");
const deviceRowTemplate = document.getElementById("device-row-template");
const alertItemTemplate = document.getElementById("alert-item-template");

function formatRelativeTime(isoString) {
  const then = new Date(isoString).getTime();
  const now = state.snapshot ? new Date(state.snapshot.server_time).getTime() : Date.now();
  const diffMin = Math.max(0, Math.round((now - then) / 60000));
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  return `${Math.round(diffMin / 60)}h ago`;
}

function formatKind(kind) {
  return kind.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

function deviceLabel(device, roomName) {
  const kind = device.kind === "fan" ? "Fan" : "Light";
  return `${roomName} · ${kind} ${device.index}`;
}

function setStatusDot(dotEl, isOn) {
  dotEl.classList.toggle("status-dot--on", isOn);
  dotEl.classList.toggle("status-dot--off", !isOn);
}

function setTotalWattsInstant(watts) {
  if (state.wattsAnimationFrame) cancelAnimationFrame(state.wattsAnimationFrame);
  state.displayedWatts = watts;
  el.totalWatts.textContent = Math.round(watts);
}

function tweenTotalWatts(target, duration = 500) {
  if (state.wattsAnimationFrame) cancelAnimationFrame(state.wattsAnimationFrame);
  const start = state.displayedWatts;
  const startTime = performance.now();

  function step(now) {
    const t = Math.min(1, (now - startTime) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    state.displayedWatts = start + (target - start) * eased;
    el.totalWatts.textContent = Math.round(state.displayedWatts);
    state.wattsAnimationFrame = t < 1 ? requestAnimationFrame(step) : null;
  }

  state.wattsAnimationFrame = requestAnimationFrame(step);
}

function renderDeviceRow(device, roomName) {
  const row = deviceRowTemplate.content.cloneNode(true);
  const tr = row.querySelector(".device-row");
  tr.id = `device-${device.id}`;
  setStatusDot(tr.querySelector(".status-dot"), device.state === "on");
  tr.querySelector(".device-label").textContent = deviceLabel(device, roomName);
  tr.querySelector(".device-wattage").textContent = `${device.wattage} W`;
  const lastChanged = tr.querySelector(".device-last-changed");
  lastChanged.textContent = formatRelativeTime(device.last_changed);
  lastChanged.dataset.ts = device.last_changed;
  return row;
}

function render(snapshot) {
  state.snapshot = snapshot;

  setTotalWattsInstant(snapshot.total_watts);
  el.todayKwh.textContent = snapshot.today_kwh.toFixed(1);
  el.serverClock.textContent = new Date(snapshot.server_time).toLocaleTimeString("en-GB");

  el.rooms.replaceChildren();
  el.deviceList.replaceChildren();

  for (const room of snapshot.rooms) {
    const card = roomCardTemplate.content.cloneNode(true);
    const article = card.querySelector(".room-card");
    article.id = `room-${room.id}`;
    article.querySelector(".room-name").textContent = room.name;
    article.querySelector(".room-watts").textContent = `${room.watts} W`;
    article.querySelector(".room-svg-slot").innerHTML = buildRoomSVG(room);
    el.rooms.appendChild(card);

    for (const device of room.devices) {
      el.deviceList.appendChild(renderDeviceRow(device, room.name));
    }
  }
}

function updateDevice(payload) {
  const { device, room_id, room_watts, total_watts } = payload;

  const row = document.getElementById(`device-${device.id}`);
  if (row) {
    setStatusDot(row.querySelector(".status-dot"), device.state === "on");
    row.querySelector(".device-wattage").textContent = `${device.wattage} W`;
    const lastChanged = row.querySelector(".device-last-changed");
    lastChanged.textContent = formatRelativeTime(device.last_changed);
    lastChanged.dataset.ts = device.last_changed;
  }

  const roomCard = document.getElementById(`room-${room_id}`);
  if (roomCard) roomCard.querySelector(".room-watts").textContent = `${room_watts} W`;

  setDeviceSVGState(device);

  setTotalWattsInstant(total_watts);

  if (state.snapshot) {
    const room = state.snapshot.rooms.find((r) => r.id === room_id);
    if (room) {
      const idx = room.devices.findIndex((d) => d.id === device.id);
      if (idx !== -1) room.devices[idx] = device;
      room.watts = room_watts;
    }
    state.snapshot.total_watts = total_watts;
  }
}

function updateMeter(payload) {
  tweenTotalWatts(payload.total_watts);
  el.todayKwh.textContent = payload.today_kwh.toFixed(1);
  el.serverClock.textContent = new Date(payload.server_time).toLocaleTimeString("en-GB");

  if (state.snapshot) {
    state.snapshot.total_watts = payload.total_watts;
    state.snapshot.today_kwh = payload.today_kwh;
    state.snapshot.server_time = payload.server_time;
  }
}

function roomNameFor(roomId) {
  if (!roomId) return "";
  const room = state.snapshot && state.snapshot.rooms.find((r) => r.id === roomId);
  return room ? room.name : roomId;
}

function addAlert(alert, { silent = false } = {}) {
  const item = alertItemTemplate.content.cloneNode(true);
  item.querySelector(".alert-item").classList.add(`alert-item--${alert.kind}`);
  item.querySelector(".alert-kind").textContent = formatKind(alert.kind);
  const time = item.querySelector(".alert-time");
  time.textContent = formatRelativeTime(alert.created_at);
  time.dataset.ts = alert.created_at;
  item.querySelector(".alert-room").textContent = roomNameFor(alert.room_id);
  item.querySelector(".alert-message").textContent = alert.message;
  el.alertsList.prepend(item);
  if (!silent) playAlertBell();
}

async function loadAlertHistory() {
  try {
    const { alerts } = await (await fetch("/api/alerts?limit=50")).json();
    // API returns newest-first; addAlert prepends, so walk oldest-to-newest
    // to end up with the newest alert on top, same order as live alert_new.
    for (const alert of [...alerts].reverse()) {
      addAlert(alert, { silent: true });
    }
  } catch (err) {
    console.error("Alert history fetch failed", err);
  }
}

function ensureAudioContext() {
  if (!state.audioCtx) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) state.audioCtx = new AudioCtx();
  }
  return state.audioCtx;
}

function playAlertBell() {
  if (state.alertsMuted) return;
  const ctx = ensureAudioContext();
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.value = 880;
  gain.gain.setValueAtTime(0.15, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.4);
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.4);
}

el.bellToggle.addEventListener("click", () => {
  state.alertsMuted = !state.alertsMuted;
  if (!state.alertsMuted) ensureAudioContext();
  el.bellToggle.textContent = state.alertsMuted ? "🔕" : "🔔";
  el.bellToggle.setAttribute("aria-pressed", String(!state.alertsMuted));
  el.bellToggle.title = state.alertsMuted ? "Alert sound: muted" : "Alert sound: on";
});

function setOnline(isOnline) {
  setStatusDot(el.onlineDot, isOnline);
  el.onlineText.textContent = isOnline ? "Live" : "Offline";
  el.offlineBanner.classList.toggle("hidden", isOnline);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    state.reconnectDelay = 500;
    setOnline(true);
  };

  ws.onmessage = (event) => {
    const { type, payload } = JSON.parse(event.data);
    switch (type) {
      case "snapshot":
        render(payload);
        break;
      case "device_change":
        updateDevice(payload);
        break;
      case "usage_tick":
        updateMeter(payload);
        break;
      case "alert_new":
        addAlert(payload.alert);
        break;
    }
  };

  ws.onclose = () => {
    setOnline(false);
    setTimeout(connect, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 5000);
  };
}

async function init() {
  try {
    const snapshot = await (await fetch("/api/state")).json();
    render(snapshot);
  } catch (err) {
    console.error("Initial fetch failed", err);
  }
  await loadAlertHistory();
  connect();
}

// Relative "last changed" / alert timestamps go stale without a new event; refresh them periodically.
setInterval(() => {
  if (!state.snapshot) return;
  document.querySelectorAll("[data-ts]").forEach((node) => {
    node.textContent = formatRelativeTime(node.dataset.ts);
  });
}, 30000);

init();
