"""What every router shares: the database session and the JSON response (no-store unless a
public endpoint says otherwise)."""
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


def encode(data):
    return jsonable_encoder(data, custom_encoder={Decimal: lambda d: format(d, "f")})


def respond(data, cache: str | None = None) -> JSONResponse:
    headers = {"Cache-Control": cache or "no-store"}
    return JSONResponse(encode(data), headers=headers)
