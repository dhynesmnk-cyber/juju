"""What every router of the internal API shares: its database session and its JSON responses."""
from collections.abc import Iterator
from decimal import Decimal
from typing import Annotated

from fastapi import Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from juju.db import session_factory


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def respond(data, cache: str | None = None, status: int = 200) -> JSONResponse:
    body = jsonable_encoder(data, custom_encoder={Decimal: lambda d: format(d, "f")})
    headers = {"Cache-Control": cache or "no-store"}
    return JSONResponse(body, status, headers=headers)
