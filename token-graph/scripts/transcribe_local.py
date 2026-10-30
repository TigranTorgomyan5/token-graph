#!/usr/bin/env python3
"""Transcribe only project-local media with a caller-provided local Whisper model directory."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) not in (4, 5):
        print("usage: transcribe_local.py PROJECT LOCAL_WHISPER_MODEL_DIR OUTPUT_DIR [--skip-missing]", file=sys.stderr)
        return 2
    root, model_dir, out_dir = (Path(value).expanduser().resolve() for value in sys.argv[1:4])
    skip_missing = len(sys.argv) == 5 and sys.argv[4] == "--skip-missing"
    if not root.is_dir():
        raise SystemExit(f"Project directory does not exist: {root}")
    from graphify.detect import detect
    from graphify.transcribe import transcribe
    detection = detect(root)
    videos = [Path(value).resolve() for value in detection.get("files", {}).get("video", [])]
    status_path = out_dir.parent / "TOKEN_GRAPH_MEDIA_STATUS.md"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    if not videos:
        status_path.write_text("No audio/video files were detected.\n", encoding="utf-8")
        print("No local audio/video files found; transcription skipped.")
        return 0
    if not model_dir.is_dir() or not (model_dir / "model.bin").is_file():
        if skip_missing:
            status_path.write_text(f"Skipped {len(videos)} audio/video file(s): no cached local Whisper model was found.\n", encoding="utf-8")
            print(f"Skipped {len(videos)} audio/video file(s): no cached local Whisper model was found.")
            return 0
        raise SystemExit("This project contains audio/video. Pass --whisper-model-dir with an already-downloaded local faster-whisper model directory containing model.bin. No model download is attempted.")
    if importlib.util.find_spec("faster_whisper") is None:
        if skip_missing:
            status_path.write_text(f"Skipped {len(videos)} audio/video file(s): faster-whisper is not installed in the Token Graph runtime.\n", encoding="utf-8")
            print("Skipped audio/video files: faster-whisper is unavailable locally.")
            return 0
        raise SystemExit("The Token Graph runtime lacks faster-whisper. Install faster-whisper separately from local packages; Token Graph does not install packages.")
    os.environ["GRAPHIFY_WHISPER_MODEL"] = str(model_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir.parent / ".token-graph-transcripts.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": {}}
    except (OSError, json.JSONDecodeError):
        manifest = {"files": {}}
    old_files = manifest.get("files", {})
    updated: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    for media in videos:
        relative = media.relative_to(root).as_posix()
        digest = hashlib.sha256(media.read_bytes()).hexdigest()
        key = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
        transcript_dir = out_dir / key
        transcript = transcript_dir / f"{media.stem}.txt"
        prior = old_files.get(relative, {})
        if prior.get("sha256") == digest and transcript.is_file():
            updated[relative] = {"sha256": digest, "transcript": transcript.relative_to(out_dir).as_posix()}
            print(f"Cached local transcript: {relative}")
            continue
        try:
            transcript_dir.mkdir(parents=True, exist_ok=True)
            path = transcribe(media, output_dir=transcript_dir, initial_prompt="Transcribe accurately. Preserve technical terms and proper names.", force=True)
            path = Path(path).resolve()
            if not path.is_file():
                raise RuntimeError("transcriber did not create a transcript")
            updated[relative] = {"sha256": digest, "transcript": path.relative_to(out_dir).as_posix()}
            print(f"Transcribed locally: {relative}")
        except Exception as exc:
            failures.append(f"{relative}: {exc}")
    # Remove only transcript files previously owned by this manifest and no longer represented.
    for relative, row in old_files.items():
        if relative in updated:
            continue
        stale = (out_dir / row.get("transcript", "")).resolve()
        if out_dir in stale.parents and stale.is_file():
            stale.unlink()
    manifest_path.write_text(json.dumps({"files": updated}, indent=2), encoding="utf-8")
    if failures:
        for failure in failures:
            print(f"Transcription failed: {failure}", file=sys.stderr)
        if not skip_missing:
            return 1
    status_path.write_text(f"Locally transcribed {len(updated)} of {len(videos)} audio/video file(s).\n", encoding="utf-8")
    print(f"Locally transcribed {len(updated)} audio/video file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
