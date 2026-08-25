from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from ..database import SessionLocal
from ..models import WatchlistItem
from ..schemas import WatchlistCreate, WatchlistOut

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistOut])
def list_watchlist():
    with SessionLocal() as session:
        return list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.added_at.desc())).all())


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def add_watchlist(payload: WatchlistCreate):
    with SessionLocal.begin() as session:
        item = session.get(WatchlistItem, payload.ticker)
        if item:
            item.notes = payload.notes
        else:
            item = WatchlistItem(**payload.model_dump())
            session.add(item)
        session.flush()
        session.refresh(item)
        return item


@router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watchlist(ticker: str):
    with SessionLocal.begin() as session:
        item = session.get(WatchlistItem, ticker.strip().upper())
        if item is None:
            raise HTTPException(status_code=404, detail="Ticker not found")
        session.delete(item)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
