import os
import uuid
import json
import re
import time
import threading
from flask import Flask, request, jsonify, render_template, send_from_directory
from concurrent.futures import ThreadPoolExecutor
import pypdfium2 as pdfium
from PIL import Image
from glmocr import GlmOcr
from utils.logger import setup_logging, get_logger

# Project Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLM_CONFIG = os.path.join(BASE_DIR, 'glm_config.yaml')

# Initialize logging immediately
setup_logging()
logger = get_logger("app")

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'output')
SESSIONS_FOLDER = os.path.join(BASE_DIR, 'sessions')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['SESSIONS_FOLDER'] = SESSIONS_FOLDER
app.json.sort_keys = False
app.json.ensure_ascii = False

for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, SESSIONS_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Job Status Storage (In-Memory for now, persisted to JSON)
jobs = {}
jobs_lock = threading.Lock()  # Guards all mutations of the shared jobs dict

# Initialize thread pool for serial job execution
job_executor = ThreadPoolExecutor(max_workers=1)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_job_state(job_id):
    """Persist a job's current state to disk."""
    state_path = os.path.join(app.config['SESSIONS_FOLDER'], f'{job_id}.json')
    with open(state_path, 'w') as f:
        json.dump(jobs[job_id], f)

def load_job_state(job_id):
    state_path = os.path.join(app.config['SESSIONS_FOLDER'], f'{job_id}.json')
    if os.path.exists(state_path):
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
            logger.warning(f"Corrupted session file detected and removed: {state_path}")
            try:
                os.remove(state_path)
            except Exception as e:
                logger.warning(f"Could not remove corrupted session file {state_path}: {e}")
            return None
    return None

@app.route('/api/image/<job_id>/<path:filename>')
def get_image(job_id, filename):
    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        return "Job not found", 404

    output_dir = job['output_dir']

    return send_from_directory(output_dir, filename)

def cleanup_old_uploads():
    """Removes uploads older than 24 hours."""
    now = time.time()
    cutoff = now - (24 * 3600)
    for folder in [UPLOAD_FOLDER]:
        if not os.path.exists(folder):
            continue
        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            if os.path.getmtime(path) < cutoff:
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except Exception as e:
                    logger.warning(f"Could not remove old upload {path}: {e}")

_last_cleanup = 0.0

@app.before_request
def before_request():
    global _last_cleanup
    # Throttle to at most once per hour to avoid thread pile-up on page reload
    if request.path == '/' and time.time() - _last_cleanup > 3600:
        _last_cleanup = time.time()
        threading.Thread(target=cleanup_old_uploads, daemon=True).start()

def ocr_worker(job_id, file_path):
    filename = jobs[job_id]['filename']
    jobs[job_id]['status'] = 'processing'
    logger.info(f"Starting OCR job for {filename} (ID: {job_id})")
    save_job_state(job_id)

    try:
        # Create output directory for this job
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        folder_name = f"{base_name}_{timestamp}"
        job_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], folder_name)
        os.makedirs(job_output_dir, exist_ok=True)

        jobs[job_id]['output_dir'] = job_output_dir
        # No save here — 'splitting' or the first page loop below will save

        # Prepare list of pages
        page_images = []
        if file_path.lower().endswith('.pdf'):
            logger.info(f"[{job_id}] Splitting PDF into images...")
            jobs[job_id]['status'] = 'splitting'
            save_job_state(job_id)
            
            pdf = pdfium.PdfDocument(file_path)
            num_pages = len(pdf)
            for i in range(num_pages):
                page = pdf[i]
                # Render at 200 DPI
                bitmap = page.render(scale=200/72)
                pil_image = bitmap.to_pil()
                img_path = os.path.join(job_output_dir, f"page_{i}.jpg")
                pil_image.save(img_path)
                page_images.append(img_path)
            pdf.close()
        else:
            num_pages = 1
            # Re-encode image as JPEG for consistent format regardless of input type
            img_path = os.path.join(job_output_dir, "page_0.jpg")
            with Image.open(file_path) as img:
                img.convert("RGB").save(img_path, "JPEG")
            page_images = [img_path]

        # Prepare page data subfolder
        pages_data_dir = os.path.join(job_output_dir, "pages_data")
        os.makedirs(pages_data_dir, exist_ok=True)

        # Use a single GlmOcr session for the entire job
        with GlmOcr(config_path=GLM_CONFIG) as model:
            for i, img_path in enumerate(page_images):
                # Update progress and save once per page
                logger.info(f"[{job_id}] Processing page {i+1}/{num_pages}...")

                # Run GLM-OCR on this page using the persistent model
                result = model.parse(img_path)
                result.save(output_dir=job_output_dir)

                img_stem = os.path.splitext(os.path.basename(img_path))[0]
                inner_dir = os.path.join(job_output_dir, img_stem)

                md_file = os.path.join(inner_dir, f"{img_stem}.md")
                vis_dir = os.path.join(inner_dir, 'layout_vis')

                content = ""
                if os.path.exists(md_file):
                    with open(md_file, 'r', encoding='utf-8') as f:
                        content = f.read()

                vis_image_url = None
                orig_image_url = f"/api/image/{job_id}/page_{i}.jpg"

                if os.path.exists(vis_dir):
                    vis_files = sorted([f for f in os.listdir(vis_dir) if f.endswith(('.jpg', '.png'))])
                    if vis_files:
                        vis_image_url = f"/api/image/{job_id}/{img_stem}/layout_vis/{vis_files[0]}"

                page_data = {
                    'page_num': i + 1,
                    'content': content,
                    'image': orig_image_url,
                    'layout_image': vis_image_url or orig_image_url
                }

                # Save individual page data to disk
                with open(os.path.join(pages_data_dir, f"page_{i}.json"), 'w') as pf:
                    json.dump(page_data, pf)

                # Update job state once per page, after all page work is done
                jobs[job_id]['status'] = 'processing'
                jobs[job_id]['current_page'] = i + 1
                jobs[job_id]['total_pages'] = num_pages
                jobs[job_id]['progress'] = int(((i + 1) / num_pages) * 100)
                jobs[job_id]['total_pages_finished'] = i + 1
                save_job_state(job_id)

        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['progress'] = 100
        logger.info(f"[{job_id}] Job completed successfully.")
        save_job_state(job_id)
        
        # Cleanup source upload once processed
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning(f"[{job_id}] Could not remove upload file {file_path}: {e}")

    except Exception as e:
        logger.exception(f"[{job_id}] critical error during OCR processing")
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] = str(e)
        save_job_state(job_id)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
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

        with jobs_lock:
            jobs[job_id] = {
                'job_id': job_id,
                'filename': filename,
                'status': 'queued',
                'progress': 0,
                'total_pages_finished': 0,
                'last_page_index': 0,
                'start_time': time.time()
            }
        save_job_state(job_id)

        # Queue the job for serial execution
        job_executor.submit(ocr_worker, job_id, file_path)

        return jsonify({'job_id': job_id})
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/api/status/<job_id>')
def get_status(job_id):
    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)

