import json
import os
import time
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from tests.conftest import seed_completed_job


class TestUploadValidation:

    def test_rejects_missing_mode(self, client, make_upload_file):
        resp = client.post(
            "/api/upload",
            data={"files": [make_upload_file("photo.jpg")]},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "mode" in resp.get_json()["error"]

    def test_rejects_invalid_mode(self, client, make_upload_file):
        resp = client.post(
            "/api/upload",
            data={"mode": "video", "files": [make_upload_file("photo.jpg")]},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_rejects_no_files(self, client):
        resp = client.post(
            "/api/upload",
            data={"mode": "pdf"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "No files" in resp.get_json()["error"]

    def test_rejects_images_in_pdf_mode(self, client, make_upload_file):
        resp = client.post(
            "/api/upload",
            data={"mode": "pdf", "files": [make_upload_file("photo.jpg")]},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "does not match" in resp.get_json()["error"]

    def test_rejects_pdfs_in_image_mode(self, client, make_upload_file):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "files": [make_upload_file("doc.pdf")]},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "does not match" in resp.get_json()["error"]

    @pytest.mark.parametrize("filename", [
        "document.txt",
        "image.bmp",
        "archive.zip",
        "no_extension",
    ])
    def test_rejects_disallowed_extensions(self, client, make_upload_file, filename):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "files": [make_upload_file(filename)]},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize("filename,mode", [
        ("photo.jpg", "image"),
        ("photo.jpeg", "image"),
        ("photo.png", "image"),
        ("PHOTO.JPG", "image"),
        ("document.pdf", "pdf"),
        ("DOCUMENT.PDF", "pdf"),
    ])
    def test_accepts_allowed_extensions(self, client, make_upload_file,
                                        sync_executor, mock_glmocr, filename, mode):
        resp = client.post(
            "/api/upload",
            data={"mode": mode, "files": [make_upload_file(filename)]},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert len(resp.get_json()["jobs"]) == 1


class TestUploadJobCreation:

    def test_single_image_creates_one_job(self, client, make_upload_file,
                                          sync_executor, mock_glmocr):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "bundle": "false",
                  "files": [make_upload_file("photo.jpg")]},
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert len(body["jobs"]) == 1
        assert "job_id" in body
        assert body["job_id"] == body["jobs"][0]["job_id"]

    def test_single_pdf_creates_one_job(self, client, make_upload_file,
                                        sync_executor, mock_glmocr, mock_pdfium):
        resp = client.post(
            "/api/upload",
            data={"mode": "pdf", "files": [make_upload_file("document.pdf")]},
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert len(body["jobs"]) == 1
        assert "job_id" in body

    def test_multiple_images_bundled(self, client, make_upload_file,
                                     sync_executor, mock_glmocr):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "bundle": "true", "files": [
                make_upload_file("a.jpg"),
                make_upload_file("b.png"),
                make_upload_file("c.jpeg"),
            ]},
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert len(body["jobs"]) == 1
        assert "3 images" in body["jobs"][0]["filename"]

    def test_multiple_images_separate(self, client, make_upload_file,
                                      sync_executor, mock_glmocr):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "bundle": "false", "files": [
                make_upload_file("a.jpg"),
                make_upload_file("b.jpg"),
                make_upload_file("c.jpg"),
            ]},
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert len(body["jobs"]) == 3

    def test_multiple_pdfs_separate(self, client, make_upload_file,
                                    sync_executor, mock_glmocr, mock_pdfium):
        resp = client.post(
            "/api/upload",
            data={"mode": "pdf", "files": [
                make_upload_file("doc1.pdf"),
                make_upload_file("doc2.pdf"),
            ]},
            content_type="multipart/form-data",
        )
        body = resp.get_json()
        assert len(body["jobs"]) == 2
        filenames = {j["filename"] for j in body["jobs"]}
        assert "doc1.pdf" in filenames
        assert "doc2.pdf" in filenames
        assert "job_id" not in body

    def test_upload_saves_file_to_disk(self, client, make_upload_file, tmp_dirs):
        with patch("app.job_executor") as mock_exec:
            mock_exec.submit = MagicMock()
            resp = client.post(
                "/api/upload",
                data={"mode": "image", "files": [make_upload_file("test.jpg")]},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        upload_files = list(tmp_dirs["upload"].iterdir())
        assert len(upload_files) == 1
        assert "test.jpg" in upload_files[0].name

    def test_filename_sanitization(self, client, make_upload_file, tmp_dirs):
        with patch("app.job_executor") as mock_exec:
            mock_exec.submit = MagicMock()
            resp = client.post(
                "/api/upload",
                data={"mode": "image",
                      "files": [make_upload_file("my:file*name.jpg")]},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert ":" not in body["jobs"][0]["filename"]
        assert "*" not in body["jobs"][0]["filename"]


class TestOcrWorker:

    def test_image_worker_completes(self, client, make_upload_file,
                                    sync_executor, mock_glmocr, app_module):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "files": [make_upload_file("test.jpg")]},
            content_type="multipart/form-data",
        )
        job_id = resp.get_json()["job_id"]
        job = app_module.jobs[job_id]
        assert job["status"] == "completed"
        assert job["progress"] == 100
        assert job["total_pages_finished"] == 1
        assert "output_dir" in job

    def test_worker_calls_model_parse(self, client, make_upload_file,
                                      sync_executor, mock_glmocr):
        client.post(
            "/api/upload",
            data={"mode": "image", "files": [make_upload_file("test.jpg")]},
            content_type="multipart/form-data",
        )
        assert mock_glmocr.parse.call_count == 1
        call_arg = mock_glmocr.parse.call_args[0][0]
        assert call_arg.endswith("page_0.jpg")

    def test_worker_creates_session_file(self, client, make_upload_file,
                                         sync_executor, mock_glmocr, tmp_dirs):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "files": [make_upload_file("test.jpg")]},
            content_type="multipart/form-data",
        )
        job_id = resp.get_json()["job_id"]
        session_file = tmp_dirs["sessions"] / f"{job_id}.json"
        assert session_file.exists()
        state = json.loads(session_file.read_text())
        assert state["status"] == "completed"

    def test_worker_creates_pages_data(self, client, make_upload_file,
                                       sync_executor, mock_glmocr, app_module):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "files": [make_upload_file("test.jpg")]},
            content_type="multipart/form-data",
        )
        job_id = resp.get_json()["job_id"]
        output_dir = app_module.jobs[job_id]["output_dir"]
        page_file = os.path.join(output_dir, "pages_data", "page_0.json")
        assert os.path.exists(page_file)

        with open(page_file) as f:
            page_data = json.load(f)
        assert page_data["page_num"] == 1
        assert "content" in page_data

    def test_worker_cleans_up_upload_file(self, client, make_upload_file,
                                          sync_executor, mock_glmocr, tmp_dirs):
        client.post(
            "/api/upload",
            data={"mode": "image", "files": [make_upload_file("test.jpg")]},
            content_type="multipart/form-data",
        )
        remaining = list(tmp_dirs["upload"].iterdir())
        assert len(remaining) == 0

    def test_worker_sets_failed_on_error(self, app_instance, app_module, tmp_dirs, mock_glmocr):
        mock_glmocr.parse.side_effect = RuntimeError("CUDA out of memory")

        job_id = "fail-test"
        upload_path = str(tmp_dirs["upload"] / "fail_test.jpg")
        img = Image.new("RGB", (10, 10), "red")
        img.save(upload_path, "JPEG")

        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "fail_test.jpg", "status": "queued",
            "progress": 0, "total_pages_finished": 0, "last_page_index": 0,
            "start_time": time.time(),
        }

        app_module.ocr_worker(job_id, upload_path)

        assert app_module.jobs[job_id]["status"] == "failed"
        assert app_module.jobs[job_id]["error"] == "OCR processing failed"

    def test_pdf_worker_splits_and_processes(self, client, make_upload_file,
                                             sync_executor, mock_glmocr,
                                             mock_pdfium, app_module):
        resp = client.post(
            "/api/upload",
            data={"mode": "pdf", "files": [make_upload_file("report.pdf")]},
            content_type="multipart/form-data",
        )
        job_id = resp.get_json()["job_id"]
        job = app_module.jobs[job_id]
        assert job["status"] == "completed"
        assert job["total_pages_finished"] == 3
        assert mock_glmocr.parse.call_count == 3


