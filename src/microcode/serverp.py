import asyncio
import websockets

async def echo(websocket):
    async for message in websocket:
        print(f"Mensaje recibido del cliente: {message}")
        await websocket.send(f"Echo: {message}")

async def main():
    async with websockets.serve(echo, '0.0.0.0', 12345):
        print("Servidor WebSocket escuchando en ws://0.0.0.0:12345")
        await asyncio.Future()

asyncio.run(main())
