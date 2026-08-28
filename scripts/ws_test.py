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

        # stop
        await ws.send(json.dumps({"type": "toggle_play"}))
        s = await recv_until(ws, lambda m: m["type"] == "state" and m["playing"] is False)
        print("stop OK")

        print("ALL WS CONTRACT TESTS PASSED")


asyncio.run(main())
