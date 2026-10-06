"""Request-body size limit for uploads (docs/DECISIONS.md D24).

Why this is middleware and not a check in the route: FastAPI reads and parses the whole
multipart body before it runs any dependency or the handler, so a check there runs only
after the full upload has been received and spooled to disk. This middleware sits in
front of that and stops early:

1. A declared Content-Length over the limit gets 413 before a single body byte is read.
2. Otherwise (no header, chunked, or a header that understates the size) it counts bytes
   as they arrive and raises 413 as soon as the count passes the limit.

The limit here covers the whole request body, so it includes an allowance for multipart
framing. The route still checks the exact file size against the file limit.
"""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

UPLOAD_PATHS = frozenset({"/api/files", "/api/files/"})


class UploadSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int, limit_label: str) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.detail = f"Maximum upload size is {limit_label}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] in UPLOAD_PATHS
        ):
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            response = JSONResponse({"detail": self.detail}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    # Must be an HTTPException: FastAPI re-raises those from body parsing
                    # (fastapi/routing.py), but turns any other exception into a 400.
                    raise HTTPException(status_code=413, detail=self.detail)
            return message

        await self.app(scope, counting_receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # The server rejects malformed headers itself.
    return None
