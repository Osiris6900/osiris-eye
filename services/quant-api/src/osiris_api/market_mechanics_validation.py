"""JSON-safe validation errors for the market mechanics request contracts."""

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class MechanicsRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def route_handler(request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                # Pydantic includes the raw rejected input in errors. NaN/Inf
                # cannot be serialized by JSONResponse; expose field locations
                # and messages without echoing arbitrary request content.
                return JSONResponse(
                    status_code=422,
                    content={"detail": [
                        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                        for error in exc.errors()
                    ]},
                )

        return route_handler
