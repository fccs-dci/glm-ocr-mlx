import os
import uuid
import json
import re
import time
import threading
import shutil
from flask import Flask, request, jsonify, render_template, send_from_directory
import pypdfium2 as pdfium
from PIL import Image
from glmocr import GlmOcr, parse
from utils.logger import setup_logging, get_logger

# Project Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLM_CONFIG = os.path.join(BASE_DIR, 'glm_config.yaml')

# Initialize logging immediately
setup_logging()
logger = get_logger("app")

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'static/uploads'
OUTPUT_FOLDER = 'output'
SESSIONS_FOLDER = 'sessions'
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

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_job_state(job_id):
    state_path = os.path.join(app.config['SESSIONS_FOLDER'], f'{job_id}.json')
    with open(state_path, 'w') as f:
        json.dump(jobs[job_id], f)

def load_job_state(job_id):
    state_path = os.path.join(app.config['SESSIONS_FOLDER'], f'{job_id}.json')
    if os.path.exists(state_path):
        if os.path.getsize(state_path) == 0:
            try: os.remove(state_path)
            except: pass
            return None
        try:
            with open(state_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning(f"Corrupted session file detected and removed: {state_path}")
            try: os.remove(state_path)
            except: pass
            return None
    return None

@app.route('/api/image/<job_id>/<path:filename>')
def get_image(job_id, filename):
    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        return "Job not found", 404
        
    output_dir = job['output_dir']
    
    # Ensure we are serving from the correct job output dir
    return send_from_directory(output_dir, filename)

def cleanup_old_uploads():
    """Removes uploads older than 24 hours."""
    now = time.time()
    cutoff = now - (24 * 3600)
    for folder in [UPLOAD_FOLDER]:
        if not os.path.exists(folder): continue
        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            if os.path.getmtime(path) < cutoff:
                try:
                    if os.path.isfile(path): os.remove(path)
                except: pass

@app.before_request
def before_request():
    # Trigger accidental cleanup
    if request.path == '/':
        threading.Thread(target=cleanup_old_uploads).start()

def ocr_worker(job_id, file_path):
    job = jobs[job_id]
    logger.info(f"Starting OCR job for {job['filename']} (ID: {job_id})")
    job['status'] = 'processing'
    save_job_state(job_id)

    try:
        # Create output directory for this job
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        folder_name = f"{base_name}_{timestamp}"
        job_output_dir = os.path.join(os.path.abspath(app.config['OUTPUT_FOLDER']), folder_name)
        os.makedirs(job_output_dir, exist_ok=True)
        
        job['output_dir'] = job_output_dir
        save_job_state(job_id)

        # Prepare list of pages
        page_images = []
        if file_path.lower().endswith('.pdf'):
            logger.info(f"[{job_id}] Splitting PDF into images...")
            job['status'] = 'splitting'
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
            # Copy original image to output dir for consistent serving as page_0
            img_path = os.path.join(job_output_dir, "page_0.jpg")
            shutil.copy(file_path, img_path)
            page_images = [img_path]

        # Prepare page data subfolder
        pages_data_dir = os.path.join(job_output_dir, "pages_data")
        os.makedirs(pages_data_dir, exist_ok=True)

        pages_metadata = []
        
        # Use a single GlmOcr session for the entire job
        with GlmOcr(config_path=GLM_CONFIG) as model:
            for i, img_path in enumerate(page_images):
                # Update status for current page
                logger.info(f"[{job_id}] Processing page {i+1}/{num_pages}...")
                job['status'] = 'processing'
                job['current_page'] = i + 1
                job['total_pages'] = num_pages
                save_job_state(job_id)
                
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

                # Summary metadata (minimal for list/status)
                pages_metadata.append({'page_num': i + 1})

                # Incremental update of progress (without heavy content)
                job['progress'] = int(((i + 1) / num_pages) * 100)
                job['total_pages_finished'] = len(pages_metadata)
                save_job_state(job_id)

        job['status'] = 'completed'
        job['progress'] = 100
        logger.info(f"[{job_id}] Job completed successfully.")
        save_job_state(job_id)
        
        # Cleanup source upload once processed
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

    except Exception as e:
        logger.exception(f"[{job_id}] critical error during OCR processing")
        job['status'] = 'failed'
        job['error'] = str(e)
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
        original_filename = file.filename
        safe_base = re.sub(r'[\\/:*?"<>|]', '_', original_filename)
        
        filename = safe_base
        job_id = str(uuid.uuid4())
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
        file.save(file_path)

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

        # Start worker thread
        thread = threading.Thread(target=ocr_worker, args=(job_id, file_path))
        thread.start()

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
                except:
                    pass
    
    # Sort by start_time descending
    available_jobs.sort(key=lambda x: x.get('start_time', 0), reverse=True)
    return jsonify(available_jobs)
    
@app.route('/api/last_page/<job_id>', methods=['POST'])
def update_last_page(job_id):
    if job_id not in jobs:
        job = load_job_state(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
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
            return f.read() # Already JSON
            
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
    else:
        num_pages = job.get('total_pages_finished', 0)
        indices = list(range(num_pages))

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
        if scope == 'current' and page_idx is not None:
            filename = f"{job['filename']}_page_{page_idx + 1}_export.md"
        else:
            filename = f"{job['filename']}_export.md"

        return full_text, 200, {
            'Content-Type': 'text/markdown',
            'Content-Disposition': f'attachment; filename="{filename}"'
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
        
        if scope == 'current' and page_idx is not None:
            filename = f"{job['filename']}_page_{page_idx + 1}_export.json"
        else:
            filename = f"{job['filename']}_export.json"

        if len(results) == 1:
            return jsonify(results[0]), 200, {
                'Content-Type': 'application/json',
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
            
        return jsonify(results), 200, {
            'Content-Type': 'application/json',
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
    
    return jsonify({'error': 'Invalid format'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=True)
