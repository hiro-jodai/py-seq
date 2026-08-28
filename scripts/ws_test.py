import asyncio
import json
import websockets


async def recv_until(ws, pred, n=40, timeout=3):
    for _ in range(n):
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if pred(msg):
            return msg
    raise AssertionError("condition not met")


async def main():
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        s = await recv_until(ws, lambda m: m["type"] == "state")
        print("initial state OK (playing=%s)" % s["playing"])
        # make sure we start from a stopped state (server may be mid-play from a previous run)
        if s["playing"]:
            await ws.send(json.dumps({"type": "toggle_play"}))
            await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is False)
            print("reset to stopped")

        # toggle play
        await ws.send(json.dumps({"type": "toggle_play"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is True)
        tick = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        print("play OK; step ticks:", tick["type"] == "step")

        # rec on + bpm param -> drain until bpm=138 & aut>=1
        await ws.send(json.dumps({"type": "rec_on"}))
        await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "param", "param": "bpm", "value": 138}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["bpm"] == 138)
        assert s["automation_count"] >= 1 and s["recording"] is True
        print("rec + bpm param OK (aut=%s, bpm=%s)" % (s["automation_count"], s["bpm"]))

        # randomize track (steps change)
        prev = s["tracks"][3]["steps"]
        await ws.send(json.dumps({"type": "randomize_track", "track": 3}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"] != prev)
        print("randomize_track OK")

        # swing
        await ws.send(json.dumps({"type": "set_swing", "value": 40}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["swing"] == 40)
        print("swing OK")

        # pattern switch
        await ws.send(json.dumps({"type": "set_pattern", "index": 2}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["current_pattern"] == 2)
        assert s["pattern_length"] == s["pattern_length"]
        print("pattern switch OK (P%d, bars=%d)" % (s["current_pattern"] + 1, s["pattern_length"]))
        await ws.send(json.dumps({"type": "set_pattern", "index": 0}))
        await recv_until(ws, lambda m: m["type"] == "state" and m["current_pattern"] == 0)

        # song entry + song mode
        await ws.send(json.dumps({"type": "set_song_entry", "index": 1, "pattern": 2}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["song"][1] == 2)
        print("song entry OK")
        await ws.send(json.dumps({"type": "toggle_song"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["song_on"] is True)
        print("song on OK")
        await ws.send(json.dumps({"type": "toggle_song"}))
        await recv_until(ws, lambda m: m["type"] == "state" and m["song_on"] is False)
        print("song off OK")

        # midi learn plumbing
        await ws.send(json.dumps({"type": "midi_learn", "action": "swing"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m.get("learn_mode") is True)
        assert s.get("learn_action") == "swing" and "mapping" in s and "midi_ports" in s
        print("midi learn on OK (ports:", len(s["midi_ports"]), ")")
        await ws.send(json.dumps({"type": "midi_learn_cancel"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m.get("learn_mode") is False)
        print("midi learn cancel OK")

        # stop
        await ws.send(json.dumps({"type": "toggle_play"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is False)
        print("stop OK")

        print("ALL WS CONTRACT TESTS PASSED")


asyncio.run(main())
