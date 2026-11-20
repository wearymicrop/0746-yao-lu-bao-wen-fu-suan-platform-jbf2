from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.errors import BusinessError
from app.modules.m8_shift import service

router = APIRouter(prefix="/api/shifts", tags=["班次总账"])


class Signoff(BaseModel):
    operator: str = ""


@router.get("/{shift}")
def inspect(request: Request, shift: str):
    return service.view(request.app.state.ledger, service.shift_id(shift))


@router.post("/{shift}/packets")
async def packets(request: Request, shift: str):
    try:
        body = await request.json()
    except (UnicodeError, ValueError) as error:
        raise BusinessError("BAD_JSON", "请输入有效的 UTF-8 JSON") from error
    return service.accept(request.app.state.ledger, service.shift_id(shift), body)


@router.get("/{shift}/handovers")
def handovers(request: Request, shift: str):
    shift = service.shift_id(shift)
    return {
        "shift": shift,
        "handovers": [
            {
                "id": record["id"],
                "operator": record["operator"],
                "decision": record["decision"],
                "ruling": record["ruling"],
            }
            for record in request.app.state.ledger.handovers(shift)
        ],
    }


@router.post("/{shift}/handover")
def handover(request: Request, shift: str, data: Signoff):
    return service.handover(
        request.app.state.ledger, service.shift_id(shift), data.operator
    )
