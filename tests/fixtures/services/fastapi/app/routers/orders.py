import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/v1/orders")
client = httpx.AsyncClient()


@router.post("/create")
async def create_order(body: dict):
    # client.post("/not-a-route") is an outgoing call, not a route
    await client.post("/payments/charge", json=body)
    return {"id": 1}


@router.get("/{order_id}")
async def get_order(order_id: int):
    return {"id": order_id}


@router.api_route("/{order_id}/cancel", methods=["POST", "DELETE"])
async def cancel_order(order_id: int):
    return {}
