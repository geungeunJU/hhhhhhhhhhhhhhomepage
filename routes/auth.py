import json
import os
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for, render_template, jsonify

auth_bp = Blueprint('auth', __name__)

USERS_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'users.json')


def load_users():
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def login_required(f):
    """다른 routes/*.py에서 from routes.auth import login_required 로 사용"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


@auth_bp.route('/', methods=['GET'])
def index():
    if 'username' in session:
        return redirect(url_for('common.main'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        users = load_users()

        if username in users and users[username].get('password') == password:
            session['username'] = username
            session['role'] = users[username].get('role', 'user')
            session.permanent = True
            return redirect(url_for('common.main'))
        else:
            error = '아이디와 패스워드를 확인해 주세요'  # 법률 확정 문구

    return render_template('login.html', error=error)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))