import os
import json
import pandas as pd
from flask import Blueprint, jsonify, request, current_app
from routes.auth import login_required

data_viz_bp = Blueprint('data_viz', __name__)

DATA_DIR_NAME = 'data'


def get_data_dir():
    return os.path.join(current_app.static_folder, DATA_DIR_NAME)


def load_dataframe(filename: str) -> pd.DataFrame:
    data_dir = get_data_dir()
    filepath = os.path.join(data_dir, filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext in ('.xlsx', '.xls'):
        return pd.read_excel(filepath)
    elif ext == '.csv':
        # UTF-8 먼저 시도, 실패시 cp949
        try:
            return pd.read_csv(filepath, encoding='utf-8')
        except UnicodeDecodeError:
            return pd.read_csv(filepath, encoding='cp949')
    raise ValueError(f'지원하지 않는 파일 형식: {ext}')


@data_viz_bp.route('/api/data/files')
@login_required
def data_files():
    """static/data/ 파일 목록"""
    data_dir = get_data_dir()
    if not os.path.exists(data_dir):
        return jsonify([])

    files = []
    for filename in os.listdir(data_dir):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ('.xlsx', '.xls', '.csv'):
            continue
        filepath = os.path.join(data_dir, filename)
        stat = os.stat(filepath)
        files.append({
            'filename': filename,
            'size': stat.st_size,
            'modified': stat.st_mtime,
        })
    files.sort(key=lambda x: x['filename'])
    return jsonify(files)


@data_viz_bp.route('/api/data/preview', methods=['POST'])
@login_required
def data_preview():
    """파일 미리보기 (첫 100행)"""
    data = request.get_json()
    filename = data.get('filename', '')
    if not filename:
        return jsonify({'error': '파일명이 없습니다.'}), 400

    try:
        df = load_dataframe(filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    preview_rows = df.head(100)
    # NaN을 None으로 변환
    preview_rows = preview_rows.where(pd.notnull(preview_rows), None)

    return jsonify({
        'columns': list(df.columns),
        'rows': preview_rows.values.tolist(),
        'dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()},
        'shape': list(df.shape),
        'total_rows': len(df),
    })


@data_viz_bp.route('/api/data/analyze', methods=['POST'])
@login_required
def data_analyze():
    """AI 자동 그래프 추천"""
    data = request.get_json()
    filename = data.get('filename', '')
    if not filename:
        return jsonify({'error': '파일명이 없습니다.'}), 400

    try:
        df = load_dataframe(filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    recommendations = []
    numeric_cols = df.select_dtypes(include='number').columns.tolist()
    categorical_cols = df.select_dtypes(exclude='number').columns.tolist()

    # 1. 수치형 컬럼이 2개 이상이면 라인/산점도 추천
    if len(numeric_cols) >= 2:
        recommendations.append({
            'type': 'line',
            'title': f'{numeric_cols[0]} vs {numeric_cols[1]} 추세',
            'x': numeric_cols[0],
            'y': numeric_cols[1],
            'reason': '두 수치 컬럼 간 추세를 확인하기 좋습니다.',
        })
        if len(numeric_cols) >= 3:
            recommendations.append({
                'type': 'scatter',
                'title': f'{numeric_cols[1]} vs {numeric_cols[2]} 분포',
                'x': numeric_cols[1],
                'y': numeric_cols[2],
                'reason': '두 변수 간 상관관계를 파악하기 좋습니다.',
            })

    # 2. 카테고리 + 수치형 → 막대 추천
    if categorical_cols and numeric_cols:
        cat = categorical_cols[0]
        num = numeric_cols[0]
        if df[cat].nunique() <= 30:
            recommendations.append({
                'type': 'bar',
                'title': f'{cat}별 {num} 비교',
                'x': cat,
                'y': num,
                'reason': '범주별 수치를 한눈에 비교하기 좋습니다.',
            })

    # 3. 카테고리 컬럼이 있으면 파이차트 추천
    if categorical_cols:
        cat = categorical_cols[0]
        if 2 <= df[cat].nunique() <= 10:
            num_col = numeric_cols[0] if numeric_cols else None
            recommendations.append({
                'type': 'pie',
                'title': f'{cat} 구성 비율',
                'x': cat,
                'y': num_col,
                'reason': '구성 비율을 시각적으로 보기 좋습니다.',
            })

    if not recommendations and numeric_cols:
        recommendations.append({
            'type': 'bar',
            'title': f'{numeric_cols[0]} 분포',
            'x': None,
            'y': numeric_cols[0],
            'reason': '데이터 분포를 확인합니다.',
        })

    return jsonify({'recommendations': recommendations})