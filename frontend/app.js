const state = {
  snapshot: null,
  reconnectDelay: 500,
};

const el = {
  totalWatts: document.getElementById("total-watts"),
  todayKwh: document.getElementById("today-kwh"),
  serverClock: document.getElementById("server-clock"),
  onlineDot: document.getElementById("online-dot"),
  onlineText: document.getElementById("online-text"),
  offlineBanner: document.getElementById("offline-banner"),
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

  el.totalWatts.textContent = snapshot.total_watts;
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

  el.totalWatts.textContent = total_watts;

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
  el.totalWatts.textContent = payload.total_watts;
  el.todayKwh.textContent = payload.today_kwh.toFixed(1);
  el.serverClock.textContent = new Date(payload.server_time).toLocaleTimeString("en-GB");

  if (state.snapshot) {
    state.snapshot.total_watts = payload.total_watts;
    state.snapshot.today_kwh = payload.today_kwh;
    state.snapshot.server_time = payload.server_time;
  }
}

function addAlert(alert) {
  const item = alertItemTemplate.content.cloneNode(true);
  item.querySelector(".alert-kind").textContent = formatKind(alert.kind);
  const time = item.querySelector(".alert-time");
  time.textContent = formatRelativeTime(alert.created_at);
  time.dataset.ts = alert.created_at;
  item.querySelector(".alert-message").textContent = alert.message;
  el.alertsList.prepend(item);
}

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
