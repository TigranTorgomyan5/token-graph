"""Orders API: accepts orders and reports their status."""
import signal
import uvicorn
from fastapi import FastAPI
from app.routers import orders

app = FastAPI()
app.include_router(orders.router)


@app.get("/health")
def health():
    return {"ok": True}


@app.on_event("shutdown")
async def close_pool():
    await orders.pool.close()


def main():
    signal.signal(signal.SIGTERM, lambda *_: None)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
