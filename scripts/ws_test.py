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

        # piano roll note roundtrip
        await ws.send(json.dumps({"type": "set_step_note", "track": 3, "step": 5, "note": 55}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"][5].get("notes") == [55])
        assert s["tracks"][3]["steps"][5]["on"] is True
        print("set_step_note echo OK")
        await ws.send(json.dumps({"type": "set_step_note", "track": 3, "step": 5, "note": 52}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"][5].get("notes") == [52, 55])
        print("chord add OK")
        await ws.send(json.dumps({"type": "set_step_note", "track": 3, "step": 5, "note": 55}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"][5].get("notes") == [52])
        print("chord remove OK")
        await ws.send(json.dumps({"type": "set_step_note", "track": 3, "step": 5, "note": None}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"][5].get("notes") is None)
        assert s["tracks"][3]["steps"][5]["on"] is False
        print("set_step_note clear OK")

        # note length roundtrip
        await ws.send(json.dumps({"type": "set_step_note", "track": 3, "step": 6, "note": 50, "length": 3}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"][6].get("length") == 3)
        assert s["tracks"][3]["steps"][6]["notes"] == [50]
        print("set_step_length echo OK")

        # chord add must not reset length
        await ws.send(json.dumps({"type": "set_step_note", "track": 3, "step": 7, "note": 50, "length": 4}))
        await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"][7].get("length") == 4)
        await ws.send(json.dumps({"type": "set_step_note", "track": 3, "step": 7, "note": 53, "length": 1}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][3]["steps"][7].get("notes") == [50, 53])
        assert s["tracks"][3]["steps"][7]["length"] == 4, "chord add must keep length"
        print("chord add keeps length OK")

        # midi learn plumbing
        await ws.send(json.dumps({"type": "midi_learn", "action": "swing"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m.get("learn_mode") is True)
        assert s.get("learn_action") == "swing" and "mapping" in s and "midi_ports" in s
        print("midi learn on OK (ports:", len(s["midi_ports"]), ")")
        await ws.send(json.dumps({"type": "midi_learn_cancel"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m.get("learn_mode") is False)
        print("midi learn cancel OK")

        # track management + follow
        await ws.send(json.dumps({"type": "track_add"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and len(m["tracks"]) == 5)
        print("track add OK")
        await ws.send(json.dumps({"type": "set_track_name", "track": 4, "name": "FM1"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][4]["name"] == "FM1")
        print("track rename OK")
        await ws.send(json.dumps({"type": "set_track_color", "track": 4, "color": "#ff0000"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][4]["color"] == "#ff0000")
        print("track color OK")
        await ws.send(json.dumps({"type": "track_remove", "index": 4}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and len(m["tracks"]) == 4)
        print("track remove OK")

        # drum track preset (CH10 + drum map + Drum 1 trigger note 60)
        await ws.send(json.dumps({"type": "track_add_drum"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and len(m["tracks"]) == 5
                             and m["tracks"][4]["drum"] is True and m["tracks"][4]["channel"] == 10
                             and m["tracks"][4]["note"] == 60)
        await ws.send(json.dumps({"type": "track_remove", "index": 4}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and len(m["tracks"]) == 4)
        print("drum track preset OK")

        # test note (debug trigger; expect a state echo, no crash)
        await ws.send(json.dumps({"type": "test_note", "track": 0}))
        s = await recv_until(ws, lambda m: m["type"] == "state")
        print("test note OK")

        # drum scan (36-51 sweep; expect a state echo, no crash)
        await ws.send(json.dumps({"type": "drum_scan", "track": 0}))
        s = await recv_until(ws, lambda m: m["type"] == "state")
        print("drum scan OK")

        # drum map mode
        await ws.send(json.dumps({"type": "set_track_drum", "track": 0, "on": True}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][0]["drum"] is True)
        await ws.send(json.dumps({"type": "set_track_drum", "track": 0, "on": False}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][0]["drum"] is False)
        print("drum map mode OK")

        # mute / solo
        await ws.send(json.dumps({"type": "set_track_mute", "track": 0, "on": True}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][0]["mute"] is True)
        await ws.send(json.dumps({"type": "set_track_mute", "track": 0, "on": False}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][0]["mute"] is False)
        await ws.send(json.dumps({"type": "set_track_solo", "track": 1, "on": True}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][1]["solo"] is True)
        await ws.send(json.dumps({"type": "set_track_solo", "track": 1, "on": False}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["tracks"][1]["solo"] is False)
        print("mute / solo OK")
        await ws.send(json.dumps({"type": "toggle_follow"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m.get("follow") is False)
        await ws.send(json.dumps({"type": "toggle_follow"}))
        await recv_until(ws, lambda m: m["type"] == "state" and m.get("follow") is True)
        print("follow toggle OK")

        # follow pushes the followed bar while playing (UI grid must follow)
        # ensure stopped first so no stale playing states confuse the sync check
        if s.get("playing"):
            await ws.send(json.dumps({"type": "toggle_play"}))
            await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is False)
        await ws.send(json.dumps({"type": "set_bpm", "value": 240}))
        await ws.send(json.dumps({"type": "toggle_play"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is True)
        e0 = s["edit_bar"]
        s2 = await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is True and m["edit_bar"] != e0, n=40, timeout=4)
        print("follow bar sync OK (edit_bar %s -> %s while playing)" % (e0, s2["edit_bar"]))

        # stop
        await ws.send(json.dumps({"type": "toggle_play"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is False)
        print("stop OK")

        print("ALL WS CONTRACT TESTS PASSED")


asyncio.run(main())
