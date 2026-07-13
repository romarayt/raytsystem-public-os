# Result schema

Return a human-readable projection backed by a typed result equivalent to:

```json
{
  "schema_version": "raytsystem.watch.result.v1",
  "run_id": "watch_<stable-id>",
  "status": "complete|partial|blocked",
  "mode": "summary|timeline|automation|frames|transcript",
  "source": {
    "kind": "local_file",
    "display_identity": "safe redacted identity",
    "content_sha256": "sha256 or null before acquisition",
    "url_identity_sha256": "safe URL identity hash or null",
    "duration_ms": 12000,
    "streams": ["video", "audio"]
  },
  "transcript": {
    "method": "embedded_caption|public_caption|local_asr|supplied|none",
    "language": "en",
    "artifact_ref": "watch-artifact://...",
    "cue_count": 3,
    "confidence": 0.96
  },
  "timeline": [
    {
      "start_ms": 1000,
      "end_ms": 4000,
      "spoken": ["..."],
      "shown": ["..."],
      "screen_text": ["..."],
      "actions": ["..."],
      "transitions": ["..."],
      "inferences": ["..."],
      "confidence": 0.9,
      "evidence_refs": ["transcript:c1", "frame:f2", "ocr:o2"],
      "uncertainty": []
    }
  ],
  "summary": ["evidence-bound finding"],
  "automation_brief": null,
  "frames": [{"timestamp_ms": 2000, "artifact_ref": "watch-artifact://..."}],
  "artifacts": [{"ref": "watch-artifact://...", "sha256": "...", "parent_refs": []}],
  "tool_versions": [{"tool": "video.probe", "version": "..."}],
  "limitations": ["..."],
  "errors": [{"stage": "video.ocr_frames", "code": "timeout", "retryable": true}],
  "approvals": [{"scope_hash": "...", "decision": "approved", "expires_at": "..."}]
}
```

## Human projection

Always include:

1. source identity and duration;
2. status and selected mode;
3. transcript acquisition method;
4. concise findings appropriate to the mode;
5. important timestamps and evidence type (`said`, `shown`, `screen text`, `action`,
   `transition`, or explicit `inference`);
6. local links/references to derivatives;
7. source identity hash and tool versions;
8. limitations, uncovered intervals, uncertainty, redactions, and partial failures.

Do not merge conflicting speech and visual evidence. Report the conflict with both evidence refs.
Use `null` for unavailable timestamps instead of inventing values. Quotes remain short and
evidence-bound; paraphrase by default.
