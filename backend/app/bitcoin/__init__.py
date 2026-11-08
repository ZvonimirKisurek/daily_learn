"""Phase 11: Bitcoin intelligence. Ingest price and news, compute indicators in code, ask the LLM for a forecast.

    ingest.py      spot price + daily closes (CoinGecko, falling back to Coinbase) and RSS news (bitcoin_sources.yaml)
    technicals.py  pure maths: SMA, RSI, realised volatility, 1-sigma moves (the model never does arithmetic)
    schemas.py     BitcoinPredictionOutput: the LLM contract and its validators (closed-world citations, vol band, no advice)
    predictor.py   builds the prompt, calls StructuredLLM, stores the row
"""
