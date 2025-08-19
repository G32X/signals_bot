from fastapi import FastAPI, Depends
from .db import Base, engine, get_db
from . import models
from sqlalchemy.orm import Session
from .signal_engine import run_signal_engine
from .universe import DEFAULT_TICKERS

Base.metadata.create_all(bind=engine)
app = FastAPI()

@app.get("/")
def root():
    return {"ok": True}

@app.post("/scan")
def scan(db: Session = Depends(get_db)):
    run_signal_engine(db, DEFAULT_TICKERS)
    return {"status": "scan complete"}
