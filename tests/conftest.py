import json
import os
from concurrent.futures import Future
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


@pytest.fixture()
def tmp_dirs(tmp_path):
    dirs = {
        "upload": tmp_path / "uploads",
        "output": tmp_path / "output",
        "sessions": tmp_path / "sessions",
    }
    for d in dirs.values():
        d.mkdir()
    return dirs


@pytest.fixture()
def app_module():
    import app as mod
    return mod


@pytest.fixture()
def app_instance(tmp_dirs, app_module):
    orig_upload = app_module.app.config["UPLOAD_FOLDER"]
    orig_output = app_module.app.config["OUTPUT_FOLDER"]
    orig_sessions = app_module.app.config["SESSIONS_FOLDER"]
    orig_upload_var = app_module.UPLOAD_FOLDER
    orig_jobs = app_module.jobs.copy()
    orig_cancel_events = app_module._cancel_events.copy()
    orig_job_futures = app_module._job_futures.copy()

    app_module.app.config["UPLOAD_FOLDER"] = str(tmp_dirs["upload"])
    app_module.app.config["OUTPUT_FOLDER"] = str(tmp_dirs["output"])
    app_module.app.config["SESSIONS_FOLDER"] = str(tmp_dirs["sessions"])
    app_module.UPLOAD_FOLDER = str(tmp_dirs["upload"])
    app_module.jobs.clear()
    app_module._cancel_events.clear()
    app_module._job_futures.clear()
    app_module.app.config["TESTING"] = True

    yield app_module.app

    app_module.app.config["UPLOAD_FOLDER"] = orig_upload
    app_module.app.config["OUTPUT_FOLDER"] = orig_output
    app_module.app.config["SESSIONS_FOLDER"] = orig_sessions
    app_module.UPLOAD_FOLDER = orig_upload_var
    app_module.jobs.clear()
    app_module.jobs.update(orig_jobs)
    app_module._cancel_events.clear()
    app_module._cancel_events.update(orig_cancel_events)
    app_module._job_futures.clear()
    app_module._job_futures.update(orig_job_futures)


@pytest.fixture()
def client(app_instance):
    return app_instance.test_client()


@pytest.fixture()
def mock_ocr_result():
    def _make(content="# Recognized Text\n\nSample content."):
        result = MagicMock()

        def fake_save(output_dir):
            img_path = getattr(result, "_last_img_path", "page_0.jpg")
            img_stem = os.path.splitext(os.path.basename(img_path))[0]
            page_dir = os.path.join(output_dir, img_stem)
            os.makedirs(page_dir, exist_ok=True)

            with open(os.path.join(page_dir, f"{img_stem}.md"), "w") as f:
                f.write(content)

            with open(os.path.join(page_dir, f"{img_stem}.json"), "w") as f:
                json.dump([{"content": content, "label": "text"}], f)

            vis_dir = os.path.join(page_dir, "layout_vis")
            os.makedirs(vis_dir, exist_ok=True)
            vis_img = Image.new("RGB", (1, 1), "red")
            vis_img.save(os.path.join(vis_dir, f"{img_stem}.jpg"))

        result.save = MagicMock(side_effect=fake_save)
        return result

    return _make


@pytest.fixture()
def mock_glmocr(mock_ocr_result):
    model = MagicMock()
    result_instance = mock_ocr_result()

    def parse_side_effect(img_path):
        result_instance._last_img_path = img_path
        return result_instance

    model.parse = MagicMock(side_effect=parse_side_effect)
    model.__enter__ = MagicMock(return_value=model)
    model.__exit__ = MagicMock(return_value=False)

    with patch("app.GlmOcr", return_value=model):
        yield model


@pytest.fixture()
def mock_pdfium():
    def _make_pdf(num_pages=3):
        mock_pdf = MagicMock()
        mock_pdf.__len__ = MagicMock(return_value=num_pages)

        pages = []
        for _ in range(num_pages):
            page = MagicMock()
            bitmap = MagicMock()
            bitmap.to_pil.return_value = Image.new("RGB", (100, 100), "white")
            page.render.return_value = bitmap
            pages.append(page)

        mock_pdf.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
        mock_pdf.close = MagicMock()
        return mock_pdf

    mock_doc = _make_pdf()

    with patch("app.pdfium.PdfDocument", return_value=mock_doc) as mock_cls:
        mock_cls._make_pdf = _make_pdf
        yield mock_cls


@pytest.fixture()
def sync_executor():
    def run_sync(fn, *args, **kwargs):
        future = Future()
        try:
            result = fn(*args, **kwargs)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        return future

    with patch("app.job_executor") as mock_executor:
        mock_executor.submit = MagicMock(side_effect=run_sync)
        yield mock_executor


@pytest.fixture()
def make_upload_file():
    def _make(filename):
        buf = BytesIO()
        img = Image.new("RGB", (10, 10), "white")
        img.save(buf, "JPEG")
        buf.seek(0)
        return (buf, filename)

    return _make


def seed_completed_job(app_module, tmp_dirs, job_id, filename="test.pdf",
                       num_pages=2, content="# Page content"):
    output_dir = str(tmp_dirs["output"] / f"job_{job_id}")
    os.makedirs(output_dir, exist_ok=True)

    pages_data_dir = os.path.join(output_dir, "pages_data")
    os.makedirs(pages_data_dir, exist_ok=True)

    for i in range(num_pages):
        page_data = {
            "page_num": i + 1,
            "content": f"{content} {i + 1}",
            "image": f"/api/image/{job_id}/page_{i}.jpg",
            "layout_image": f"/api/image/{job_id}/page_{i}/layout_vis/page_{i}.jpg",
        }
        with open(os.path.join(pages_data_dir, f"page_{i}.json"), "w") as f:
            json.dump(page_data, f)

        page_dir = os.path.join(output_dir, f"page_{i}")
        os.makedirs(page_dir, exist_ok=True)
        with open(os.path.join(page_dir, f"page_{i}.md"), "w") as f:
            f.write(f"{content} {i + 1}")
        with open(os.path.join(page_dir, f"page_{i}.json"), "w") as f:
            json.dump([{"content": f"{content} {i + 1}", "label": "text"}], f)

        img = Image.new("RGB", (10, 10), "white")
        img.save(os.path.join(output_dir, f"page_{i}.jpg"))

    job_state = {
        "job_id": job_id,
        "filename": filename,
        "status": "completed",
        "progress": 100,
        "total_pages": num_pages,
        "total_pages_finished": num_pages,
        "last_page_index": 0,
        "output_dir": output_dir,
        "start_time": 1700000000.0,
    }

    app_module.jobs[job_id] = job_state

    session_path = str(tmp_dirs["sessions"] / f"{job_id}.json")
    with open(session_path, "w") as f:
        json.dump(job_state, f)

    return job_state
