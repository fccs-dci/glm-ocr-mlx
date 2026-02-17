// GLM-OCR Studio | Frontend Logic

const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const uploadView = document.getElementById('uploadView');
const progressContainer = document.getElementById('progressContainer');
const progressBar = document.getElementById('progressBar');
const statusText = document.getElementById('statusText');
const scanningIndicator = document.getElementById('scanningIndicator');
const resultView = document.getElementById('resultView');
const markdownContainer = document.getElementById('markdownContainer');
const pagePreview = document.getElementById('pagePreview');
const newScanBtn = document.getElementById('newScanBtn');
const historyBtn = document.getElementById('historyBtn');
const historyModal = document.getElementById('historyModal');
const closeHistoryBtn = document.getElementById('closeHistoryBtn');
const historyList = document.getElementById('historyList');
const toggleLayoutBtn = document.getElementById('toggleLayoutBtn');
const exportBtn = document.getElementById('exportBtn');

// Jump Inputs
const imageJumpInput = document.getElementById('imageJumpInput');
const textJumpInput = document.getElementById('textJumpInput');
const imagePageTotal = document.getElementById('imagePageTotal');
const textPageTotal = document.getElementById('textPageTotal');

let currentJobId = null;
let totalPagesFinished = 0;
let currentPageIndex = 0;
let isPolling = false;
let showLayout = false;
let pageCache = {};

// Initialize
async function init() {
    console.log("Initializing GLM-OCR Studio (Rapid Nav Version)...");

    try {
        const response = await fetch('/api/jobs');
        const jobs = await response.json();

        if (jobs.length > 0) {
            const latestJob = jobs[0];
            const now = Date.now() / 1000;
            if (latestJob.status !== 'completed' || (now - latestJob.start_time < 3600)) {
                loadJob(latestJob.job_id);
            }
        }
    } catch (err) {
        console.error('Initial session restore failed:', err);
    }
}

// Drag & Drop Handlers
if (dropZone) dropZone.addEventListener('click', () => fileInput.click());

if (dropZone) {
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) handleFileUpload(files[0]);
    });
}

if (fileInput) fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) handleFileUpload(e.target.files[0]);
});

async function handleFileUpload(file) {
    if (dropZone) dropZone.style.display = 'none';
    if (progressContainer) progressContainer.style.display = 'block';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (data.job_id) {
            currentJobId = data.job_id;
            pollStatus();
        } else {
            alert('Upload failed: ' + data.error);
            resetUI();
        }
    } catch (err) {
        alert('Error uploading file: ' + err.message);
        resetUI();
    }
}

async function pollStatus() {
    if (!currentJobId || isPolling) return;
    isPolling = true;

    try {
        const response = await fetch(`/api/status/${currentJobId}`);
        const data = await response.json();

        const oldFinished = totalPagesFinished;
        totalPagesFinished = data.total_pages_finished || 0;
        const lastPageIdx = data.last_page_index || 0;

        if (totalPagesFinished > 0) {
            if (resultView.style.display === 'none' || resultView.style.display === '') {
                uploadView.style.display = 'none';
                resultView.style.display = 'grid';
                if (newScanBtn) newScanBtn.style.display = 'block';

                // Restore last viewed page
                renderPage(lastPageIdx);
            } else if (totalPagesFinished > oldFinished) {
                // If it's a fresh job load (oldFinished was 0)
                if (oldFinished === 0) {
                    renderPage(lastPageIdx);
                } else {
                    updateNavigationUI();
                }
            }
        }

        if (data.status === 'completed') {
            if (progressContainer) progressContainer.style.display = 'none';
            if (scanningIndicator) scanningIndicator.style.display = 'none';
            isPolling = false;
        } else if (data.status === 'failed') {
            alert('OCR Processing failed: ' + data.error);
            resetUI();
            isPolling = false;
        } else {
            if (progressContainer) progressContainer.style.display = 'block';
            if (scanningIndicator) scanningIndicator.style.display = 'block';

            const progress = data.progress || 0;
            if (progressBar) progressBar.style.width = `${progress}%`;

            let statusMsg = 'Initializing...';
            if (data.status === 'splitting') {
                statusMsg = 'Splitting PDF into pages...';
            } else if (data.status === 'processing') {
                if (data.current_page && data.total_pages) {
                    statusMsg = `Processing page ${data.current_page} of ${data.total_pages}... (${progress}%)`;
                } else {
                    statusMsg = `Processing... (${progress}%)`;
                }
            }
            if (statusText) statusText.innerText = statusMsg;

            setTimeout(() => {
                isPolling = false;
                pollStatus();
            }, 2000);
        }
    } catch (err) {
        console.error('Status poll error:', err);
        isPolling = false;
        if (err.message && err.message.includes('404')) {
            resetUI();
        } else {
            setTimeout(pollStatus, 5000);
        }
    }
}

