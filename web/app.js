/* PI-SEQ browser UI — vanilla JS, no build step. */
const $ = (id) => document.getElementById(id);

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
function noteName(n) { return NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1); }

const SCALES = [
  "chromatic", "minor_pentatonic", "major_pentatonic",
  "natural_minor", "natural_major", "blues", "whole_tone", "dorian",
];
const TRACK_COLORS = ["#22d3ee", "#f472b6", "#fbbf24", "#a78bfa"];

const MIDI_ACTIONS = [
  ["bpm", "BPM"], ["swing", "SWING"], ["humanize_time", "JIT TIME"], ["humanize_velocity", "VEL JIT"],
  ["vel:0", "KICK VEL"], ["vel:1", "SNARE VEL"], ["vel:2", "HAT VEL"], ["vel:3", "BASS VEL"],
  ["note:0", "KICK NOTE"], ["note:1", "SNARE NOTE"], ["note:2", "HAT NOTE"], ["note:3", "BASS NOTE"],
  ["toggle_play", "PLAY/STOP"], ["pattern:0", "P1"], ["pattern:1", "P2"], ["pattern:2", "P3"], ["pattern:3", "P4"],
  ["rec", "REC TOGGLE"], ["song_toggle", "SONG TOGGLE"], ["clear_auto", "CLR AUT"],
  ["randomize:0", "KICK RND"], ["randomize:1", "SNARE RND"], ["randomize:2", "HAT RND"], ["randomize:3", "BASS RND"],
];
(function populateMidiActions() {
  const sel = $("midiActionSelect");
  MIDI_ACTIONS.forEach(([val, label]) => {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = label;
    sel.appendChild(opt);
  });
})();

let state = null;
let probMode = false;
let live = { bar: 0, step: -1, pattern: 0, songPos: -1 };
let ws = null;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "state") { state = msg; render(); }
    else if (msg.type === "step") {
      live.bar = msg.bar; live.step = msg.step;
      live.pattern = msg.pattern; live.songPos = msg.song_pos;
      renderLive();
    }
  };
  ws.onclose = () => setTimeout(connect, 1000);
}
function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

