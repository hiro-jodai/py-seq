"""FastAPI app: WebSocket control channel + static browser UI."""
import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .core import MAX_BARS, STEPS_PER_BAR, Sequencer
from .mapping import MidiMapper


def build_app(seq: Sequencer, web_dir, midi_in=None):
    app = FastAPI(title="PI-SEQ")
    mapper = MidiMapper(seq, port_name=midi_in)
    clients = set()
    loop_ref = {"loop": None}

    def state_msg():
        return {"type": "state", **seq.get_state(), **mapper.get_state()}

    async def send_all(msg):
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(clients):
            try:
                await ws.send_text(data)
            except Exception:
                clients.discard(ws)

    def broadcast(msg):
        loop = loop_ref["loop"]
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(send_all(msg), loop)

    seq.set_state_listener(lambda: broadcast(state_msg()))
    mapper.set_state_listener(lambda: broadcast(state_msg()))
    seq.set_step_listener(
        lambda bar, step: broadcast(
            {
                "type": "step",
                "bar": bar,
                "step": step,
                "pattern": seq.current_pattern,
                "song_pos": seq._song_entry if seq.song_on else -1,
            }
        )
    )

    def handle(msg):
        t = msg.get("type")
        if t == "play":
            seq.play()
        elif t == "stop":
            seq.stop()
        elif t == "toggle_play":
            seq.toggle_play()
        elif t == "set_bpm":
            seq.set_bpm(msg.get("value", 120))
        elif t == "set_swing":
            seq.set_swing(msg.get("value", 0))
        elif t == "set_pattern":
            seq.set_pattern(msg.get("index", 0))
        elif t == "set_song_entry":
            seq.set_song_entry(msg.get("index", 0), msg.get("pattern", 0))
        elif t == "set_song_len":
            seq.set_song_len(msg.get("value", 4))
        elif t == "toggle_song":
            seq.toggle_song()
        elif t == "set_song_on":
            if seq.song_on != bool(msg.get("on", True)):
                seq.toggle_song()
        elif t == "midi_set_port":
            port = msg.get("port") or None
            if port:
                mapper.open_port(port)
            else:
                mapper.close_port()
        elif t == "midi_set_out":
            seq.set_midi_out(msg.get("port") or None)
        elif t == "midi_learn":
            mapper.set_learn(msg.get("action", "swing"))
        elif t == "midi_learn_cancel":
            mapper.set_learn(None)
        elif t == "midi_map_rel":
            mapper.set_mapping_rel(msg.get("index", 0), bool(msg.get("rel", True)))
        elif t == "midi_map_remove":
            mapper.remove_mapping(msg.get("index", 0))
        elif t == "midi_map_clear":
            mapper.clear_mappings()
        elif t == "set_pattern_length":
            seq.set_pattern_length(msg.get("value", 1))
        elif t == "set_edit_bar":
            seq.set_edit_bar(msg.get("value", 0))
        elif t == "set_step":
            seq.set_step(
                msg.get("track", 0),
                msg.get("bar", seq.edit_bar),
                msg.get("step", 0),
                msg.get("on", False),
            )
        elif t == "set_step_note":
            seq.set_step_note(
                msg.get("track", 0),
                msg.get("bar", seq.edit_bar),
                msg.get("step", 0),
                msg.get("note"),
                msg.get("length"),
            )
        elif t == "set_step_length":
            seq.set_step_length(
                msg.get("track", 0),
                msg.get("bar", seq.edit_bar),
                msg.get("step", 0),
                msg.get("length", 1),
            )
        elif t == "set_prob":
            seq.set_prob(
                msg.get("track", 0),
                msg.get("bar", seq.edit_bar),
                msg.get("step", 0),
                msg.get("prob", 100),
            )
        elif t == "cycle_prob":
            seq.cycle_prob(
                msg.get("track", 0),
                msg.get("bar", seq.edit_bar),
                msg.get("step", 0),
            )
        elif t == "set_track_note":
            seq.set_track_note(msg.get("track", 0), msg.get("note", 36))
        elif t == "set_track_channel":
            seq.set_track_channel(msg.get("track", 0), msg.get("channel", 1))
        elif t == "set_track_out":
            seq.set_track_out(msg.get("track", 0), msg.get("port") or None)
        elif t == "track_add":
            seq.add_track()
        elif t == "track_add_drum":
            seq.add_drum_track()
        elif t == "test_note":
            seq.test_note(msg.get("track", 0))
        elif t == "track_remove":
            seq.remove_track(msg.get("index", 0))
        elif t == "set_track_name":
            seq.set_track_name(msg.get("track", 0), msg.get("name", ""))
        elif t == "set_track_color":
            seq.set_track_color(msg.get("track", 0), msg.get("color", ""))
        elif t == "set_follow":
            seq.set_follow(msg.get("on", True))
        elif t == "toggle_follow":
            seq.toggle_follow()
        elif t == "set_track_vel":
            seq.set_track_vel(msg.get("track", 0), msg.get("velocity", 100))
        elif t == "set_track_mode":
            seq.set_track_mode(msg.get("track", 0), msg.get("mode", "fixed"))
        elif t == "set_track_drum":
            seq.set_track_drum(msg.get("track", 0), msg.get("on", False))
        elif t == "set_track_mute":
            seq.set_track_mute(msg.get("track", 0), msg.get("on", False))
        elif t == "set_track_solo":
            seq.set_track_solo(msg.get("track", 0), msg.get("on", False))
        elif t == "set_track_scale":
            seq.set_track_scale(msg.get("track", 0), msg.get("scale", "minor_pentatonic"))
        elif t == "randomize_track":
            seq.randomize_track(msg.get("track", 0))
        elif t == "set_humanize":
            seq.set_humanize(time_ms=msg.get("time_ms"), velocity=msg.get("velocity"))
        elif t == "rec_on":
            seq.recording = True
            seq.notify_state()
        elif t == "rec_off":
            seq.recording = False
            seq.notify_state()
        elif t == "clear_automation":
            seq.clear_automation()
        elif t == "param":
            seq.record_param(msg.get("param", ""), msg.get("value", 0))
            seq._set_param(msg.get("param", ""), msg.get("value", 0))
        elif t == "get_state":
            pass

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        clients.add(websocket)
        loop_ref["loop"] = asyncio.get_running_loop()
        try:
            await websocket.send_text(json.dumps(state_msg(), ensure_ascii=False))
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                handle(msg)
                # echo full state back after every command so the UI stays in sync
                seq.notify_state()
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(websocket)

    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app