async function renderPage(index) {
    // Bounds check
    if (index < 0) index = 0;
    if (totalPagesFinished > 0 && index >= totalPagesFinished) index = totalPagesFinished - 1;

    currentPageIndex = index;

    // Check cache first
    let page = pageCache[index];

    if (!page) {
        if (markdownContainer) markdownContainer.innerHTML = '<div class="loading-spinner">Loading page content...</div>';
        try {
            const response = await fetch(`/api/page/${currentJobId}/${index}`);
            page = await response.json();
            pageCache[index] = page;
        } catch (err) {
            console.error('Failed to fetch page:', err);
            if (markdownContainer) markdownContainer.innerHTML = '<div class="error-msg">Failed to load page content.</div>';
            return;
        }
    }

    // Render Markdown
    if (markdownContainer) {
        markdownContainer.innerHTML = marked.parse(page.content || "*(No text detected yet)*");
        markdownContainer.scrollTop = 0;
    }

    // Update Image Preview
    updateImagePreview();

    updateNavigationUI();

    // Save last page index to backend
    fetch(`/api/last_page/${currentJobId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page_index: index })
    }).catch(err => console.error("Failed to save last page:", err));
}

function updateImagePreview() {
    const page = pageCache[currentPageIndex];
    if (!page) return;

    if (pagePreview) {
        const targetSrc = showLayout ? (page.layout_image || page.image) : page.image;
        if (pagePreview.src !== targetSrc) {
            pagePreview.src = targetSrc;
        }
    }

    if (toggleLayoutBtn) {
        toggleLayoutBtn.innerText = showLayout ? "Show Original" : "Show Layout";
        if (showLayout) {
            toggleLayoutBtn.classList.add('active');
        } else {
            toggleLayoutBtn.classList.remove('active');
        }
    }
}

if (toggleLayoutBtn) {
    toggleLayoutBtn.addEventListener('click', () => {
        showLayout = !showLayout;
        updateImagePreview();
    });
}

function updateNavigationUI() {
    const total = totalPagesFinished;
    const current = currentPageIndex + 1;

    if (imageJumpInput) imageJumpInput.value = current;
    if (textJumpInput) textJumpInput.value = current;
    if (imagePageTotal) imagePageTotal.innerText = total;
    if (textPageTotal) textPageTotal.innerText = total;
}

// Jump Input Handlers
function handleJump(input) {
    let val = parseInt(input.value);
    if (!isNaN(val)) {
        if (val < 1) val = 1;
        if (val > totalPagesFinished) val = totalPagesFinished;
        renderPage(val - 1);
    } else {
        input.value = currentPageIndex + 1;
    }
}

if (imageJumpInput) {
    imageJumpInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            handleJump(imageJumpInput);
            imageJumpInput.blur();
        }
    });
    imageJumpInput.addEventListener('blur', () => handleJump(imageJumpInput));
}

if (textJumpInput) {
    textJumpInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            handleJump(textJumpInput);
            textJumpInput.blur();
        }
    });
    textJumpInput.addEventListener('blur', () => handleJump(textJumpInput));
}

// Navigation
const prevBtn = document.getElementById('prevBtn');
const nextBtn = document.getElementById('nextBtn');

if (prevBtn) prevBtn.addEventListener('click', () => {
    if (currentPageIndex > 0) renderPage(currentPageIndex - 1);
});

if (nextBtn) nextBtn.addEventListener('click', () => {
    if (currentPageIndex < totalPagesFinished - 1) renderPage(currentPageIndex + 1);
});

if (newScanBtn) newScanBtn.addEventListener('click', () => {
    if (confirm('Start a new scan?')) {
        resetUI();
    }
});

// History Modal Logic
if (historyBtn) historyBtn.addEventListener('click', openHistory);
if (closeHistoryBtn) closeHistoryBtn.addEventListener('click', () => historyModal.style.display = 'none');
if (historyModal) historyModal.addEventListener('click', (e) => {
    if (e.target === historyModal) historyModal.style.display = 'none';
});

async function openHistory() {
    historyModal.style.display = 'flex';
    historyList.innerHTML = '<div class="loading-spinner">Loading history...</div>';

    try {
        const response = await fetch('/api/jobs');
        const jobs = await response.json();

        if (jobs.length === 0) {
            historyList.innerHTML = '<div class="loading-spinner">No scans found on disk.</div>';
            return;
        }

        historyList.innerHTML = '';
        jobs.forEach(job => {
            const date = new Date(job.start_time * 1000).toLocaleString();
            const item = document.createElement('div');
            item.className = 'history-item';
            item.innerHTML = `
                <div class="history-info">
                    <h4>${job.filename}</h4>
                    <div class="history-meta">${date} • ${job.page_count} pages</div>
                </div>
                <div class="history-badge">${job.status}</div>
            `;
            item.onclick = () => {
                loadJob(job.job_id);
                historyModal.style.display = 'none';
            };
            historyList.appendChild(item);
        });
    } catch (err) {
        historyList.innerHTML = `<div class="loading-spinner" style="color:red">Error: ${err.message}</div>`;
    }
}

function loadJob(jobId) {
    currentJobId = jobId;
    totalPagesFinished = 0;
    currentPageIndex = 0;
    pageCache = {};
    isPolling = false;

    if (progressContainer) progressContainer.style.display = 'block';

    pollStatus();
}

function resetUI() {
    currentJobId = null;
    totalPagesFinished = 0;
    currentPageIndex = 0;
    pageCache = {};
    isPolling = false;
    if (uploadView) uploadView.style.display = 'block';
    if (dropZone) dropZone.style.display = 'block';
    if (progressContainer) progressContainer.style.display = 'none';
    if (resultView) resultView.style.display = 'none';
    if (newScanBtn) newScanBtn.style.display = 'none';
    if (progressBar) progressBar.style.width = '0%';
    if (statusText) statusText.innerText = 'Ready to scan';
}

window.handleExport = async function (scope, format) {
    if (!currentJobId) return;

    const url = `/api/export/${currentJobId}?scope=${scope}&format=${format}&page_idx=${currentPageIndex}`;

    // Show indicator
    if (statusText) statusText.innerText = "Generating export...";

    try {
        // We use a simple window.location.href or <a> click for download
        // because the browser handles the download stream better for large files.
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', '');
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        setTimeout(() => {
            if (statusText) statusText.innerText = "Export triggered.";
        }, 1000);
    } catch (err) {
        console.error("Export failed:", err);
        alert("Export failed: " + err.message);
    }
};

init();
