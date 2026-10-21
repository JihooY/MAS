"""Schema-respecting model responses; no real model inference in automated tests."""
import json

from backend.app.music_evaluation import RUBRIC


def observations(source_id="s", track_id="v"):
    return [{"source_id": source_id, "track_id": track_id, "kind": k, "scope": "section", "group_id": "group",
             "evidence_id": f"audio:{source_id}:{k}", "provides_song_overview": k == "full_mix",
             "raw_response": "Sustained tone and accompaniment.", "content": "Sustained tone and accompaniment.",
             "audio": {"source_range_seconds": [0, 1]}, "musical_features": ["Tone"], "uncertainties": []}
            for k in ("full_mix", "vocals", "instrumental")]


def item(source_id="s", track_id="v"):
    return {"name": "song.wav", "source_id": source_id, "track_id": track_id, "range_seconds": [0, 1],
            "source_range_seconds": [0, 1], "region": {"median_pitch_hz": 220, "valid_pitch_frames": 10}}


def response_for(body):
    payload = json.loads(body["messages"][1]["content"])
    stage = payload["stage"]
    sources = {i["source_id"] for i in payload["dsp_evidence"]}
    if stage != "integrated":
        return {"analyses": [{"source_id": sid, "summary": "Interpretation for " + stage,
            "strengths": ["Controlled phrase."], "weaknesses": ["Uneven ending."], "improvements": ["Practice the ending."],
            "evidence_ids": [o["evidence_id"] for o in payload["music_flamingo"] if o["source_id"] == sid],
            "uncertainties": ["Separation artifacts may affect this observation."]} for sid in sorted(sources)],
            "comparison": "Compare the audible recordings; no claim about universal singer skill.", "uncertainties": []}
    overviews, interactions, assessments = [], [], []
    for sid in sorted(sources):
        context = [o for o in payload["full_mix_context"] if o["source_id"] == sid]
        overview_refs = [o["evidence_id"] for o in context if o["provides_song_overview"]]
        refs = [o["evidence_id"] for o in context if o["scope"] == "section"]
        overviews.append({"source_id": sid, "summary": "Whole-song energy contour.", "evidence_ids": overview_refs, "uncertainties": []})
        interactions.append({"source_id": sid, "summary": "The vocal phrasing sits against the accompaniment pulse.", "evidence_ids": refs, "uncertainties": ["Manual alignment."]})
        scores = {}
        for key, (_, _, _, kinds) in RUBRIC.items():
            evidence = [e["id"] for e in payload["evidence_catalog"] if e["source_id"] == sid and e["kind"] in kinds and e["scope"] == "section"]
            enough = any(i["source_id"] == sid and i["region"].get("valid_pitch_frames", 0) >= 3 for i in payload["dsp_evidence"])
            scores[key] = {"value": None if key == "fundamentals" and not enough else 8., "reason": "Phrase-level evidence.",
                           "confidence": "medium", "evidence_ids": evidence, "uncertainty": "Subjective estimate."}
        assessments.append({"source_id": sid, "strengths": ["Coherent expression."], "weaknesses": ["Uneven articulation."],
                            "improvements": ["Rehearse the marked phrase with the accompaniment."], "scores": scores})
    return {"song_overviews": overviews, "interactions": interactions, "comparison": "Integrated context comparison.",
            "assessments": assessments, "uncertainties": ["Scores are uncalibrated model estimates."]}
