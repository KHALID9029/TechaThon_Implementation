// Per-room device layout, mirroring the brief's top-view image: a light-fan-light
// row across the top of the room, a second fan lower, and a third light at the
// bottom (this pattern repeats identically in all 3 rooms in the brief).
const FLOORPLAN_LAYOUT = {
  "light.1": { cx: 34, cy: 26 },
  "fan.1": { cx: 120, cy: 26 },
  "light.2": { cx: 206, cy: 26 },
  "fan.2": { cx: 120, cy: 84 },
  "light.3": { cx: 120, cy: 128 },
};

const FAN_BLADE_ANGLES = [0, 120, 240];

function fanIcon(deviceId, cx, cy, on) {
  const blades = FAN_BLADE_ANGLES.map(
    (angle) => `<ellipse class="fan-blade" cx="0" cy="-13" rx="4" ry="13" transform="rotate(${angle})" />`
  ).join("");
  // Position (translate, an SVG attribute) and spin (a CSS transform animation) must live on
  // separate nested <g>s -- a CSS transform on an element fully replaces its transform
  // *attribute* rather than composing with it, which would strip the translate mid-spin.
  return `
    <g id="svg-device-${deviceId}" transform="translate(${cx},${cy})">
      <g class="fan-icon${on ? " fan--on" : ""}">
        ${blades}
        <circle class="fan-hub" r="3" />
      </g>
    </g>`;
}

function lightIcon(deviceId, cx, cy, on) {
  return `<circle id="svg-device-${deviceId}" class="light-dot${on ? " light--on" : ""}" cx="${cx}" cy="${cy}" r="9" />`;
}

function buildRoomSVG(room) {
  const parts = room.devices.map((device) => {
    const pos = FLOORPLAN_LAYOUT[`${device.kind}.${device.index}`];
    if (!pos) return "";
    const on = device.state === "on";
    return device.kind === "fan"
      ? fanIcon(device.id, pos.cx, pos.cy, on)
      : lightIcon(device.id, pos.cx, pos.cy, on);
  });

  return `
    <svg class="room-svg" viewBox="0 0 240 150" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${room.name} floor plan">
      <rect class="room-floor" x="4" y="4" width="232" height="142" rx="10" />
      ${parts.join("")}
    </svg>`;
}

function setDeviceSVGState(device) {
  const el = document.getElementById(`svg-device-${device.id}`);
  if (!el) return;
  const isOn = device.state === "on";
  if (device.kind === "fan") {
    const icon = el.querySelector(".fan-icon");
    if (icon) icon.classList.toggle("fan--on", isOn);
  } else {
    el.classList.toggle("light--on", isOn);
  }
}
