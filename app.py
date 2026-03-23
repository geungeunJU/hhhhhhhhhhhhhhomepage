from flask import Flask, render_template, redirect, url_for, session, request
from routes.auth     import auth_bp
from routes.common   import common_bp
from routes.trend    import trend_bp
from routes.aircon   import aircon_bp
from routes.patents  import patents_bp
from routes.compare  import compare_bp
from routes.data_viz import data_viz_bp
from routes.news     import news_bp
from routes.autojournal import autojournal_bp
from routes.kdi      import kdi_bp
from routes.meeting import meeting_bp


app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

app.register_blueprint(auth_bp)
app.register_blueprint(common_bp)
app.register_blueprint(trend_bp)
app.register_blueprint(aircon_bp)
app.register_blueprint(patents_bp)
app.register_blueprint(compare_bp)
app.register_blueprint(data_viz_bp)
app.register_blueprint(news_bp)
app.register_blueprint(autojournal_bp)
app.register_blueprint(kdi_bp)
app.register_blueprint(meeting_bp)

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('auth.login'))
    return render_template(
        'main.html',
        username=session.get('username', ''),
        role=session.get('role', 'user'),
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))

if __name__ == '__main__':
    app.run(host='128.1.250.191', port=8000, debug=True)