@app.route('/api/jobs')
def list_jobs():
    available_jobs = []
    session_files = [f for f in os.listdir(app.config['SESSIONS_FOLDER']) if f.endswith('.json')]
    
    for f in session_files:
        job_id = f.replace('.json', '')
        job_state = load_job_state(job_id)
        if job_state:
            # Check if output directory still exists
            output_dir = job_state.get('output_dir')
            if output_dir and os.path.exists(output_dir):
                available_jobs.append({
                    'job_id': job_id,
                    'filename': job_state.get('filename'),
                    'status': job_state.get('status'),
                    'progress': job_state.get('progress'),
                    'start_time': job_state.get('start_time'),
                    'page_count': job_state.get('total_pages_finished', 0)
                })
            else:
                # Cleanup stale session file if output is gone
                try:
                    os.remove(os.path.join(app.config['SESSIONS_FOLDER'], f))
                except Exception as e:
                    logger.warning(f"Could not remove stale session file {f}: {e}")
    
    # Sort by start_time descending
    available_jobs.sort(key=lambda x: x.get('start_time', 0), reverse=True)
    return jsonify(available_jobs)
    
@app.route('/api/last_page/<job_id>', methods=['POST'])
def update_last_page(job_id):
    if job_id not in jobs:
        job = load_job_state(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        with jobs_lock:
            jobs[job_id] = job

    data = request.json
    page_idx = data.get('page_index', 0)
    jobs[job_id]['last_page_index'] = page_idx
    save_job_state(job_id)
    return jsonify({'status': 'success'})

@app.route('/api/page/<job_id>/<int:page_idx>')
def get_page_content(job_id, page_idx):
    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    output_dir = job.get('output_dir')
    if not output_dir:
        return jsonify({'error': 'Output not ready'}), 404
    
    page_file = os.path.join(output_dir, "pages_data", f"page_{page_idx}.json")
    if os.path.exists(page_file):
        with open(page_file, 'r', encoding='utf-8') as f:
            return app.response_class(f.read(), mimetype='application/json')
            
    return jsonify({'error': 'Page not found'}), 404

@app.route('/api/export/<job_id>')
def export_job(job_id):
    fmt = request.args.get('format', 'markdown')
    scope = request.args.get('scope', 'all') # 'all' or 'current'
    page_idx = request.args.get('page_idx', type=int)

    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    output_dir = job.get('output_dir')
    if not output_dir:
        return jsonify({'error': 'Output not ready'}), 404

    pages_data_dir = os.path.join(output_dir, "pages_data")

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
            # Use the raw SDK output markdown file
            page_folder = os.path.join(output_dir, f"page_{idx}")
            md_file = os.path.join(page_folder, f"page_{idx}.md")
            
            if os.path.exists(md_file):
                with open(md_file, 'r', encoding='utf-8') as f:
                    content.append(f"## Page {idx + 1}\n\n" + f.read())
            else:
                # Fallback to UI data if raw markdown is missing
                page_file = os.path.join(pages_data_dir, f"page_{idx}.json")
                if os.path.exists(page_file):
                    with open(page_file, 'r', encoding='utf-8') as f:
                        page_data = json.load(f)
                        content.append(f"## Page {idx + 1}\n\n" + page_data.get('content', ''))
        
        full_text = "\n\n---\n\n".join(content)
        return full_text, 200, {
            'Content-Type': 'text/markdown',
            'Content-Disposition': f'attachment; filename="{base_filename}.md"'
        }
    
    elif fmt == 'json':
        results = []
        for idx in indices:
            # Use the raw SDK output json file
            page_folder = os.path.join(output_dir, f"page_{idx}")
            json_file = os.path.join(page_folder, f"page_{idx}.json")
            
            if os.path.exists(json_file):
                with open(json_file, 'r', encoding='utf-8') as f:
                    results.append(json.load(f))
            else:
                # Fallback to UI data
                page_file = os.path.join(pages_data_dir, f"page_{idx}.json")
                if os.path.exists(page_file):
                    with open(page_file, 'r', encoding='utf-8') as f:
                        results.append(json.load(f))
        
        result_data = results[0] if len(results) == 1 else results
        return jsonify(result_data), 200, {
            'Content-Type': 'application/json',
            'Content-Disposition': f'attachment; filename="{base_filename}.json"'
        }
    
    return jsonify({'error': 'Invalid format'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=False)
