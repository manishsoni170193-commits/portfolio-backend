from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    # Allow our Netlify dashboard (or any frontend) to call this API from the browser
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

# Yahoo's quoteSummary endpoint (used for analyst data) requires a session cookie + "crumb" token.
# We fetch and cache these once, refreshing only if a request starts failing.
_yahoo_session = {"cookies": None, "crumb": None}

def _get_yahoo_crumb():
    if _yahoo_session["crumb"]:
        return _yahoo_session["cookies"], _yahoo_session["crumb"]

    sess = requests.Session()
    sess.headers.update(HEADERS)
    # Visit the main site first to receive Yahoo's session cookies
    sess.get("https://fc.yahoo.com", timeout=10)
    crumb_resp = sess.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
    crumb = crumb_resp.text.strip()

    _yahoo_session["cookies"] = sess.cookies
    _yahoo_session["crumb"] = crumb
    return sess.cookies, crumb

@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Portfolio data API is running"})

@app.route("/api/quote/<symbol>")
def get_quote(symbol):
    """
    Fetches daily price history for an NSE stock from Yahoo Finance.
    Example: /api/quote/RVNL  ->  fetches RVNL.NS
    """
    ticker = symbol.upper() + ".NS"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": "1d", "range": "1y"}

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("chart", {}).get("result")
        if not result:
            error_desc = data.get("chart", {}).get("error", {}).get("description", "No data found")
            return jsonify({"success": False, "error": error_desc}), 404

        r = result[0]
        closes = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
        meta = r["meta"]

        if len(closes) < 20:
            return jsonify({"success": False, "error": "Insufficient price history"}), 404

        return jsonify({
            "success": True,
            "symbol": symbol.upper(),
            "currentPrice": meta.get("regularMarketPrice"),
            "previousClose": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "closes": closes
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Failed to reach Yahoo Finance: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {str(e)}"}), 500


@app.route("/api/analyst/<symbol>")
def get_analyst(symbol):
    """
    Fetches analyst consensus rating, analyst count, and price target for an NSE stock.
    Example: /api/analyst/RVNL
    """
    ticker = symbol.upper() + ".NS"
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    params = {"modules": "financialData,recommendationTrend"}

    try:
        cookies, crumb = _get_yahoo_crumb()
        params["crumb"] = crumb
        resp = requests.get(url, headers=HEADERS, params=params, cookies=cookies, timeout=10)
        if resp.status_code == 401:
            # crumb may have expired — refresh once and retry
            _yahoo_session["crumb"] = None
            cookies, crumb = _get_yahoo_crumb()
            params["crumb"] = crumb
            resp = requests.get(url, headers=HEADERS, params=params, cookies=cookies, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("quoteSummary", {}).get("result")
        if not result:
            return jsonify({"success": False, "error": "No analyst data found"}), 404

        r = result[0]
        fin = r.get("financialData", {})
        rec = r.get("recommendationTrend", {}).get("trend", [])

        rec_key = fin.get("recommendationKey", "").upper().replace("_", " ")
        target_mean = fin.get("targetMeanPrice", {}).get("raw")
        current_price = fin.get("currentPrice", {}).get("raw")
        num_analysts = fin.get("numberOfAnalystOpinions", {}).get("raw")

        # latest period breakdown (strongBuy, buy, hold, sell, strongSell)
        breakdown = rec[0] if rec else {}

        upside_pct = None
        if target_mean and current_price:
            upside_pct = round(((target_mean - current_price) / current_price) * 100, 2)

        return jsonify({
            "success": True,
            "symbol": symbol.upper(),
            "rating": rec_key or None,
            "numAnalysts": num_analysts,
            "targetMeanPrice": target_mean,
            "upsidePct": upside_pct,
            "breakdown": {
                "strongBuy": breakdown.get("strongBuy"),
                "buy": breakdown.get("buy"),
                "hold": breakdown.get("hold"),
                "sell": breakdown.get("sell"),
                "strongSell": breakdown.get("strongSell")
            }
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Failed to reach data source: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {str(e)}"}), 500


@app.route("/api/news/<symbol>")
def get_news(symbol):
    """
    Fetches recent news headlines for an NSE stock via Yahoo Finance search.
    Example: /api/news/RVNL
    """
    ticker = symbol.upper() + ".NS"
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    params = {"q": ticker, "newsCount": 5, "quotesCount": 0}

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        news_items = data.get("news", [])
        headlines = [
            {
                "title": n.get("title"),
                "publisher": n.get("publisher"),
                "link": n.get("link"),
                "time": n.get("providerPublishTime")
            }
            for n in news_items[:5]
        ]

        return jsonify({"success": True, "symbol": symbol.upper(), "news": headlines})

    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Failed to reach data source: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {str(e)}"}), 500


@app.route("/api/fundamentals/<symbol>")
def get_fundamentals(symbol):
    """
    Fetches Screener-relevant fundamentals: PE, PEG, ROE, Debt/Equity, Promoter/Insider holding, Volume.
    Note: Yahoo Finance does not provide ROCE directly for Indian stocks — ROE is included as the
    closest available substitute. Promoter holding uses Yahoo's insider-holding field, which is a
    reasonable proxy but may not exactly match NSE's official promoter shareholding disclosure.
    Example: /api/fundamentals/RVNL
    """
    ticker = symbol.upper() + ".NS"
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    params = {"modules": "summaryDetail,defaultKeyStatistics,financialData,majorHoldersBreakdown"}

    try:
        cookies, crumb = _get_yahoo_crumb()
        params["crumb"] = crumb
        resp = requests.get(url, headers=HEADERS, params=params, cookies=cookies, timeout=10)
        if resp.status_code == 401:
            _yahoo_session["crumb"] = None
            cookies, crumb = _get_yahoo_crumb()
            params["crumb"] = crumb
            resp = requests.get(url, headers=HEADERS, params=params, cookies=cookies, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("quoteSummary", {}).get("result")
        if not result:
            return jsonify({"success": False, "error": "No fundamentals data found"}), 404

        r = result[0]
        summary = r.get("summaryDetail", {})
        stats = r.get("defaultKeyStatistics", {})
        fin = r.get("financialData", {})
        holders = r.get("majorHoldersBreakdown", {})

        def raw(d, key):
            v = d.get(key)
            return v.get("raw") if isinstance(v, dict) else v

        volume = raw(summary, "volume")
        avg_volume = raw(summary, "averageVolume")
        volume_ratio = round(volume / avg_volume, 2) if volume and avg_volume else None

        return jsonify({
            "success": True,
            "symbol": symbol.upper(),
            "pe": raw(summary, "trailingPE"),
            "peg": raw(stats, "pegRatio"),
            "roe": raw(fin, "returnOnEquity"),
            "debtToEquity": raw(fin, "debtToEquity"),
            "promoterHoldingPct": raw(holders, "insidersPercentHeld"),
            "volume": volume,
            "avgVolume": avg_volume,
            "volumeRatio": volume_ratio
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Failed to reach data source: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {str(e)}"}), 500


# ---------- Mutual Fund data (AMFI) ----------
_amfi_cache = {"data": None, "fetched_at": 0}

def _load_amfi_data():
    """Downloads and parses AMFI's daily NAV file, cached for 6 hours to avoid repeated large downloads."""
    import time
    now = time.time()
    if _amfi_cache["data"] and (now - _amfi_cache["fetched_at"] < 6 * 3600):
        return _amfi_cache["data"]

    resp = requests.get("https://www.amfiindia.com/spages/NAVAll.txt", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    lines = resp.text.splitlines()

    funds = []
    for line in lines:
        parts = line.split(";")
        if len(parts) == 6 and parts[0].strip().isdigit():
            scheme_code, isin_growth, isin_div, name, nav, date = [p.strip() for p in parts]
            funds.append({
                "schemeCode": scheme_code,
                "isinGrowth": isin_growth or None,
                "isinDividend": isin_div or None,
                "name": name,
                "nav": float(nav) if nav else None,
                "date": date
            })
    _amfi_cache["data"] = funds
    _amfi_cache["fetched_at"] = now
    return funds


@app.route("/api/mf/isin/<isin>")
def get_mf_by_isin(isin):
    """Look up a mutual fund's current NAV by its ISIN (most reliable — avoids name-matching errors)."""
    try:
        funds = _load_amfi_data()
        isin = isin.strip().upper()
        match = next((f for f in funds if f["isinGrowth"] == isin or f["isinDividend"] == isin), None)
        if not match:
            return jsonify({"success": False, "error": "No fund found for this ISIN"}), 404
        return jsonify({"success": True, **match})
    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Failed to reach AMFI: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {str(e)}"}), 500


@app.route("/api/mf/search")
def search_mf():
    """Search mutual funds by name keyword. Example: /api/mf/search?q=parag parikh flexi"""
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify({"success": False, "error": "Provide a search query with ?q="}), 400
    try:
        funds = _load_amfi_data()
        matches = [f for f in funds if query in f["name"].lower()][:15]
        return jsonify({"success": True, "results": matches})
    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Failed to reach AMFI: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