class TestOcrWorkerBatch:

    def test_batch_processes_all_pages(self, client, make_upload_file,
                                       sync_executor, mock_glmocr, app_module):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "bundle": "true", "files": [
                make_upload_file("a.jpg"),
                make_upload_file("b.jpg"),
                make_upload_file("c.jpg"),
            ]},
            content_type="multipart/form-data",
        )
        job_id = resp.get_json()["job_id"]
        job = app_module.jobs[job_id]
        assert job["status"] == "completed"
        assert job["total_pages_finished"] == 3
        assert mock_glmocr.parse.call_count == 3

    def test_batch_cleans_up_all_uploads(self, client, make_upload_file,
                                         sync_executor, mock_glmocr, tmp_dirs):
        client.post(
            "/api/upload",
            data={"mode": "image", "bundle": "true", "files": [
                make_upload_file("a.jpg"),
                make_upload_file("b.jpg"),
            ]},
            content_type="multipart/form-data",
        )
        remaining = list(tmp_dirs["upload"].iterdir())
        assert len(remaining) == 0

    def test_batch_progress_reaches_100(self, client, make_upload_file,
                                        sync_executor, mock_glmocr, app_module):
        resp = client.post(
            "/api/upload",
            data={"mode": "image", "bundle": "true", "files": [
                make_upload_file("a.jpg"),
                make_upload_file("b.jpg"),
            ]},
            content_type="multipart/form-data",
        )
        job_id = resp.get_json()["job_id"]
        assert app_module.jobs[job_id]["progress"] == 100


