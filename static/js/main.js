// GLM-OCR Inference | Frontend Logic

const dropZone = document.getElementById('dropZone');
const fileInputPdf = document.getElementById('fileInputPdf');
const fileInputImages = document.getElementById('fileInputImages');
const modePdfBtn = document.getElementById('modePdf');
const modeImagesBtn = document.getElementById('modeImages');
const bundlePrompt = document.getElementById('bundlePrompt');
const bundleText = document.getElementById('bundleText');
const bundleYesBtn = document.getElementById('bundleYes');
const bundleNoBtn = document.getElementById('bundleNo');
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
const docTitle = document.getElementById('docTitle');
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
let pollRetryCount = 0;
const MAX_POLL_RETRIES = 120;

// Initialize
async function init() {
    console.log("Initializing GLM-OCR Inference...");

    try {
        const response = await fetch('/api/jobs');
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
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

// Upload Mode Buttons
let pendingImageFiles = null;

if (modePdfBtn) modePdfBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    fileInputPdf.click();
});
if (modeImagesBtn) modeImagesBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    fileInputImages.click();
});

if (fileInputPdf) fileInputPdf.addEventListener('change', (e) => {
    if (e.target.files.length > 0) uploadFiles(e.target.files, 'pdf');
    e.target.value = '';
});

if (fileInputImages) fileInputImages.addEventListener('change', (e) => {
    if (e.target.files.length > 0) promptBundleOrUpload(e.target.files);
    e.target.value = '';
});

function promptBundleOrUpload(files) {
    if (files.length <= 1) {
        uploadFiles(files, 'image', false);
        return;
    }
    pendingImageFiles = files;
    if (bundleText) bundleText.innerText = `Bundle ${files.length} images as one document?`;
    if (bundlePrompt) bundlePrompt.style.display = 'block';
}

if (bundleYesBtn) bundleYesBtn.addEventListener('click', () => {
    if (bundlePrompt) bundlePrompt.style.display = 'none';
    if (pendingImageFiles) uploadFiles(pendingImageFiles, 'image', true);
    pendingImageFiles = null;
});
if (bundleNoBtn) bundleNoBtn.addEventListener('click', () => {
    if (bundlePrompt) bundlePrompt.style.display = 'none';
    if (pendingImageFiles) uploadFiles(pendingImageFiles, 'image', false);
    pendingImageFiles = null;
});

// Drag & Drop with auto-detection
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
        if (files.length === 0) return;

        const IMAGE_EXTS = ['jpg', 'jpeg', 'png'];
        let hasPdf = false, hasImage = false;
        for (const f of files) {
            const ext = f.name.split('.').pop().toLowerCase();
            if (ext === 'pdf') hasPdf = true;
            else if (IMAGE_EXTS.includes(ext)) hasImage = true;
            else {
                alert(`Unsupported file type: ${f.name}`);
                return;
            }
        }

        if (hasPdf && hasImage) {
            alert('Please upload either PDFs or images, not both at once.');
            return;
        }

        if (hasPdf) {
            uploadFiles(files, 'pdf');
        } else {
            promptBundleOrUpload(files);
        }
    });
}

async function uploadFiles(files, mode, bundle) {
    if (dropZone) dropZone.style.display = 'none';
    if (bundlePrompt) bundlePrompt.style.display = 'none';
    if (progressContainer) progressContainer.style.display = 'block';

    const formData = new FormData();
    formData.append('mode', mode);
    if (mode === 'image') formData.append('bundle', bundle ? 'true' : 'false');
    for (const file of files) {
        formData.append('files', file);
    }

    try {
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) throw new Error(`Upload failed (${response.status})`);
        const data = await response.json();

        if (data.jobs && data.jobs.length > 0) {
            currentJobId = data.jobs[0].job_id;
            pollStatus();
            if (data.jobs.length > 1) openHistory();
        } else if (data.job_id) {
            currentJobId = data.job_id;
            pollStatus();
        } else {
            alert('Upload failed: ' + (data.error || 'Unknown error'));
            resetUI();
        }
    } catch (err) {
        alert('Error uploading files: ' + err.message);
        resetUI();
    }
}