/* ------------------------------------------------------------------ render */
function render() {
  if (!state) return;
  $("playBtn").textContent = state.playing ? "■" : "▶";
  $("bpmInput").value = state.bpm;
  $("barLabel").textContent = `bar ${state.edit_bar + 1}/${state.pattern_length}`;
  $("lenInput").value = state.pattern_length;
  $("swingRange").value = state.swing;
  $("swingVal").textContent = `${state.swing}%`;
  $("humanizeTime").value = state.humanize_time;
  $("humanizeTimeVal").textContent = `${state.humanize_time}ms`;
  $("humanizeVel").value = state.humanize_velocity;
  $("humanizeVelVal").textContent = `${state.humanize_velocity}%`;
  $("recBtn").classList.toggle("active", state.recording);
  $("autoCount").textContent = `aut:${state.automation_count}`;
  $("midiLabel").textContent = `midi: ${state.midi_port}`;
  $("probModeBtn").classList.toggle("active", probMode);
  $("songBtn").classList.toggle("active", state.song_on);
  $("songLenInput").value = state.song_len;
  ["p1", "p2", "p3", "p4"].forEach((id, i) => {
    $(id).classList.toggle("active", i === state.current_pattern);
  });

  // MIDI bar
  const portSel = $("midiPortSelect");
  const portOpts = portSel.options;
  if (portOpts.length !== state.midi_ports.length ||
      (state.midi_ports.length > 0 && portOpts[0] && portOpts[0].value !== state.midi_ports[0])) {
    portSel.innerHTML = "";
    state.midi_ports.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      portSel.appendChild(opt);
    });
  }
  if (state.midi_in && state.midi_ports.includes(state.midi_in)) portSel.value = state.midi_in;
  $("midiInLabel").textContent = state.midi_in || (state.midi_error ? `err: ${state.midi_error}` : "no port");
  $("midiLearnBtn").classList.toggle("active", state.learn_mode);
  $("midiLearnBtn").textContent = state.learn_mode ? "LEARN…" : "LEARN";
  const mapList = $("midiMapList");
  mapList.innerHTML = "";
  state.mapping.forEach((m) => {
    const chip = document.createElement("span");
    chip.className = "map-chip";
    chip.textContent = `${m.type === "cc" ? "CC" : "NOTE"} ${m.number} → ${m.action} [${m.rel ? "rel" : "abs"}]`;
    chip.title = "click: toggle rel/abs · right-click: remove";
    chip.addEventListener("click", () => send({ type: "midi_map_rel", index: m.index, rel: !m.rel }));
    chip.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      send({ type: "midi_map_remove", index: m.index });
    });
    mapList.appendChild(chip);
  });

  const grid = $("grid");
  grid.innerHTML = "";
  state.tracks.forEach((tr, ti) => {
    const row = document.createElement("div");
    row.className = "track";

    const left = document.createElement("div");
    left.className = "track-ctl";
    left.innerHTML = `
      <span class="tname" style="color:${TRACK_COLORS[ti]}">${tr.name}</span>
      <select class="tnote" title="note"></select>
      <button class="tmode" title="fixed / scale-random">${tr.mode === "scale" ? "RND" : "FX"}</button>
      <select class="tscale" title="scale"></select>
      <input class="tvel" type="number" min="1" max="127" value="${tr.velocity}" title="velocity">
      <button class="tdice" title="randomize pattern">🎲</button>
    `;
    const noteSel = left.querySelector(".tnote");
    for (let n = 0; n <= 127; n++) {
      const opt = document.createElement("option");
      opt.value = n;
      opt.textContent = noteName(n);
      noteSel.appendChild(opt);
    }
    noteSel.value = tr.note;
    const scaleSel = left.querySelector(".tscale");
    SCALES.forEach((sc) => {
      const opt = document.createElement("option");
      opt.value = sc;
      opt.textContent = sc;
      scaleSel.appendChild(opt);
    });
    scaleSel.value = tr.scale;

    noteSel.addEventListener("change", (e) => send({ type: "param", param: `note:${ti}`, value: parseInt(e.target.value) }));
    left.querySelector(".tmode").addEventListener("click", () =>
      send({ type: "set_track_mode", track: ti, mode: tr.mode === "scale" ? "fixed" : "scale" }));
    scaleSel.addEventListener("change", (e) => send({ type: "set_track_scale", track: ti, scale: e.target.value }));
    left.querySelector(".tvel").addEventListener("change", (e) =>
      send({ type: "param", param: `vel:${ti}`, value: parseInt(e.target.value) }));
    left.querySelector(".tdice").addEventListener("click", () => send({ type: "randomize_track", track: ti }));

    const cells = document.createElement("div");
    cells.className = "cells";
    tr.steps.forEach((st, si) => {
      const c = document.createElement("div");
      c.className = "cell" + (st.on ? " on" : " off");
      c.style.setProperty("--c", TRACK_COLORS[ti]);
      c.style.opacity = st.on ? 0.45 + 0.55 * (st.prob / 100) : 1;
      c.dataset.track = ti;
      c.dataset.step = si;
      c.innerHTML = `<span class="prob">${st.prob}%</span>`;
      c.addEventListener("click", () => {
        if (probMode) {
          const p = parseInt($("probSlider").value);
          send({ type: "param", param: `prob:${ti}:${state.edit_bar}:${si}`, value: p });
        } else {
          send({ type: "set_step", track: ti, step: si, on: !st.on });
        }
      });
      cells.appendChild(c);
    });

    row.appendChild(left);
    row.appendChild(cells);
    grid.appendChild(row);
  });

  // song row
  const songRow = document.createElement("div");
  songRow.className = "track song-row" + (state.song_on ? "" : " dim");
  const songLabel = document.createElement("span");
  songLabel.className = "song-label";
  songLabel.textContent = "SONG";
  songRow.appendChild(songLabel);
  const songCells = document.createElement("div");
  songCells.className = "cells";
  for (let i = 0; i < state.song.length; i++) {
    const c = document.createElement("div");
    const pat = state.song[i];
    c.className = "cell pat-cell";
    c.style.setProperty("--c", TRACK_COLORS[pat]);
    c.textContent = pat + 1;
    c.dataset.songIdx = i;
    c.addEventListener("click", () => {
      const nxt = (state.song[i] + 1) % 4;
      send({ type: "set_song_entry", index: i, pattern: nxt });
    });
    songCells.appendChild(c);
  }
  songRow.appendChild(songCells);
  grid.appendChild(songRow);
  renderLive();
}

