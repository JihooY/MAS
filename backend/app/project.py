"""Upgrade earlier vocal-only projects without discarding their audio or analysis."""


def upgrade_project(project):
    if project.get("schema_version", 1) >= 2:
        return project
    for source in project["tracks"]:
        source.setdefault("original_file", f"{source['id']}.wav")
        source.setdefault("separation_status", "legacy")
        source.setdefault("stems", [{**source, "source_id": source["id"], "file": f"{source['id']}.wav",
                                    "kind": "vocals", "name": "보컬 (이전 파일)", "offset": 0}])
        source["legacy_vocal"] = True
    project["schema_version"] = 2
    return project
