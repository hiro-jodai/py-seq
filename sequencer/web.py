"""FastAPI app: WebSocket control channel + static browser UI."""
import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .core import MAX_BARS, STEPS_PER_BAR, Sequencer


def build_app(seq: Sequencer, web_dir):
    app = FastAPI(title="PI-SEQ")
    clients = set()
    loop_ref = {"loop": None}

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

    seq.set_state_listener(lambda: broadcast({"type": "state", **seq.get_state()}))
    seq.set_step_listener(
        lambda bar, step: broadcast({"type": "step", "bar": bar, "step": step})
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
        elif t == "set_track_vel":
            seq.set_track_vel(msg.get("track", 0), msg.get("velocity", 100))
        elif t == "set_track_mode":
            seq.set_track_mode(msg.get("track", 0), msg.get("mode", "fixed"))
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
            await websocket.send_text(
                json.dumps({"type": "state", **seq.get_state()}, ensure_ascii=False)
            )
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