function renderLive() {
  if (!state) return;
  const rows = document.querySelectorAll(".track:not(.song-row)");
  rows.forEach((rowEl) => {
    rowEl.querySelectorAll(".cell").forEach((c) => {
      const si = parseInt(c.dataset.step);
      const playing = state.playing && live.bar === state.edit_bar && live.step === si;
      c.classList.toggle("live", playing);
    });
  });
  // pattern + song position indicators
  ["p1", "p2", "p3", "p4"].forEach((id, i) => {
    $(id).classList.toggle("active", i === live.pattern);
  });
  const songRow = document.querySelector(".song-row");
  if (songRow) {
    songRow.querySelectorAll(".pat-cell").forEach((c) => {
      c.classList.toggle("live", state.playing && parseInt(c.dataset.songIdx) === live.songPos);
    });
  }
  $("posLabel").textContent = state.playing ? `${live.bar + 1}:${live.step + 1}` : "--";
}

/* --------------------------------------------------------------- controls */
$("playBtn").onclick = () => send({ type: "toggle_play" });
$("bpmInput").onchange = (e) => send({ type: "param", param: "bpm", value: parseInt(e.target.value) });
$("barPrev").onclick = () => send({ type: "set_edit_bar", value: state.edit_bar - 1 });
$("barNext").onclick = () => send({ type: "set_edit_bar", value: state.edit_bar + 1 });
$("lenInput").onchange = (e) => send({ type: "set_pattern_length", value: parseInt(e.target.value) });
$("swingRange").oninput = (e) => {
  $("swingVal").textContent = `${e.target.value}%`;
  send({ type: "set_swing", value: parseInt(e.target.value) });
};
$("humanizeTime").oninput = (e) => {
  $("humanizeTimeVal").textContent = `${e.target.value}ms`;
  send({ type: "set_humanize", time_ms: parseInt(e.target.value) });
};
$("humanizeVel").oninput = (e) => {
  $("humanizeVelVal").textContent = `${e.target.value}%`;
  send({ type: "set_humanize", velocity: parseInt(e.target.value) });
};
$("recBtn").onclick = () => send({ type: state.recording ? "rec_off" : "rec_on" });
$("clearAutoBtn").onclick = () => send({ type: "clear_automation" });
$("probModeBtn").onclick = () => { probMode = !probMode; render(); };
$("probSlider").oninput = (e) => { $("probSliderVal").textContent = e.target.value; };
$("songBtn").onclick = () => send({ type: "toggle_song" });
$("songLenInput").onchange = (e) => send({ type: "set_song_len", value: parseInt(e.target.value) });
$("midiOpenBtn").onclick = () => send({ type: "midi_set_port", port: $("midiPortSelect").value });
$("midiLearnBtn").onclick = () => {
  if (state.learn_mode) send({ type: "midi_learn_cancel" });
  else send({ type: "midi_learn", action: $("midiActionSelect").value });
};
$("midiMapClearBtn").onclick = () => send({ type: "midi_map_clear" });
["p1", "p2", "p3", "p4"].forEach((id, i) => {
  $(id).onclick = () => send({ type: "set_pattern", index: i });
});

window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.code === "Space") { e.preventDefault(); send({ type: "toggle_play" }); }
  if (e.code === "ArrowLeft") send({ type: "set_edit_bar", value: state.edit_bar - 1 });
  if (e.code === "ArrowRight") send({ type: "set_edit_bar", value: state.edit_bar + 1 });
});

connect();
