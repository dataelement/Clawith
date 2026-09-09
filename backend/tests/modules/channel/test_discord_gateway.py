import asyncio
import json
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView
from app.modules.channel.providers.discord import DiscordAdapter
from app.modules.channel.providers.discord_gateway import _WIRE_LOGGER
from app.modules.credential.public import Secret


async def test_gateway_identify_dm_mention_filter_resume_and_cancellation_closes_socket():
    seen, operations, closed = [], [], []
    complete = asyncio.Event()
    async def gateway(socket):
        connection = len(operations)
        try:
            await socket.send(json.dumps({"op":10,"d":{"heartbeat_interval":100}}))
            auth = json.loads(await socket.recv())
            operations.append(auth)
            assert auth["d"]["token"] == "secret-token"
            if connection == 0:
                assert auth["op"] == 2
                await socket.send(json.dumps({"op":0,"t":"READY","s":1,"d":{
                    "application":{"id":"123"},"user":{"id":"bot"},"session_id":"session",
                    "resume_gateway_url":"wss://gateway.discord.gg"}}))
                await socket.send(json.dumps({"op":0,"t":"MESSAGE_CREATE","s":2,"d":{
                    "id":"first","channel_id":"dm","author":{"id":"human"},"content":"hello"}}))
                pulse = json.loads(await socket.recv())
                assert pulse == {"op":1,"d":2}
                await socket.close(code=1001)
            else:
                assert auth["op"] == 6 and auth["d"]["session_id"] == "session" and auth["d"]["seq"] == 2
                await socket.send(json.dumps({"op":0,"t":"RESUMED","s":3,"d":{}}))
                for index, mentions in enumerate(([], [{"id":"bot"}])):
                    await socket.send(json.dumps({"op":0,"t":"MESSAGE_CREATE","s":4+index,"d":{
                        "id":str(index),"channel_id":"group","guild_id":"guild","author":{"id":"human"},
                        "content":"<@bot> question","mentions":mentions}}))
                async for raw in socket:
                    if json.loads(raw)["op"] == 1:
                        await socket.send('{"op":11,"d":null}')
        finally:
            closed.append(connection)
    async def accepted(result):
        seen.append(result.message)
        if len(seen) == 2:
            complete.set()
    async with serve(gateway, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        def connector(_):
            return connect(f"ws://127.0.0.1:{port}", logger=_WIRE_LOGGER, proxy=None)
        agent = uuid4()
        channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent,
            '{"connection_mode":"gateway"}')
        async with create_stateless_http_client() as http:
            adapter = DiscordAdapter(http, connector=connector)
            task = asyncio.create_task(adapter.listen(channel, Secret('{"version":1,"bot_token":"secret-token"}'), accepted))
            try:
                await asyncio.wait_for(complete.wait(), timeout=5)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    assert [message.text for message in seen] == ["hello", "question"]
    assert [message.group_id for message in seen] == [None, "group"]
    assert sorted(closed) == [0, 1]