async function pollStatus() {
    if (!currentJobId || isPolling) return;
    isPolling = true;

    try {
        const response = await fetch(`/api/status/${currentJobId}`);
        if (!response.ok) {
            if (response.status === 404) { resetUI(); isPolling = false; return; }
            throw new Error(`Server error: ${response.status}`);
        }
        const data = await response.json();
        pollRetryCount = 0;

        if (docTitle && data.filename) docTitle.textContent = data.filename;

        const oldFinished = totalPagesFinished;
        totalPagesFinished = data.total_pages_finished || 0;
        const lastPageIdx = data.last_page_index || 0;

        if (totalPagesFinished > 0) {
            if (resultView.style.display === 'none' || resultView.style.display === '') {
                uploadView.style.display = 'none';
                resultView.style.display = 'grid';
                if (newScanBtn) newScanBtn.style.display = 'block';

                renderPage(lastPageIdx);
            } else if (totalPagesFinished > oldFinished) {
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
        } else if (data.status === 'cancelled') {
            if (progressContainer) progressContainer.style.display = 'none';
            if (scanningIndicator) scanningIndicator.style.display = 'none';
            if (statusText) statusText.innerText = 'Job cancelled.';
            isPolling = false;
        } else if (data.status === 'cancelling') {
            if (progressContainer) progressContainer.style.display = 'block';
            if (scanningIndicator) scanningIndicator.style.display = 'block';
            if (statusText) statusText.innerText = 'Cancelling after current page...';
            setTimeout(() => {
                isPolling = false;
                pollStatus();
            }, 2000);
        } else {
            if (progressContainer) progressContainer.style.display = 'block';
            if (scanningIndicator) scanningIndicator.style.display = 'block';

            const progress = data.progress || 0;
            if (progressBar) progressBar.style.width = `${progress}%`;

            let statusMsg = 'Initializing...';
            if (data.status === 'splitting') {
                statusMsg = 'Splitting PDF into pages...';
            } else if (data.status === 'processing') {
                if (data.total_pages) {
                    statusMsg = `Processing page ${data.total_pages_finished + 1} of ${data.total_pages}... (${progress}%)`;
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
        pollRetryCount++;
        if (pollRetryCount >= MAX_POLL_RETRIES) {
            if (statusText) statusText.innerText = 'Status polling timed out. Refresh the page to retry.';
            if (scanningIndicator) scanningIndicator.style.display = 'none';
        } else {
            setTimeout(pollStatus, 5000);
        }
    }
}

async function renderPage(index) {
    if (index < 0) index = 0;
    if (totalPagesFinished > 0 && index >= totalPagesFinished) index = totalPagesFinished - 1;

    currentPageIndex = index;
    const jobIdAtStart = currentJobId;

    let page = pageCache[index];

    if (!page) {
        if (markdownContainer) markdownContainer.innerHTML = '<div class="loading-spinner">Loading page content...</div>';
        try {
            const response = await fetch(`/api/page/${currentJobId}/${index}`);
            if (currentJobId !== jobIdAtStart) return;
            if (!response.ok) throw new Error(`Server error: ${response.status}`);
            page = await response.json();
            pageCache[index] = page;
        } catch (err) {
            console.error('Failed to fetch page:', err);
            if (currentJobId === jobIdAtStart && markdownContainer) {
                markdownContainer.innerHTML = '<div class="error-msg">Failed to load page content.</div>';
            }
            return;
        }
    }

    if (currentJobId !== jobIdAtStart) return;

    if (markdownContainer) {
        const rawHtml = marked.parse(page.content || "*(No text detected yet)*");
        markdownContainer.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(rawHtml) : rawHtml;
        markdownContainer.scrollTop = 0;
    }

    updateImagePreview();
    updateNavigationUI();

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
let jobsRefreshInterval = null;

function closeJobsModal() {
    if (historyModal) historyModal.style.display = 'none';
    if (jobsRefreshInterval) {
        clearInterval(jobsRefreshInterval);
        jobsRefreshInterval = null;
    }
}

if (historyBtn) historyBtn.addEventListener('click', openHistory);
if (closeHistoryBtn) closeHistoryBtn.addEventListener('click', closeJobsModal);
if (historyModal) historyModal.addEventListener('click', (e) => {
    if (e.target === historyModal) closeJobsModal();
});

async function openHistory() {
    historyModal.style.display = 'flex';
    await renderJobsList();

    if (jobsRefreshInterval) clearInterval(jobsRefreshInterval);
    jobsRefreshInterval = setInterval(async () => {
        if (historyModal.style.display !== 'flex') {
            clearInterval(jobsRefreshInterval);
            jobsRefreshInterval = null;
            return;
        }
        await renderJobsList();
    }, 3000);
}

async function cancelJob(jobId, e) {
    e.stopPropagation();
    try {
        const resp = await fetch(`/api/cancel/${jobId}`, { method: 'POST' });
        if (resp.ok) await renderJobsList();
    } catch (err) {
        console.error('Cancel failed:', err);
    }
}

async function cancelAllJobs() {
    try {
        const resp = await fetch('/api/cancel_all', { method: 'POST' });
        if (resp.ok) await renderJobsList();
    } catch (err) {
        console.error('Cancel all failed:', err);
    }
}

async function renderJobsList() {
    try {
        const response = await fetch('/api/jobs');
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const jobsList = await response.json();

        if (jobsList.length === 0) {
            historyList.innerHTML = '<div class="loading-spinner">No jobs yet.</div>';
            return;
        }

        historyList.innerHTML = '';
        let hasActive = false;
        jobsList.forEach(job => {
            const date = new Date(job.start_time * 1000).toLocaleString();
            const isActive = ['queued', 'splitting', 'processing', 'cancelling'].includes(job.status);
            if (isActive) hasActive = true;

            const item = document.createElement('div');
            item.className = 'history-item';
            item.setAttribute('role', 'button');
            item.setAttribute('tabindex', '0');

            const info = document.createElement('div');
            info.className = 'history-info';
            const title = document.createElement('h4');
            title.textContent = job.filename;
            const meta = document.createElement('div');
            meta.className = 'history-meta';
            meta.textContent = `${date} • ${job.page_count} pages`;
            info.appendChild(title);
            info.appendChild(meta);

            const actions = document.createElement('div');
            actions.className = 'history-actions';

            if (isActive && job.status !== 'cancelling') {
                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'btn-cancel';
                cancelBtn.textContent = 'Cancel';
                cancelBtn.onclick = (e) => cancelJob(job.job_id, e);
                actions.appendChild(cancelBtn);
            }

            const badge = document.createElement('div');
            badge.className = `history-badge ${job.status}`;
            badge.textContent = job.status;
            actions.appendChild(badge);

            item.appendChild(info);
            item.appendChild(actions);

            const activate = () => { loadJob(job.job_id); closeJobsModal(); };
            item.onclick = activate;
            item.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } };
            historyList.appendChild(item);
        });

        // Cancel All button
        const cancelAllContainer = document.getElementById('cancelAllContainer');
        if (cancelAllContainer) {
            cancelAllContainer.style.display = hasActive ? 'block' : 'none';
        }

        if (!hasActive && jobsRefreshInterval) {
            clearInterval(jobsRefreshInterval);
            jobsRefreshInterval = null;
        }
    } catch (err) {
        const errDiv = document.createElement('div');
        errDiv.className = 'loading-spinner';
        errDiv.style.color = 'red';
        errDiv.textContent = `Error: ${err.message}`;
        historyList.innerHTML = '';
        historyList.appendChild(errDiv);
    }
}

function loadJob(jobId) {
    currentJobId = jobId;
    totalPagesFinished = 0;
    currentPageIndex = 0;
    pageCache = {};
    isPolling = false;
    pollRetryCount = 0;

    if (markdownContainer) markdownContainer.innerHTML = '<div class="loading-spinner">Waiting for pages...</div>';
    if (pagePreview) pagePreview.src = '';
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
    if (docTitle) docTitle.textContent = 'Original Document';
    if (progressBar) progressBar.style.width = '0%';
    if (statusText) statusText.innerText = 'Ready to scan';
}

if (exportBtn) {
    const dropdown = exportBtn.closest('.dropdown');
    exportBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        dropdown.classList.toggle('active');
    });
    document.addEventListener('click', () => dropdown.classList.remove('active'));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') dropdown.classList.remove('active'); });
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
