#!/usr/bin/env python3
"""Local web app for converting legacy configs to EX3400 configs."""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_file

from converter import SUPPORTED_PLATFORMS, TARGET_MODELS
from services import (
    APP_VERSION,
    TEMPLATE_PROFILES,
    build_batch_zip,
    build_project_zip,
    process_conversion,
)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


@app.route("/", methods=["GET", "POST"])
def index():
    context = default_context()

    if request.method == "POST":
        context.update(form_context(request.form))
        packaged = process_conversion(
            context["source_config"],
            context["selected_platform"],
            context["stack_members"],
            context["hostname_suffix"],
            context["selected_target_model"],
            context["selected_template_profile"],
            context["port_overrides"],
            context["strip_review_comments"],
            context["include_load_hint"],
            context["redact_secrets"],
        )
        context.update(packaged)

    return render_template("index.html", **context)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": APP_VERSION})


@app.route("/api/platforms")
def api_platforms():
    return jsonify(
        {
            "version": APP_VERSION,
            "platforms": SUPPORTED_PLATFORMS,
            "target_models": TARGET_MODELS,
            "template_profiles": TEMPLATE_PROFILES,
        }
    )


@app.route("/api/convert", methods=["POST"])
def api_convert():
    payload = request.get_json(silent=True) or {}
    packaged = process_conversion(
        payload.get("source_config", ""),
        payload.get("platform", "auto"),
        int(payload.get("stack_members", 1) or 1),
        payload.get("hostname_suffix", "-EX3400"),
        payload.get("target_model", "ex3400_24p"),
        payload.get("template_profile", "none"),
        payload.get("port_overrides", ""),
        bool(payload.get("strip_review_comments", False)),
        bool(payload.get("include_load_hint", False)),
        bool(payload.get("redact_secrets", True)),
    )
    result = packaged["result"]
    return jsonify(
        {
            "version": APP_VERSION,
            "platform": result.platform,
            "config": packaged["output_config"],
            "warnings": result.warnings,
            "port_mapping": result.source_to_target_ports,
            "report": packaged["report"],
        }
    )


@app.route("/api/validate", methods=["POST"])
def api_validate():
    payload = request.get_json(silent=True) or {}
    packaged = process_conversion(
        payload.get("source_config", ""),
        payload.get("platform", "auto"),
        int(payload.get("stack_members", 1) or 1),
        payload.get("hostname_suffix", "-EX3400"),
        payload.get("target_model", "ex3400_24p"),
        payload.get("template_profile", "none"),
        payload.get("port_overrides", ""),
        bool(payload.get("strip_review_comments", False)),
        bool(payload.get("include_load_hint", False)),
        bool(payload.get("redact_secrets", True)),
    )
    return jsonify(packaged["report"])


@app.route("/bundle", methods=["POST"])
def bundle_convert():
    ctx = form_context(request.form)
    packaged = process_conversion(
        ctx["source_config"],
        ctx["selected_platform"],
        ctx["stack_members"],
        ctx["hostname_suffix"],
        ctx["selected_target_model"],
        ctx["selected_template_profile"],
        ctx["port_overrides"],
        ctx["strip_review_comments"],
        ctx["include_load_hint"],
        ctx["redact_secrets"],
    )
    zip_buffer = build_project_zip(
        ctx["source_config"],
        packaged["output_config"],
        packaged["mapping_csv"],
        packaged["report"],
        ctx["include_source_in_exports"],
        "ex3400_conversion_project",
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"ex3400_project_{stamp}.zip",
    )


@app.route("/batch", methods=["POST"])
def batch_convert():
    files = [file for file in request.files.getlist("config_files") if file and file.filename]
    if not files:
        return "No files uploaded", 400

    ctx = form_context(request.form)
    zip_buffer = build_batch_zip(
        files,
        ctx["selected_platform"],
        ctx["selected_target_model"],
        ctx["stack_members"],
        ctx["hostname_suffix"],
        ctx["strip_review_comments"],
        ctx["include_load_hint"],
        ctx["selected_template_profile"],
        ctx["redact_secrets"],
        ctx["include_source_in_exports"],
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"ex3400_batch_{stamp}.zip",
    )


def default_context() -> dict:
    return {
        "app_version": APP_VERSION,
        "platforms": SUPPORTED_PLATFORMS,
        "target_models": TARGET_MODELS,
        "template_profiles": TEMPLATE_PROFILES,
        "result": None,
        "output_config": "",
        "source_config": "",
        "selected_platform": "auto",
        "stack_members": 1,
        "hostname_suffix": "-EX3400",
        "selected_target_model": "ex3400_24p",
        "strip_review_comments": False,
        "include_load_hint": False,
        "redact_secrets": True,
        "include_source_in_exports": False,
        "selected_template_profile": "none",
        "port_overrides": "",
        "stats": None,
        "report": None,
        "mapping_csv": "",
        "matched_view": [],
    }


def form_context(form) -> dict:
    return {
        "source_config": form.get("source_config", ""),
        "selected_platform": form.get("platform", "auto"),
        "stack_members": int(form.get("stack_members") or 1),
        "hostname_suffix": form.get("hostname_suffix", "-EX3400"),
        "selected_target_model": form.get("target_model", "ex3400_24p"),
        "strip_review_comments": form.get("strip_review_comments") == "on",
        "include_load_hint": form.get("include_load_hint") == "on",
        "redact_secrets": form.get("redact_secrets", "on") == "on",
        "include_source_in_exports": form.get("include_source_in_exports") == "on",
        "selected_template_profile": form.get("template_profile", "none"),
        "port_overrides": form.get("port_overrides", ""),
    }


def open_browser_later(url: str) -> None:
    time.sleep(1.5)
    webbrowser.open(url)


# Backward-compatible imports for existing local tests/scripts.
from services import (  # noqa: E402,F401
    apply_port_overrides,
    apply_template_profile,
    build_mapping_csv,
    build_report,
    parse_port_overrides,
    prepare_output,
)


if __name__ == "__main__":
    host = os.environ.get("CONFIG_CONVERT_HOST", "127.0.0.1")
    port = int(os.environ.get("CONFIG_CONVERT_PORT", "5050"))
    url = f"http://{host}:{port}/?theme=dark"
    if os.environ.get("CONFIG_CONVERT_OPEN_BROWSER") == "1":
        threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()
    app.run(host=host, port=port, debug=False, use_reloader=False)
