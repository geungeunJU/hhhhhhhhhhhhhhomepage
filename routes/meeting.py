from flask import Blueprint, render_template, request, jsonify
import os, threading, uuid, time

meeting_bp = Blueprint('meeting', __name__)

MEETING_UPLOAD_DIR = r'C:\cache\ido_portal\meeting'

# 변환 작업 상태 저장 (job_id -> 상태)
_transcribe_jobs = {}

# Whisper 모델 (처음 요청 시 로드, 이후 재사용)
_whisper_model = None
_model_lock = threading.Lock()

def _get_model():
    """Whisper 모델 로드 (최초 1회만, 이후 재사용)"""
    global _whisper_model
    if _whisper_model is None:
        with _model_lock:
            if _whisper_model is None:
                import whisper
                _whisper_model = whisper.load_model('small')
    return _whisper_model


# ── 페이지 렌더링 ────────────────────────────────
@meeting_bp.route('/meeting')
def meeting():
    return render_template('sections/meeting.html')


# ── API: 녹음파일 업로드 + STT 변환 시작 ────────
@meeting_bp.route('/api/meeting/transcribe', methods=['POST'])
def transcribe():
    """
    1. 녹음파일 서버에 저장
    2. 백그라운드에서 Whisper 변환 시작
    3. job_id 즉시 반환 (프론트에서 폴링)
    """
    file = request.files.get('audio')
    if not file:
        return jsonify({'error': '파일 없음'}), 400

    os.makedirs(MEETING_UPLOAD_DIR, exist_ok=True)
    job_id   = str(uuid.uuid4())
    ext      = os.path.splitext(file.filename)[1] or '.audio'
    save_path = os.path.join(MEETING_UPLOAD_DIR, job_id + ext)
    file.save(save_path)

    # 작업 등록
    _transcribe_jobs[job_id] = {
        'status': 'running',   # running / done / error
        'text': '',
        'started': time.time(),
    }

    # 백그라운드 스레드에서 Whisper 실행
    def _run(path, jid):
        try:
            model  = _get_model()
            result = model.transcribe(path, language='ko')
            _transcribe_jobs[jid]['text']   = result['text']
            _transcribe_jobs[jid]['status'] = 'done'
        except Exception as e:
            _transcribe_jobs[jid]['status'] = 'error'
            _transcribe_jobs[jid]['text']   = str(e)

    threading.Thread(target=_run, args=(save_path, job_id), daemon=True).start()

    return jsonify({'job_id': job_id})


# ── API: 변환 상태 폴링 ──────────────────────────
@meeting_bp.route('/api/meeting/status/<job_id>')
def transcribe_status(job_id):
    """
    프론트에서 2초마다 호출해서 완료 여부 확인
    반환: { status: 'running'|'done'|'error', text: '...' }
    """
    job = _transcribe_jobs.get(job_id)
    if not job:
        return jsonify({'error': '없는 작업'}), 404
    return jsonify({
        'status': job['status'],
        'text':   job['text'],
        'elapsed': round(time.time() - job['started'], 1),
    })


# ── API: 저장된 회의록 목록 ──────────────────────
@meeting_bp.route('/api/meeting/list')
def list_meetings():
    return jsonify({'meetings': []})