import hoshino
import asyncio

bot = hoshino.init()
app = bot.asgi

if __name__ == '__main__':
    # Quart 0.14's run() uses the removed asyncio.gather(loop=...) on exit.
    # asyncio.run() owns the loop and safely cancels pending plugin tasks.
    asyncio.run(app.run_task(host=bot.config.HOST, port=bot.config.PORT,
                             debug=bot.config.DEBUG, use_reloader=False))
