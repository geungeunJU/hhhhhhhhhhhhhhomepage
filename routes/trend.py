import os
import base64
from flask import Blueprint, jsonify, current_app, send_file
from routes.auth import login_required

trend_bp = Blueprint("trend", __name__)

COMPANY_MAP = {
    "KATECH":     "자동차연구원",
    "HKMC":       "현대기아",
    "TESLA":      "테슬라",
    "BYD":        "BYD",
    "TOYOTA":     "토요타",
    "SHAOMI":     "샤오미",
    "Geely":      "Geely",
}

def _trend_folder(company: str) -> str:
    return os.path.join(current_app.static_folder, "trend", company)

@trend_bp.route("/api/trend/<company>", methods=["GET"])
@login_required
def get_trend_files(company):
    company_upper = company.upper()
    if company_upper not in COMPANY_MAP:
        return jsonify({"error": "알 수 없는 업체입니다."}), 404
    folder = _trend_folder(company_upper)
    if not os.path.isdir(folder):
        files = []
    else:
        files = sorted([
            f for f in os.listdir(folder)
            if f.lower().endswith(".html") or f.lower().endswith(".pdf")
        ], reverse=True)  # ← 최신 파일이 맨 위로
    return jsonify({
        "company":    company_upper,
        "company_kr": COMPANY_MAP[company_upper],
        "files":      files,
    })

@trend_bp.route("/api/trend/<company>/<path:filename>", methods=["GET"])
@login_required
def get_trend_content(company, filename):
    company_upper = company.upper()
    if company_upper not in COMPANY_MAP:
        return jsonify({"error": "알 수 없는 업체입니다."}), 404
    filepath = os.path.join(_trend_folder(company_upper), filename)
    if not os.path.isfile(filepath):
        return jsonify({"error": "파일을 찾을 수 없습니다."}), 404
    is_pdf = filename.lower().endswith(".pdf")
    if is_pdf:
        with open(filepath, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")
    else:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    return jsonify({
        "filename": filename,
        "content":  content,
        "type":     "pdf" if is_pdf else "html",
    })

@trend_bp.route("/api/trend/raw/<company>/<path:filename>", methods=["GET"])
@login_required
def get_trend_raw(company, filename):
    company_upper = company.upper()
    filepath = os.path.join(_trend_folder(company_upper), filename)
    if not os.path.isfile(filepath):
        return jsonify({"error": "파일을 찾을 수 없습니다."}), 404
    return send_file(filepath, mimetype="application/pdf")