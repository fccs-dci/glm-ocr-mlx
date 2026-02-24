import os
import uuid
import json
import re
import time
from urllib.parse import quote
from flask import Flask, request, jsonify, render_template, send_from_directory
from concurrent.futures import ThreadPoolExecutor
import pypdfium2 as pdfium
from PIL import Image
from glmocr import GlmOcr
from utils.logger import setup_logging, get_logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLM_CONFIG = os.path.join(BASE_DIR, 'glm_config.yaml')

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
job_executor = ThreadPoolExecutor(max_workers=1)

def allowed_file(filename):
    """Return True if 'filename' has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_job_state(job_id):
    """Write the current in-memory state of a job to disk as JSON.

    This is called after every meaningful state change (status, progress, etc.)
    so that if the app restarts, we can restore the job's state from disk.
    """
    state_path = os.path.join(app.config['SESSIONS_FOLDER'], f'{job_id}.json')
    with open(state_path, 'w') as f:
        json.dump(jobs[job_id], f)


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
    """Serve an image file that belongs to a specific job.

    The <path:filename> converter allows slashes in the filename segment,
    which we need because images are nested in subdirectories
    (e.g. page_0/layout_vis/vis.jpg).
    """
    # Try in-memory first; fall back to disk if the app was restarted.
    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        return "Job not found", 404

    output_dir = job['output_dir']
    return send_from_directory(output_dir, filename)


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Accept an uploaded image or PDF, create a job, and queue it for OCR.

    Returns immediately with a job_id. The actual OCR happens in the
    background via job_executor (see ThreadPoolExecutor above).
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        filename = re.sub(r'[\\/:*?"<>|]', '_', file.filename)
        job_id = str(uuid.uuid4())
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
        file.save(file_path)

        jobs[job_id] = {
            'job_id':               job_id,
            'filename':             filename,
            'status':               'queued',   # queued → splitting → processing → completed/failed
            'progress':             0,          # 0–100 percentage
            'total_pages_finished': 0,
            'last_page_index':      0,          # Which page the UI was last viewing
            'start_time':           time.time()
        }
        save_job_state(job_id)

        job_executor.submit(ocr_worker, job_id, file_path)

        return jsonify({'job_id': job_id})

    return jsonify({'error': 'Invalid file type'}), 400


@app.route('/api/status/<job_id>')
def get_status(job_id):
    """Return the current state of a job (status, progress, page count, etc.)."""
    # Try RAM first for speed, fall back to disk for older/reloaded jobs.
    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/jobs')
def list_jobs():
    """Return a summary list of all known jobs, sorted newest first.

    We read from disk (sessions/) rather than the in-memory dict because
    in-memory state is lost on restart, but session files survive.
    """
    available_jobs = []
    session_files = [f for f in os.listdir(app.config['SESSIONS_FOLDER']) if f.endswith('.json')]

    for f in session_files:
        job_id = f.replace('.json', '')
        job_state = load_job_state(job_id)
        if job_state:
            output_dir = job_state.get('output_dir')
            if output_dir and os.path.exists(output_dir):
                # Output directory still on disk — include this job in the list.
                available_jobs.append({
                    'job_id':     job_id,
                    'filename':   job_state.get('filename'),
                    'status':     job_state.get('status'),
                    'progress':   job_state.get('progress'),
                    'start_time': job_state.get('start_time'),
                    'page_count': job_state.get('total_pages_finished', 0)
                })
            else:
                # Output directory was deleted (e.g. user cleaned up manually).
                # Remove the dangling session file.
                try:
                    os.remove(os.path.join(app.config['SESSIONS_FOLDER'], f))
                except Exception as e:
                    logger.warning(f"Could not remove stale session file {f}: {e}")

    # Sort newest first so the UI shows the most recent job at the top.
    available_jobs.sort(key=lambda x: x.get('start_time', 0), reverse=True)
    return jsonify(available_jobs)


@app.route('/api/last_page/<job_id>', methods=['POST'])
def update_last_page(job_id):
    """Remember which page the user was last viewing for a given job.

    The browser calls this when the user flips to a different page, so if
    they close and reopen the app, it can restore their scroll position.
    """
    if job_id not in jobs:
        # Job might not be in RAM if the app was restarted — reload from disk.
        job = load_job_state(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        jobs[job_id] = job  # Bring back into memory

    data = request.json
    page_idx = data.get('page_index', 0)
    jobs[job_id]['last_page_index'] = page_idx
    save_job_state(job_id)
    return jsonify({'status': 'success'})


@app.route('/api/page/<job_id>/<int:page_idx>')
def get_page_content(job_id, page_idx):
    """Return the OCR result for a single page as JSON.

    Each page is stored as its own JSON file under output/<job>/pages_data/,
    written by the worker as soon as that page finishes. This lets the
    browser display results page-by-page without waiting for the whole job.
    """
    job = jobs.get(job_id) or load_job_state(job_id)
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
    """Export a job's OCR results as a downloadable file.

    Query params:
      format  — 'markdown' (default) or 'json'
      scope   — 'all' (default) or 'current' (single page)
      page_idx — required when scope='current'
    """
    fmt      = request.args.get('format', 'markdown')
    scope    = request.args.get('scope', 'all')
    page_idx = request.args.get('page_idx', type=int)

    job = jobs.get(job_id) or load_job_state(job_id)
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

        # For a single-page export return the object directly; for multi-page
        # return a list — this gives cleaner JSON in both cases.
        result_data = results[0] if len(results) == 1 else results
        return jsonify(result_data), 200, {
            'Content-Type': 'application/json',
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(base_filename + '.json')}"
        }

    return jsonify({'error': 'Invalid format'}), 400

def ocr_worker(job_id, file_path):
    """Process a single upload: convert to images, run OCR, save results.

    This function is called by the ThreadPoolExecutor in a BACKGROUND THREAD,
    not in the main Flask thread. That's why it can take as long as it needs
    without blocking any HTTP responses.

    It communicates progress back to the Flask routes by writing directly to
    the shared 'jobs' dict and calling save_job_state() after each page.
    """
    filename = jobs[job_id]['filename']
    jobs[job_id]['status'] = 'processing'
    logger.info(f"Starting OCR job for {filename} (ID: {job_id})")
    save_job_state(job_id)

    try:
        timestamp    = time.strftime('%Y%m%d_%H%M%S')
        base_name    = os.path.splitext(os.path.basename(file_path))[0]
        folder_name  = f"{base_name}_{timestamp}"
        job_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], folder_name)
        os.makedirs(job_output_dir, exist_ok=True)

        jobs[job_id]['output_dir'] = job_output_dir

        page_images = []
        if file_path.lower().endswith('.pdf'):
            # PDF — rasterise every page to JPEG at 200 DPI.
            # 200/72 is the scale factor because pypdfium works in 72 DPI units.
            logger.info(f"[{job_id}] Splitting PDF into images...")
            jobs[job_id]['status'] = 'splitting'
            save_job_state(job_id)

            pdf = pdfium.PdfDocument(file_path)
            num_pages = len(pdf)
            for i in range(num_pages):
                page   = pdf[i]
                bitmap = page.render(scale=200/72)
                pil_image = bitmap.to_pil()
                img_path = os.path.join(job_output_dir, f"page_{i}.jpg")
                pil_image.save(img_path)
                page_images.append(img_path)
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
                logger.info(f"[{job_id}] Processing page {i+1}/{num_pages}...")
                result = model.parse(img_path)

                result.save(output_dir=job_output_dir)

                img_stem  = os.path.splitext(os.path.basename(img_path))[0]
                page_output_dir = os.path.join(job_output_dir, img_stem)

                md_file = os.path.join(page_output_dir, f"{img_stem}.md")
                vis_dir = os.path.join(page_output_dir, 'layout_vis')

                content = ""
                if os.path.exists(md_file):
                    with open(md_file, 'r', encoding='utf-8') as f:
                        content = f.read()

                orig_image_url = f"/api/image/{job_id}/page_{i}.jpg"
                vis_image_url  = None

                if os.path.exists(vis_dir):
                    vis_files = sorted([f for f in os.listdir(vis_dir) if f.endswith(('.jpg', '.png'))])
                    if vis_files:
                        vis_image_url = f"/api/image/{job_id}/{img_stem}/layout_vis/{vis_files[0]}"

                page_data = {
                    'page_num':     i + 1,
                    'content':      content,
                    'image':        orig_image_url,
                    'layout_image': vis_image_url or orig_image_url  # fall back to original
                }

                with open(os.path.join(pages_data_dir, f"page_{i}.json"), 'w') as pf:
                    json.dump(page_data, pf)

                jobs[job_id]['status']               = 'processing'
                jobs[job_id]['total_pages']          = num_pages
                jobs[job_id]['progress']             = int(((i + 1) / num_pages) * 100)
                jobs[job_id]['total_pages_finished'] = i + 1
                save_job_state(job_id)

        jobs[job_id]['status']   = 'completed'
        jobs[job_id]['progress'] = 100
        logger.info(f"[{job_id}] Job completed successfully.")
        save_job_state(job_id)

        # The uploaded file is no longer needed, delete it to save space.
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning(f"[{job_id}] Could not remove upload file {file_path}: {e}")

    except Exception as e:
        logger.exception(f"[{job_id}] critical error during OCR processing")
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error']  = str(e)
        save_job_state(job_id)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=False)
