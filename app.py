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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
