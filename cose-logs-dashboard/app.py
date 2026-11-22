import os
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
ES = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").rstrip("/")
INDEX = os.getenv("ELASTICSEARCH_INDEX", "microservices-logs-*")

def search_es(body):
    r = requests.post(f"{ES}/{INDEX}/_search", json=body, timeout=10)
    r.raise_for_status()
    return r.json()

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/logs")
def logs():
    filters = []
    for key in ("service", "correlation_id"):
        value = request.args.get(key, "").strip()
        if value:
            filters.append({"match_phrase": {key: value}})
    level = request.args.get("level", "").strip()
    if level:
        filters.append({"match_phrase": {"level": level.upper()}})
    q = request.args.get("q", "").strip()
    if q:
        filters.append({"multi_match": {"query": q, "fields": ["message", "event", "service", "correlation_id"]}})
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)
        data = search_es({
            "size": limit, "track_total_hits": True,
            "sort": [{"@timestamp": {"order": "desc", "unmapped_type": "date"}}],
            "query": {"bool": {"filter": filters}}
        })
        hits = data.get("hits", {})
        total = hits.get("total", 0)
        if isinstance(total, dict):
            total = total.get("value", 0)
        result = []
        for hit in hits.get("hits", []):
            src = hit.get("_source", {})
            result.append({
                "timestamp": src.get("@timestamp") or src.get("timestamp"),
                "service": src.get("service", "unknown"),
                "level": src.get("level", "INFO"),
                "event": src.get("event", ""),
                "message": src.get("message", ""),
                "correlation_id": src.get("correlation_id", ""),
                "raw": src
            })
        return jsonify({"total": total, "logs": result})
    except (requests.RequestException, ValueError) as e:
        return jsonify({"error": str(e), "elasticsearch": ES, "index": INDEX}), 502

@app.get("/api/stats")
def stats():
    try:
        data = search_es({
            "size": 1000, "sort": [{"@timestamp": {"order": "desc", "unmapped_type": "date"}}],
            "_source": ["service", "level"], "query": {"match_all": {}}
        })
        docs = [h.get("_source", {}) for h in data.get("hits", {}).get("hits", [])]
        levels = {"INFO": 0, "WARN": 0, "ERROR": 0, "DEBUG": 0}
        services = set()
        for d in docs:
            lv = str(d.get("level", "INFO")).upper()
            levels[lv] = levels.get(lv, 0) + 1
            if d.get("service"):
                services.add(d["service"])
        return jsonify({"loaded_documents": len(docs), "services": len(services), "levels": levels})
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502

@app.get("/health")
def health():
    try:
        r = requests.get(f"{ES}/_cluster/health", timeout=8)
        r.raise_for_status()
        return jsonify({"dashboard": "ok", "elasticsearch": r.json()})
    except requests.RequestException as e:
        return jsonify({"dashboard": "ok", "elasticsearch_error": str(e)}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
