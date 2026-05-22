import os
import uuid
import json
import re
import time
import threading
import tempfile
from urllib.parse import quote
from flask import Flask, request, jsonify, render_template, send_from_directory
from concurrent.futures import ThreadPoolExecutor
import pypdfium2 as pdfium
from PIL import Image
from glmocr import GlmOcr
from utils.logger import setup_logging, get_logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLM_CONFIG = os.environ.get('GLM_CONFIG')

setup_logging()
logger = get_logger("app")

app = Flask(__name__)

UPLOAD_FOLDER  = os.path.join(BASE_DIR, 'static', 'uploads')  # Temporary home for uploaded files
OUTPUT_FOLDER  = os.path.join(BASE_DIR, 'output')             # Permanent OCR results per job
SESSIONS_FOLDER = os.path.join(BASE_DIR, 'sessions')          # One JSON file per job (persisted state)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

# Store folder paths inside app.config so any part of the code can read them
# via app.config['KEY'] without relying on a global variable.
app.config['UPLOAD_FOLDER']   = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER']   = OUTPUT_FOLDER
app.config['SESSIONS_FOLDER'] = SESSIONS_FOLDER

# Keep JSON key order as we set it (easier to read in responses / logs).
app.json.sort_keys = False
# Allow non-ASCII characters (e.g. Chinese text from OCR) in JSON responses.
app.json.ensure_ascii = False

# Create the directories on startup if they don't already exist.
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, SESSIONS_FOLDER]:
    os.makedirs(folder, exist_ok=True)

jobs = {}
_jobs_lock = threading.Lock()
_cancel_events = {}
_job_futures = {}
job_executor = ThreadPoolExecutor(max_workers=1)

_SAFE_JOB_ID = re.compile(r'^[a-zA-Z0-9_-]+$')


def _valid_job_id(job_id):
    return bool(_SAFE_JOB_ID.match(job_id))


def _update_job(job_id, **updates):
    with _jobs_lock:
        jobs[job_id].update(updates)


def _get_job(job_id):
    with _jobs_lock:
        job = jobs.get(job_id)
        if job:
            return dict(job)
    return load_job_state(job_id)


def _submit_job(job_id, fn, *args):
    _cancel_events[job_id] = threading.Event()
    _job_futures[job_id] = job_executor.submit(fn, job_id, *args)


def _is_cancelled(job_id):
    evt = _cancel_events.get(job_id)
    return evt is not None and evt.is_set()

