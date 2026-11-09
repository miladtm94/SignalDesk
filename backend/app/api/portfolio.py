from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status

from ...adapters.tradingagents_adapter import portfolio_context
from ..schemas import IbkrBookOut, PortfolioOut, PortfolioPreview, PortfolioQuote, PortfolioWrite
from ..services.ibkr_flex import IbkrFlexError, import_book
from ..services.portfolio import clear_book, load_book, quotes, save_book, used_by_default

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _out(book: dict | None, updated_at) -> dict:
    if book is None:
        return {"configured": False, "positions": [], "updated_at": None}
    return {"configured": True, **book, "use_by_default": used_by_default(), "updated_at": updated_at}


@router.get("", response_model=PortfolioOut)
def get_portfolio() -> dict:
    return _out(*load_book())


@router.put("", response_model=PortfolioOut)
def put_portfolio(payload: PortfolioWrite) -> dict:
    try:
        book, updated_at = save_book(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(book, updated_at)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio() -> Response:
    clear_book()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/ibkr", response_model=IbkrBookOut)
def import_from_ibkr() -> dict:
    """The positions and cash in the configured IBKR Flex Query; nothing is saved."""
    try:
        return import_book()
    except IbkrFlexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/preview", response_model=PortfolioPreview)
def preview(ticker: str = Query(min_length=1, max_length=32)) -> dict:
    """What the decision agents would read about the book for this ticker."""
    book, _ = load_book()
    if book is None:
        raise HTTPException(status_code=404, detail="No holdings are saved yet.")
    symbol = ticker.strip().upper()
    held = next((p for p in book["positions"] if p["ticker"].upper() == symbol), None)
    return {"ticker": symbol, "held": held, "context": portfolio_context(book, symbol)}


@router.get("/quotes", response_model=list[PortfolioQuote])
def get_quotes() -> list[dict]:
    book, _ = load_book()
    return quotes(book) if book else []