class TestJobStatus:

    def test_returns_job_state(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "job-123", filename="test.pdf")
        resp = client.get("/api/status/job-123")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "completed"
        assert body["progress"] == 100
        assert body["filename"] == "test.pdf"

    def test_returns_404_for_unknown_job(self, client):
        resp = client.get("/api/status/nonexistent-id")
        assert resp.status_code == 404

    def test_loads_from_disk_when_not_in_memory(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "disk-only")
        del app_module.jobs["disk-only"]

        resp = client.get("/api/status/disk-only")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "completed"


class TestPageContent:

    def test_returns_page_data(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "pg-test", num_pages=3)
        resp = client.get("/api/page/pg-test/1")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["page_num"] == 2
        assert "content" in body

    def test_returns_404_for_unknown_job(self, client):
        resp = client.get("/api/page/no-such-job/0")
        assert resp.status_code == 404

    def test_returns_404_for_missing_page(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "pg-test2", num_pages=2)
        resp = client.get("/api/page/pg-test2/99")
        assert resp.status_code == 404

    def test_returns_404_when_output_not_ready(self, client, app_module):
        app_module.jobs["queued-job"] = {
            "job_id": "queued-job", "status": "queued", "progress": 0,
        }
        resp = client.get("/api/page/queued-job/0")
        assert resp.status_code == 404
        assert "Output not ready" in resp.get_json()["error"]


class TestExport:

    def test_export_all_markdown(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "exp-md", num_pages=2,
                          content="# Hello")
        resp = client.get("/api/export/exp-md?format=markdown&scope=all")
        assert resp.status_code == 200
        assert resp.content_type == "text/markdown"
        text = resp.get_data(as_text=True)
        assert "Page 1" in text
        assert "Page 2" in text
        assert "---" in text

    def test_export_single_page_markdown(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "exp-md1", num_pages=3,
                          content="# Content")
        resp = client.get("/api/export/exp-md1?format=markdown&scope=current&page_idx=1")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "Page 2" in text
        assert "Page 1" not in text
        assert "Page 3" not in text

    def test_export_all_json(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "exp-json", num_pages=2)
        resp = client.get("/api/export/exp-json?format=json&scope=all")
        assert resp.status_code == 200
        body = resp.get_json()
        assert isinstance(body, list)
        assert len(body) == 2

    def test_export_single_page_json(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "exp-json1", num_pages=2)
        resp = client.get("/api/export/exp-json1?format=json&scope=current&page_idx=0")
        assert resp.status_code == 200
        body = resp.get_json()
        assert isinstance(body, list)
        assert len(body) == 1

    def test_export_has_content_disposition(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "exp-dl", filename="report.pdf")
        resp = client.get("/api/export/exp-dl?format=markdown")
        assert "Content-Disposition" in resp.headers
        assert "attachment" in resp.headers["Content-Disposition"]
        assert "report.pdf" in resp.headers["Content-Disposition"]

    def test_export_unknown_job_returns_404(self, client):
        resp = client.get("/api/export/no-job?format=markdown")
        assert resp.status_code == 404

    def test_export_invalid_format_returns_400(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "exp-bad")
        resp = client.get("/api/export/exp-bad?format=csv")
        assert resp.status_code == 400
        assert "Invalid format" in resp.get_json()["error"]