def allowed_file(filename):
    """Return True if 'filename' has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_job_state(job_id):
    """Write the current in-memory state of a job to disk as JSON.

    Takes a snapshot under the lock and writes atomically via temp file +
    os.replace so a crash mid-write never corrupts the session file.
    """
    with _jobs_lock:
        state = dict(jobs[job_id])
    sessions_dir = app.config['SESSIONS_FOLDER']
    os.makedirs(sessions_dir, exist_ok=True)
    state_path = os.path.join(sessions_dir, f'{job_id}.json')
    fd, tmp_path = tempfile.mkstemp(dir=sessions_dir, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f)
        os.replace(tmp_path, state_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_job_state(job_id):
    """Read a job's state from disk and return it, or None if it can't be loaded.

    This is the fallback used by routes when a job_id isn't in the in-memory
    'jobs' dict (e.g. after an app restart, or for older jobs the browser is
    revisiting).
    """
    state_path = os.path.join(app.config['SESSIONS_FOLDER'], f'{job_id}.json')
    if os.path.exists(state_path):
        # A zero-byte file means a previous write was interrupted — treat it
        # as missing and clean it up.
        if os.path.getsize(state_path) == 0:
            try:
                os.remove(state_path)
            except Exception as e:
                logger.warning(f"Could not remove empty session file {state_path}: {e}")
            return None
        try:
            with open(state_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            # File exists but isn't valid JSON — corrupted, remove it.
            logger.warning(f"Corrupted session file detected and removed: {state_path}")
            try:
                os.remove(state_path)
            except Exception as e:
                logger.warning(f"Could not remove corrupted session file {state_path}: {e}")
            return None
    return None


def cleanup_old_uploads():
    """Delete any uploaded files that are older than 24 hours.

    Uploads are normally deleted right after the OCR worker finishes
    (see the bottom of ocr_worker). This cleanup is a safety net for files
    that were never processed (e.g. the app crashed mid-job).
    """
    now = time.time()
    cutoff = now - (24 * 3600)  # 24 hours ago, expressed as a Unix timestamp
    if not os.path.exists(UPLOAD_FOLDER):
        return
    for f in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, f)
        # os.path.getmtime returns the file's last-modified time as a Unix timestamp.
        if os.path.getmtime(path) < cutoff:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                logger.warning(f"Could not remove old upload {path}: {e}")

_last_cleanup = 0.0

@app.before_request
def before_request():
    """Flask calls this function automatically before every incoming request.

    We use it to trigger the old-upload cleanup, but only:
      1. On the home page load (not on every API call)
      2. At most once per hour (throttled via _last_cleanup)
    """
    global _last_cleanup
    if request.path == '/' and time.time() - _last_cleanup > 3600:
        _last_cleanup = time.time()
        cleanup_old_uploads()

@app.route('/')
def index():
    """Serve the single-page frontend."""
    return render_template('index.html')


@app.route('/api/image/<job_id>/<path:filename>')
def get_image(job_id, filename):
    """Serve an image file that belongs to a specific job."""
    if not _valid_job_id(job_id):
        return "Invalid job ID", 400

    job = _get_job(job_id)
    if not job:
        return "Job not found", 404

    output_dir = job['output_dir']
    return send_from_directory(output_dir, filename)


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Accept one or more uploaded files, create jobs, and queue them for OCR.

    Requires a 'mode' field: 'pdf' or 'image'. All files must match the mode.
    Image mode accepts an optional 'bundle' field ('true'/'false') to control
    whether multiple images are grouped into a single job.
    """
    mode = request.form.get('mode')
    if mode not in ('pdf', 'image'):
        return jsonify({'error': 'Missing or invalid mode (must be "pdf" or "image")'}), 400

    files = request.files.getlist('files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No files provided'}), 400

    allowed_exts = {'pdf'} if mode == 'pdf' else {'png', 'jpg', 'jpeg'}
    for f in files:
        if not f.filename or '.' not in f.filename:
            return jsonify({'error': f'Invalid file: {f.filename}'}), 400
        ext = f.filename.rsplit('.', 1)[1].lower()
        if ext not in allowed_exts:
            return jsonify({'error': f'File "{f.filename}" does not match {mode} mode'}), 400

    bundle = request.form.get('bundle', 'true') == 'true'
    created_jobs = []
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    if mode == 'pdf':
        for f in files:
            filename = re.sub(r'[\\/:*?"<>|]', '_', f.filename)
            job_id = str(uuid.uuid4())
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
            f.save(file_path)

            jobs[job_id] = {
                'job_id': job_id, 'filename': filename, 'status': 'queued',
                'progress': 0, 'total_pages_finished': 0, 'last_page_index': 0,
                'start_time': time.time()
            }
            save_job_state(job_id)
            _submit_job(job_id, ocr_worker, file_path)
            created_jobs.append({'job_id': job_id, 'filename': filename})

    elif mode == 'image' and bundle and len(files) > 1:
        job_id = str(uuid.uuid4())
        saved_paths = []
        filenames = []
        for f in files:
            filename = re.sub(r'[\\/:*?"<>|]', '_', f.filename)
            filenames.append(filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
            f.save(file_path)
            saved_paths.append(file_path)

        display_name = f"{len(filenames)} images ({filenames[0]}, ...)"
        jobs[job_id] = {
            'job_id': job_id, 'filename': display_name, 'status': 'queued',
            'progress': 0, 'total_pages_finished': 0, 'last_page_index': 0,
            'start_time': time.time()
        }
        save_job_state(job_id)
        _submit_job(job_id, ocr_worker_batch_images, saved_paths)
        created_jobs.append({'job_id': job_id, 'filename': display_name})

    else:
        for f in files:
            filename = re.sub(r'[\\/:*?"<>|]', '_', f.filename)
            job_id = str(uuid.uuid4())
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
            f.save(file_path)

            jobs[job_id] = {
                'job_id': job_id, 'filename': filename, 'status': 'queued',
                'progress': 0, 'total_pages_finished': 0, 'last_page_index': 0,
                'start_time': time.time()
            }
            save_job_state(job_id)
            _submit_job(job_id, ocr_worker, file_path)
            created_jobs.append({'job_id': job_id, 'filename': filename})

    response = {'jobs': created_jobs}
    if len(created_jobs) == 1:
        response['job_id'] = created_jobs[0]['job_id']
    return jsonify(response)


@app.route('/api/status/<job_id>')
def get_status(job_id):
    """Return the current state of a job (status, progress, page count, etc.)."""
    if not _valid_job_id(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400

    job = _get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/jobs')
def list_jobs():
    """Return a summary list of all known jobs, sorted newest first.

    Merges on-disk session files with in-memory jobs so that queued and
    processing jobs (which may not have an output_dir yet) are included.
    """
    seen_ids = set()
    available_jobs = []

    def _job_summary(job_state):
        return {
            'job_id':     job_state.get('job_id'),
            'filename':   job_state.get('filename'),
            'status':     job_state.get('status'),
            'progress':   job_state.get('progress'),
            'start_time': job_state.get('start_time'),
            'page_count': job_state.get('total_pages_finished', 0)
        }

    with _jobs_lock:
        in_memory = list(jobs.items())

    for job_id, job_state in in_memory:
        seen_ids.add(job_id)
        available_jobs.append(_job_summary(job_state))

    sessions_dir = app.config['SESSIONS_FOLDER']
    os.makedirs(sessions_dir, exist_ok=True)
    session_files = [f for f in os.listdir(sessions_dir) if f.endswith('.json')]
    for f in session_files:
        job_id = f.replace('.json', '')
        if job_id in seen_ids:
            continue
        job_state = load_job_state(job_id)
        if job_state:
            output_dir = job_state.get('output_dir')
            status = job_state.get('status')
            if status in ('queued', 'splitting', 'processing', 'cancelling'):
                available_jobs.append(_job_summary(job_state))
            elif output_dir and os.path.exists(output_dir):
                available_jobs.append(_job_summary(job_state))
            else:
                try:
                    os.remove(os.path.join(app.config['SESSIONS_FOLDER'], f))
                except Exception as e:
                    logger.warning(f"Could not remove stale session file {f}: {e}")

    available_jobs.sort(key=lambda x: x.get('start_time', 0), reverse=True)
    return jsonify(available_jobs)


@app.route('/api/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    """Cancel a single job. Queued jobs are removed; running jobs stop after the current page."""
    if not _valid_job_id(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400

    job = _get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    if job.get('status') in ('completed', 'failed', 'cancelled', 'cancelling'):
        return jsonify({'status': job['status']})

    evt = _cancel_events.get(job_id)
    if evt:
        evt.set()

    future = _job_futures.get(job_id)
    if future:
        future.cancel()

    new_status = 'cancelling' if job.get('status') in ('processing', 'splitting') else 'cancelled'
    _update_job(job_id, status=new_status)
    save_job_state(job_id)
    return jsonify({'status': new_status})


@app.route('/api/cancel_all', methods=['POST'])
def cancel_all_jobs():
    """Cancel all queued and in-progress jobs."""
    cancelled = []
    with _jobs_lock:
        active_ids = [
            jid for jid, state in jobs.items()
            if state.get('status') in ('queued', 'splitting', 'processing')
        ]

    for job_id in active_ids:
        job = _get_job(job_id)
        if not job or job.get('status') in ('completed', 'failed', 'cancelled', 'cancelling'):
            continue

        evt = _cancel_events.get(job_id)
        if evt:
            evt.set()

        future = _job_futures.get(job_id)
        if future:
            future.cancel()

        new_status = 'cancelling' if job.get('status') in ('processing', 'splitting') else 'cancelled'
        _update_job(job_id, status=new_status)
        save_job_state(job_id)
        cancelled.append(job_id)

    return jsonify({'cancelled': cancelled})


@app.route('/api/last_page/<job_id>', methods=['POST'])
def update_last_page(job_id):
    """Remember which page the user was last viewing for a given job."""
    if not _valid_job_id(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400

    with _jobs_lock:
        if job_id not in jobs:
            job = load_job_state(job_id)
            if not job:
                return jsonify({'error': 'Job not found'}), 404
            jobs[job_id] = job

    data = request.json
    page_idx = data.get('page_index', 0)
    _update_job(job_id, last_page_index=page_idx)
    save_job_state(job_id)
    return jsonify({'status': 'success'})


@app.route('/api/page/<job_id>/<int:page_idx>')
def get_page_content(job_id, page_idx):
    """Return the OCR result for a single page as JSON."""
    if not _valid_job_id(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400

    job = _get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    output_dir = job.get('output_dir')
    if not output_dir:
        # Worker hasn't created the output directory yet (job is still queued).
        return jsonify({'error': 'Output not ready'}), 404

    page_file = os.path.join(output_dir, "pages_data", f"page_{page_idx}.json")
    if os.path.exists(page_file):
        # Read and return the raw JSON file directly (no parsing overhead).
        with open(page_file, 'r', encoding='utf-8') as f:
            return app.response_class(f.read(), mimetype='application/json')

    return jsonify({'error': 'Page not found'}), 404


@app.route('/api/export/<job_id>')
def export_job(job_id):
    """Export a job's OCR results as a downloadable file."""
    if not _valid_job_id(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400

    fmt      = request.args.get('format', 'markdown')
    scope    = request.args.get('scope', 'all')
    page_idx = request.args.get('page_idx', type=int)

    job = _get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    output_dir = job.get('output_dir')
    if not output_dir:
        return jsonify({'error': 'Output not ready'}), 404

    pages_data_dir = os.path.join(output_dir, "pages_data")

    # Decide which pages to include.
    if scope == 'current' and page_idx is not None:
        indices = [page_idx]
        base_filename = f"{job['filename']}_page_{page_idx + 1}_export"
    else:
        num_pages = job.get('total_pages_finished', 0)
        indices = list(range(num_pages))
        base_filename = f"{job['filename']}_export"

    if fmt == 'markdown':
        content = []
        for idx in indices:
            # The SDK writes one Markdown file per page under output/<job>/page_<idx>/
            page_folder = os.path.join(output_dir, f"page_{idx}")
            md_file     = os.path.join(page_folder, f"page_{idx}.md")

            if os.path.exists(md_file):
                with open(md_file, 'r', encoding='utf-8') as f:
                    content.append(f"## Page {idx + 1}\n\n" + f.read())
            else:
                # Fallback: use the content string we stored in pages_data/
                page_file = os.path.join(pages_data_dir, f"page_{idx}.json")
                if os.path.exists(page_file):
                    with open(page_file, 'r', encoding='utf-8') as f:
                        page_data = json.load(f)
                        content.append(f"## Page {idx + 1}\n\n" + page_data.get('content', ''))

        if not content:
            return jsonify({'error': 'No page data available'}), 404
        # Join pages with a horizontal rule separator.
        full_text = "\n\n---\n\n".join(content)
        return full_text, 200, {
            'Content-Type': 'text/markdown',
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(base_filename + '.md')}"
        }

    elif fmt == 'json':
        results = []
        for idx in indices:
            # SDK also writes a JSON file per page alongside the Markdown.
            page_folder = os.path.join(output_dir, f"page_{idx}")
            json_file   = os.path.join(page_folder, f"page_{idx}.json")

            if os.path.exists(json_file):
                with open(json_file, 'r', encoding='utf-8') as f:
                    results.append(json.load(f))
            else:
                # Fallback to the UI JSON we stored in pages_data/
                page_file = os.path.join(pages_data_dir, f"page_{idx}.json")
                if os.path.exists(page_file):
                    with open(page_file, 'r', encoding='utf-8') as f:
                        results.append(json.load(f))

        if not results:
            return jsonify({'error': 'No page data available'}), 404
        result_data = results[0] if len(results) == 1 else results
        return jsonify(result_data), 200, {
            'Content-Type': 'application/json',
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(base_filename + '.json')}"
        }

    return jsonify({'error': 'Invalid format'}), 400

def _process_page(job_id, model, img_path, page_index, num_pages, job_output_dir, pages_data_dir):
    """Run OCR on a single page image and save results. Updates job progress."""
    logger.info(f"[{job_id}] Processing page {page_index+1}/{num_pages}...")
    result = model.parse(img_path)
    result.save(output_dir=job_output_dir)

    img_stem = os.path.splitext(os.path.basename(img_path))[0]
    page_output_dir = os.path.join(job_output_dir, img_stem)

    md_file = os.path.join(page_output_dir, f"{img_stem}.md")
    vis_dir = os.path.join(page_output_dir, 'layout_vis')

    content = ""
    if os.path.exists(md_file):
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()

    orig_image_url = f"/api/image/{job_id}/page_{page_index}.jpg"
    vis_image_url  = None

    if os.path.exists(vis_dir):
        vis_files = sorted([f for f in os.listdir(vis_dir) if f.endswith(('.jpg', '.png'))])
        if vis_files:
            vis_image_url = f"/api/image/{job_id}/{img_stem}/layout_vis/{vis_files[0]}"

    page_data = {
        'page_num':     page_index + 1,
        'content':      content,
        'image':        orig_image_url,
        'layout_image': vis_image_url or orig_image_url
    }

    with open(os.path.join(pages_data_dir, f"page_{page_index}.json"), 'w') as pf:
        json.dump(page_data, pf)

    if not _is_cancelled(job_id):
        _update_job(job_id,
            status='processing',
            total_pages=num_pages,
            progress=int(((page_index + 1) / num_pages) * 100),
            total_pages_finished=page_index + 1,
        )
        save_job_state(job_id)


def ocr_worker(job_id, file_path):
    """Process a single upload: convert to images, run OCR, save results."""
    if _is_cancelled(job_id):
        _update_job(job_id, status='cancelled')
        save_job_state(job_id)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        return
    filename = jobs[job_id]['filename']
    _update_job(job_id, status='processing')
    logger.info(f"Starting OCR job for {filename} (ID: {job_id})")
    save_job_state(job_id)

    try:
        timestamp    = time.strftime('%Y%m%d_%H%M%S')
        base_name    = os.path.splitext(os.path.basename(file_path))[0]
        folder_name  = f"{base_name}_{timestamp}"
        job_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], folder_name)
        os.makedirs(job_output_dir, exist_ok=True)

        _update_job(job_id, output_dir=job_output_dir)

        page_images = []
        if file_path.lower().endswith('.pdf'):
            logger.info(f"[{job_id}] Splitting PDF into images...")
            _update_job(job_id, status='splitting')
            save_job_state(job_id)

            pdf = pdfium.PdfDocument(file_path)
            try:
                num_pages = len(pdf)
                for i in range(num_pages):
                    page   = pdf[i]
                    bitmap = page.render(scale=200/72)
                    pil_image = bitmap.to_pil()
                    img_path = os.path.join(job_output_dir, f"page_{i}.jpg")
                    pil_image.save(img_path)
                    page_images.append(img_path)
            finally:
                pdf.close()
        else:
            num_pages = 1
            img_path  = os.path.join(job_output_dir, "page_0.jpg")
            with Image.open(file_path) as img:
                img.convert("RGB").save(img_path, "JPEG")
            page_images = [img_path]

        pages_data_dir = os.path.join(job_output_dir, "pages_data")
        os.makedirs(pages_data_dir, exist_ok=True)

        with GlmOcr(config_path=GLM_CONFIG) as model:
            for i, img_path in enumerate(page_images):
                if _is_cancelled(job_id):
                    logger.info(f"[{job_id}] Cancelled after page {i}.")
                    _update_job(job_id, status='cancelled')
                    save_job_state(job_id)
                    return
                _process_page(job_id, model, img_path, i, num_pages, job_output_dir, pages_data_dir)

        if _is_cancelled(job_id):
            _update_job(job_id, status='cancelled')
            logger.info(f"[{job_id}] Cancelled after final page.")
        else:
            _update_job(job_id, status='completed', progress=100)
            logger.info(f"[{job_id}] Job completed successfully.")
        save_job_state(job_id)

    except Exception as e:
        if _is_cancelled(job_id):
            _update_job(job_id, status='cancelled')
        else:
            logger.exception(f"[{job_id}] critical error during OCR processing")
            _update_job(job_id, status='failed', error='OCR processing failed')
        save_job_state(job_id)
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning(f"[{job_id}] Could not remove upload file {file_path}: {e}")


def ocr_worker_batch_images(job_id, file_paths):
    """Process multiple images as a single batch job."""
    if _is_cancelled(job_id):
        _update_job(job_id, status='cancelled')
        save_job_state(job_id)
        for fp in file_paths:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass
        return
    display_name = jobs[job_id]['filename']
    _update_job(job_id, status='processing')
    logger.info(f"Starting batch image OCR job for {display_name} (ID: {job_id})")
    save_job_state(job_id)

    try:
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        folder_name = f"batch_{timestamp}"
        job_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], folder_name)
        os.makedirs(job_output_dir, exist_ok=True)

        _update_job(job_id, output_dir=job_output_dir)

        num_pages = len(file_paths)
        page_images = []
        for i, fp in enumerate(file_paths):
            img_path = os.path.join(job_output_dir, f"page_{i}.jpg")
            with Image.open(fp) as img:
                img.convert("RGB").save(img_path, "JPEG")
            page_images.append(img_path)

        pages_data_dir = os.path.join(job_output_dir, "pages_data")
        os.makedirs(pages_data_dir, exist_ok=True)

        with GlmOcr(config_path=GLM_CONFIG) as model:
            for i, img_path in enumerate(page_images):
                if _is_cancelled(job_id):
                    logger.info(f"[{job_id}] Cancelled after page {i}.")
                    _update_job(job_id, status='cancelled')
                    save_job_state(job_id)
                    return
                _process_page(job_id, model, img_path, i, num_pages, job_output_dir, pages_data_dir)

        if _is_cancelled(job_id):
            _update_job(job_id, status='cancelled')
            logger.info(f"[{job_id}] Cancelled after final page.")
        else:
            _update_job(job_id, status='completed', progress=100)
            logger.info(f"[{job_id}] Batch job completed successfully.")
        save_job_state(job_id)

    except Exception as e:
        if _is_cancelled(job_id):
            _update_job(job_id, status='cancelled')
        else:
            logger.exception(f"[{job_id}] critical error during batch OCR processing")
            _update_job(job_id, status='failed', error='OCR processing failed')
        save_job_state(job_id)
    finally:
        for fp in file_paths:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception as e:
                logger.warning(f"[{job_id}] Could not remove upload file {fp}: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=False)