class TestJobListing:

    def test_lists_completed_jobs(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "list-1", filename="a.pdf")
        seed_completed_job(app_module, tmp_dirs, "list-2", filename="b.pdf")
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 2
        job_ids = {j["job_id"] for j in body}
        assert "list-1" in job_ids
        assert "list-2" in job_ids

    def test_sorted_newest_first(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "old-job")
        app_module.jobs["old-job"]["start_time"] = 1000.0
        app_module.save_job_state("old-job")

        seed_completed_job(app_module, tmp_dirs, "new-job")
        app_module.jobs["new-job"]["start_time"] = 2000.0
        app_module.save_job_state("new-job")

        resp = client.get("/api/jobs")
        body = resp.get_json()
        assert body[0]["job_id"] == "new-job"
        assert body[1]["job_id"] == "old-job"

    def test_empty_when_no_jobs(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_includes_queued_jobs_without_output_dir(self, client, app_module):
        app_module.jobs["queued-1"] = {
            "job_id": "queued-1", "filename": "pending.pdf", "status": "queued",
            "progress": 0, "total_pages_finished": 0, "start_time": 3000.0,
        }
        resp = client.get("/api/jobs")
        body = resp.get_json()
        assert len(body) == 1
        assert body[0]["job_id"] == "queued-1"
        assert body[0]["status"] == "queued"

    def test_includes_processing_jobs(self, client, app_module):
        app_module.jobs["proc-1"] = {
            "job_id": "proc-1", "filename": "active.pdf", "status": "processing",
            "progress": 50, "total_pages_finished": 2, "start_time": 3000.0,
        }
        resp = client.get("/api/jobs")
        body = resp.get_json()
        assert len(body) == 1
        assert body[0]["status"] == "processing"

    def test_excludes_missing_output_dir(self, client, tmp_dirs):
        job_state = {
            "job_id": "orphan", "filename": "gone.pdf", "status": "completed",
            "output_dir": "/nonexistent/path", "start_time": 1000.0,
        }
        session_path = tmp_dirs["sessions"] / "orphan.json"
        session_path.write_text(json.dumps(job_state))

        resp = client.get("/api/jobs")
        assert len(resp.get_json()) == 0
        assert not session_path.exists()


class TestJobStatePersistence:

    def test_save_load_roundtrip(self, app_module, app_instance, tmp_dirs):
        app_module.jobs["rt-test"] = {
            "job_id": "rt-test", "status": "processing", "progress": 42,
        }
        app_module.save_job_state("rt-test")

        loaded = app_module.load_job_state("rt-test")
        assert loaded["job_id"] == "rt-test"
        assert loaded["status"] == "processing"
        assert loaded["progress"] == 42

    def test_load_returns_none_for_missing(self, app_module, app_instance):
        assert app_module.load_job_state("does-not-exist") is None

    def test_load_handles_empty_file(self, app_module, app_instance, tmp_dirs):
        empty_file = tmp_dirs["sessions"] / "empty.json"
        empty_file.write_text("")

        assert app_module.load_job_state("empty") is None
        assert not empty_file.exists()

    def test_load_handles_corrupted_json(self, app_module, app_instance, tmp_dirs):
        bad_file = tmp_dirs["sessions"] / "corrupt.json"
        bad_file.write_text("{not valid json!!!")

        assert app_module.load_job_state("corrupt") is None
        assert not bad_file.exists()


class TestLastPageTracking:

    def test_updates_and_persists(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "lp-test")
        resp = client.post("/api/last_page/lp-test", json={"page_index": 5})
        assert resp.status_code == 200
        assert app_module.jobs["lp-test"]["last_page_index"] == 5

        loaded = app_module.load_job_state("lp-test")
        assert loaded["last_page_index"] == 5

    def test_returns_404_for_unknown_job(self, client):
        resp = client.post("/api/last_page/no-job", json={"page_index": 0})
        assert resp.status_code == 404

    def test_restores_from_disk(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "disk-lp")
        del app_module.jobs["disk-lp"]

        resp = client.post("/api/last_page/disk-lp", json={"page_index": 3})
        assert resp.status_code == 200
        assert app_module.jobs["disk-lp"]["last_page_index"] == 3


class TestUploadCleanup:

    def test_removes_old_files(self, app_module, app_instance, tmp_dirs):
        old_file = tmp_dirs["upload"] / "old_upload.jpg"
        old_file.write_bytes(b"fake image")
        old_mtime = time.time() - (25 * 3600)
        os.utime(str(old_file), (old_mtime, old_mtime))

        app_module.cleanup_old_uploads()
        assert not old_file.exists()

    def test_preserves_recent_files(self, app_module, app_instance, tmp_dirs):
        recent_file = tmp_dirs["upload"] / "recent_upload.jpg"
        recent_file.write_bytes(b"fake image")

        app_module.cleanup_old_uploads()
        assert recent_file.exists()


class TestImageServing:

    def test_serves_existing_image(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "img-test", num_pages=1)
        resp = client.get("/api/image/img-test/page_0.jpg")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")

    def test_returns_404_for_unknown_job(self, client):
        resp = client.get("/api/image/no-job/page_0.jpg")
        assert resp.status_code == 404


class TestJobIdValidation:

    @pytest.mark.parametrize("endpoint", [
        "/api/status/../../etc/passwd",
        "/api/page/../../etc/passwd/0",
        "/api/export/../../etc/passwd",
        "/api/image/../../../etc/passwd/page_0.jpg",
    ])
    def test_rejects_path_traversal(self, client, endpoint):
        resp = client.get(endpoint)
        assert resp.status_code in (400, 404)

    @pytest.mark.parametrize("endpoint", [
        "/api/status/..%2F..%2Fetc%2Fpasswd",
        "/api/page/..%2Fsecret/0",
        "/api/export/..%2F..%2Fpasswd",
    ])
    def test_rejects_encoded_traversal(self, client, endpoint):
        resp = client.get(endpoint)
        assert resp.status_code in (400, 404)

    def test_last_page_rejects_path_traversal(self, client):
        resp = client.post(
            "/api/last_page/../../etc/passwd",
            json={"page_index": 0},
        )
        assert resp.status_code in (400, 404)

    @pytest.mark.parametrize("bad_id", [
        "../secret",
        "job/../../etc",
        "job%00id",
        "job id spaces",
        "job.json",
    ])
    def test_rejects_malformed_job_ids(self, client, bad_id):
        resp = client.get(f"/api/status/{bad_id}")
        assert resp.status_code in (400, 404)

    def test_accepts_valid_uuid_job_id(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        resp = client.get("/api/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert resp.status_code == 200

    def test_accepts_simple_alphanumeric_id(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "test-job-123")
        resp = client.get("/api/status/test-job-123")
        assert resp.status_code == 200


class TestExportEdgeCases:

    def test_export_failed_job_returns_404(self, client, app_module):
        app_module.jobs["fail-exp"] = {
            "job_id": "fail-exp", "filename": "broken.pdf",
            "status": "failed", "error": "OCR processing failed",
            "progress": 0, "total_pages_finished": 0,
            "start_time": 1700000000.0,
        }
        resp = client.get("/api/export/fail-exp?format=markdown")
        assert resp.status_code == 404
        assert "Output not ready" in resp.get_json()["error"]

    def test_export_in_progress_job_returns_404(self, client, app_module):
        app_module.jobs["prog-exp"] = {
            "job_id": "prog-exp", "filename": "processing.pdf",
            "status": "processing", "progress": 50,
            "total_pages_finished": 1,
            "start_time": 1700000000.0,
        }
        resp = client.get("/api/export/prog-exp?format=json")
        assert resp.status_code == 404
        assert "Output not ready" in resp.get_json()["error"]

    def test_export_empty_results_json_returns_404(self, client, app_module, tmp_dirs):
        output_dir = str(tmp_dirs["output"] / "empty-export")
        os.makedirs(os.path.join(output_dir, "pages_data"), exist_ok=True)
        app_module.jobs["empty-exp"] = {
            "job_id": "empty-exp", "filename": "empty.pdf",
            "status": "completed", "progress": 100,
            "total_pages_finished": 0,
            "output_dir": output_dir,
            "start_time": 1700000000.0,
        }
        resp = client.get("/api/export/empty-exp?format=json")
        assert resp.status_code == 404
        assert "No page data" in resp.get_json()["error"]

    def test_export_empty_results_markdown_returns_404(self, client, app_module, tmp_dirs):
        output_dir = str(tmp_dirs["output"] / "empty-md-export")
        os.makedirs(os.path.join(output_dir, "pages_data"), exist_ok=True)
        app_module.jobs["empty-md"] = {
            "job_id": "empty-md", "filename": "empty.pdf",
            "status": "completed", "progress": 100,
            "total_pages_finished": 0,
            "output_dir": output_dir,
            "start_time": 1700000000.0,
        }
        resp = client.get("/api/export/empty-md?format=markdown")
        assert resp.status_code == 404
        assert "No page data" in resp.get_json()["error"]


class TestWorkerCleanup:

    def test_upload_cleaned_on_failure(self, app_instance, app_module, tmp_dirs, mock_glmocr):
        mock_glmocr.parse.side_effect = RuntimeError("CUDA out of memory")

        job_id = "cleanup-fail"
        upload_path = str(tmp_dirs["upload"] / "cleanup_test.jpg")
        img = Image.new("RGB", (10, 10), "red")
        img.save(upload_path, "JPEG")

        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "cleanup_test.jpg", "status": "queued",
            "progress": 0, "total_pages_finished": 0, "last_page_index": 0,
            "start_time": time.time(),
        }

        app_module.ocr_worker(job_id, upload_path)
        assert not os.path.exists(upload_path)

    def test_batch_uploads_cleaned_on_failure(self, app_instance, app_module, tmp_dirs, mock_glmocr):
        mock_glmocr.parse.side_effect = RuntimeError("CUDA out of memory")

        job_id = "batch-cleanup-fail"
        paths = []
        for name in ["a.jpg", "b.jpg", "c.jpg"]:
            p = str(tmp_dirs["upload"] / f"{job_id}_{name}")
            Image.new("RGB", (10, 10), "red").save(p, "JPEG")
            paths.append(p)

        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "3 images (a.jpg, ...)",
            "status": "queued", "progress": 0, "total_pages_finished": 0,
            "last_page_index": 0, "start_time": time.time(),
        }

        app_module.ocr_worker_batch_images(job_id, paths)
        for p in paths:
            assert not os.path.exists(p)


class TestAtomicSessionWrites:

    def test_no_temp_files_left_after_save(self, app_module, app_instance, tmp_dirs):
        app_module.jobs["atomic-test"] = {
            "job_id": "atomic-test", "status": "processing", "progress": 50,
        }
        app_module.save_job_state("atomic-test")

        session_files = list(tmp_dirs["sessions"].iterdir())
        json_files = [f for f in session_files if f.suffix == ".json"]
        tmp_files = [f for f in session_files if f.suffix == ".tmp"]
        assert len(json_files) == 1
        assert len(tmp_files) == 0

    def test_session_file_is_valid_json_after_save(self, app_module, app_instance, tmp_dirs):
        app_module.jobs["valid-json"] = {
            "job_id": "valid-json", "status": "completed", "progress": 100,
        }
        app_module.save_job_state("valid-json")

        session_path = tmp_dirs["sessions"] / "valid-json.json"
        data = json.loads(session_path.read_text())
        assert data["status"] == "completed"
        assert data["progress"] == 100


class TestCancelJob:

    def test_cancel_queued_job(self, client, app_module, make_upload_file, tmp_dirs):
        with patch("app.job_executor") as mock_exec:
            mock_exec.submit = MagicMock(return_value=MagicMock(cancel=MagicMock(return_value=True)))
            resp = client.post(
                "/api/upload",
                data={"mode": "image", "files": [make_upload_file("test.jpg")]},
                content_type="multipart/form-data",
            )
        job_id = resp.get_json()["job_id"]

        resp = client.post(f"/api/cancel/{job_id}")
        assert resp.status_code == 200
        assert app_module.jobs[job_id]["status"] == "cancelled"

    def test_cancel_sets_event_for_running_job(self, client, app_module):
        import threading
        job_id = "running-cancel"
        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "test.pdf",
            "status": "processing", "progress": 50,
            "total_pages_finished": 2, "start_time": 1700000000.0,
        }
        app_module._cancel_events[job_id] = threading.Event()

        resp = client.post(f"/api/cancel/{job_id}")
        assert resp.status_code == 200
        assert app_module._cancel_events[job_id].is_set()
        assert app_module.jobs[job_id]["status"] == "cancelling"
        assert resp.get_json()["status"] == "cancelling"

    def test_cancel_completed_job_is_noop(self, client, app_module, tmp_dirs):
        seed_completed_job(app_module, tmp_dirs, "done-job")
        resp = client.post("/api/cancel/done-job")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "completed"

    def test_cancel_unknown_job_returns_404(self, client):
        resp = client.post("/api/cancel/nonexistent")
        assert resp.status_code == 404

    def test_cancel_all(self, client, app_module):
        import threading
        for jid, status in [("q1", "queued"), ("q2", "processing"), ("done", "completed")]:
            app_module.jobs[jid] = {
                "job_id": jid, "filename": f"{jid}.pdf", "status": status,
                "progress": 0, "total_pages_finished": 0, "start_time": 1700000000.0,
            }
            app_module._cancel_events[jid] = threading.Event()

        resp = client.post("/api/cancel_all")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "q1" in body["cancelled"]
        assert "q2" in body["cancelled"]
        assert app_module._cancel_events["q1"].is_set()
        assert app_module._cancel_events["q2"].is_set()
        assert not app_module._cancel_events["done"].is_set()
        assert app_module.jobs["q1"]["status"] == "cancelled"
        assert app_module.jobs["q2"]["status"] == "cancelling"

    def test_worker_stops_on_cancel(self, app_instance, app_module, tmp_dirs, mock_glmocr):
        import threading
        job_id = "cancel-mid"
        upload_path = str(tmp_dirs["upload"] / "cancel_test.pdf")
        Image.new("RGB", (10, 10), "red").save(upload_path, "JPEG")

        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "cancel_test.pdf", "status": "queued",
            "progress": 0, "total_pages_finished": 0, "last_page_index": 0,
            "start_time": time.time(),
        }
        evt = threading.Event()
        app_module._cancel_events[job_id] = evt

        call_count = 0
        original_parse = mock_glmocr.parse.side_effect

        def parse_then_cancel(img_path):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                evt.set()
            result = app_module.mock_ocr_result if hasattr(app_module, 'mock_ocr_result') else MagicMock()
            result._last_img_path = img_path

            img_stem = os.path.splitext(os.path.basename(img_path))[0]
            page_dir = os.path.join(app_module.jobs[job_id]["output_dir"], img_stem)
            os.makedirs(page_dir, exist_ok=True)
            with open(os.path.join(page_dir, f"{img_stem}.md"), "w") as f:
                f.write("# Test")
            with open(os.path.join(page_dir, f"{img_stem}.json"), "w") as f:
                json.dump([{"content": "# Test", "label": "text"}], f)
            vis_dir = os.path.join(page_dir, "layout_vis")
            os.makedirs(vis_dir, exist_ok=True)
            Image.new("RGB", (1, 1), "red").save(os.path.join(vis_dir, f"{img_stem}.jpg"))

            result.save = MagicMock(side_effect=lambda output_dir: None)
            return result

        mock_glmocr.parse.side_effect = parse_then_cancel

        mock_pdfium = MagicMock()
        mock_pdfium.__len__ = MagicMock(return_value=5)
        pages = []
        for _ in range(5):
            page = MagicMock()
            bitmap = MagicMock()
            bitmap.to_pil.return_value = Image.new("RGB", (100, 100), "white")
            page.render.return_value = bitmap
            pages.append(page)
        mock_pdfium.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
        mock_pdfium.close = MagicMock()

        with patch("app.pdfium.PdfDocument", return_value=mock_pdfium):
            app_module.ocr_worker(job_id, upload_path)

        assert app_module.jobs[job_id]["status"] == "cancelled"
        assert mock_glmocr.parse.call_count < 5

    def test_cancel_during_last_page_stays_cancelled(
        self, app_instance, app_module, tmp_dirs, mock_glmocr
    ):
        """Cancel set while processing the final page must not be overwritten to 'completed'."""
        import threading
        job_id = "cancel-last"
        upload_path = str(tmp_dirs["upload"] / "cancel_last.pdf")
        Image.new("RGB", (10, 10), "red").save(upload_path, "JPEG")

        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "cancel_last.pdf", "status": "queued",
            "progress": 0, "total_pages_finished": 0, "last_page_index": 0,
            "start_time": time.time(),
        }
        evt = threading.Event()
        app_module._cancel_events[job_id] = evt

        num_pages = 2

        def parse_cancel_on_last(img_path):
            if mock_glmocr.parse.call_count >= num_pages:
                evt.set()
            result = MagicMock()
            result._last_img_path = img_path
            img_stem = os.path.splitext(os.path.basename(img_path))[0]
            page_dir = os.path.join(app_module.jobs[job_id]["output_dir"], img_stem)
            os.makedirs(page_dir, exist_ok=True)
            with open(os.path.join(page_dir, f"{img_stem}.md"), "w") as f:
                f.write("# Test")
            with open(os.path.join(page_dir, f"{img_stem}.json"), "w") as f:
                json.dump([{"content": "# Test", "label": "text"}], f)
            vis_dir = os.path.join(page_dir, "layout_vis")
            os.makedirs(vis_dir, exist_ok=True)
            Image.new("RGB", (1, 1), "red").save(os.path.join(vis_dir, f"{img_stem}.jpg"))
            result.save = MagicMock(side_effect=lambda output_dir: None)
            return result

        mock_glmocr.parse.side_effect = parse_cancel_on_last

        mock_pdfium = MagicMock()
        mock_pdfium.__len__ = MagicMock(return_value=num_pages)
        pages = []
        for _ in range(num_pages):
            page = MagicMock()
            bitmap = MagicMock()
            bitmap.to_pil.return_value = Image.new("RGB", (100, 100), "white")
            page.render.return_value = bitmap
            pages.append(page)
        mock_pdfium.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
        mock_pdfium.close = MagicMock()

        with patch("app.pdfium.PdfDocument", return_value=mock_pdfium):
            app_module.ocr_worker(job_id, upload_path)

        assert app_module.jobs[job_id]["status"] == "cancelled"
        assert mock_glmocr.parse.call_count == num_pages

    def test_worker_exits_early_if_cancelled_before_start(
        self, app_instance, app_module, tmp_dirs, mock_glmocr
    ):
        """A queued job cancelled before the worker starts should not process any pages."""
        import threading
        job_id = "pre-cancel"
        upload_path = str(tmp_dirs["upload"] / "pre_cancel.jpg")
        Image.new("RGB", (10, 10), "red").save(upload_path, "JPEG")

        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "pre_cancel.jpg", "status": "queued",
            "progress": 0, "total_pages_finished": 0, "last_page_index": 0,
            "start_time": time.time(),
        }
        evt = threading.Event()
        evt.set()
        app_module._cancel_events[job_id] = evt

        app_module.ocr_worker(job_id, upload_path)

        assert app_module.jobs[job_id]["status"] == "cancelled"
        assert mock_glmocr.parse.call_count == 0
        assert not os.path.exists(upload_path)

    def test_batch_worker_exits_early_if_cancelled_before_start(
        self, app_instance, app_module, tmp_dirs, mock_glmocr
    ):
        """A queued batch job cancelled before the worker starts should not process any pages."""
        import threading
        job_id = "pre-cancel-batch"
        paths = []
        for name in ["x.jpg", "y.jpg"]:
            p = str(tmp_dirs["upload"] / f"{job_id}_{name}")
            Image.new("RGB", (10, 10), "red").save(p, "JPEG")
            paths.append(p)

        app_module.jobs[job_id] = {
            "job_id": job_id, "filename": "2 images (x.jpg, ...)",
            "status": "queued", "progress": 0, "total_pages_finished": 0,
            "last_page_index": 0, "start_time": time.time(),
        }
        evt = threading.Event()
        evt.set()
        app_module._cancel_events[job_id] = evt

        app_module.ocr_worker_batch_images(job_id, paths)

        assert app_module.jobs[job_id]["status"] == "cancelled"
        assert mock_glmocr.parse.call_count == 0
        for p in paths:
            assert not os.path.exists(p)
