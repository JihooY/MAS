"""Mongo metadata store; explicit local preview mode before credentials are supplied."""

import json
import os
import subprocess
import time
from copy import deepcopy
from threading import RLock

from pymongo import MongoClient

from .config import Settings


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = RLock()
        self.client = None
        self.collection = None
        self.tunnel = None
        self.error = None
        self.mode = "mongodb" if settings.mongo_uri else "local-preview"

    def connect(self):
        s = self.settings
        if not s.mongo_uri:
            return
        try:
            if s.ssh_alias:
                if s.ssh_alias.startswith("-") or any(c.isspace() for c in s.ssh_alias):
                    raise ValueError("SSH 별칭은 공백 없는 Host 별칭이어야 합니다.")
                self.tunnel = subprocess.Popen(
                    [
                        "ssh",
                        "-N",
                        "-T",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "StrictHostKeyChecking=yes",
                        "-o",
                        "ExitOnForwardFailure=yes",
                        "-o",
                        "ConnectTimeout=8",
                        "-o",
                        "ServerAliveInterval=15",
                        "-o",
                        "ServerAliveCountMax=3",
                        "-L",
                        f"127.0.0.1:{s.ssh_local_port}:{s.ssh_remote_host}:{s.ssh_remote_port}",
                        s.ssh_alias,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                time.sleep(0.5)
                if self.tunnel.poll() is not None:
                    raise RuntimeError(
                        "SSH 터널 실패. 별칭, 키, known_hosts 및 포트 사용 여부를 확인하세요."
                    )
            options = {"directConnection": True} if s.ssh_alias else {}
            self.client = MongoClient(
                s.mongo_uri,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=5000,
                **options,
            )
            self.client.admin.command("ping")
            self.collection = self.client[s.mongo_database].projects
        except Exception:
            self.error = "MongoDB 연결 실패: .env의 URI, SSH 별칭, 키, known_hosts 및 포트를 확인하세요."
            self.close()

    def available(self):
        if self.error:
            raise RuntimeError(self.error)

    def list(self):
        with self.lock:
            self.available()
            if self.collection is not None:
                return list(self.collection.find({}, {"_id": 0}).sort("updated_at", -1))
            return sorted(
                [
                    json.loads(p.read_text(encoding="utf-8"))
                    for p in (self.settings.storage_dir / "projects").glob("*.json")
                ],
                key=lambda p: p["updated_at"],
                reverse=True,
            )

    def get(self, project_id):
        return next((p for p in self.list() if p["id"] == project_id), None)

    def save(self, project):
        with self.lock:
            self.available()
            if self.collection is not None:
                self.collection.replace_one(
                    {"id": project["id"]}, deepcopy(project), upsert=True
                )
            else:
                path = self.settings.storage_dir / "projects" / f"{project['id']}.json"
                tmp = path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(project, ensure_ascii=False, allow_nan=False),
                    encoding="utf-8",
                )
                os.replace(tmp, path)

    def delete(self, project_id):
        with self.lock:
            self.available()
            if self.collection is not None:
                self.collection.delete_one({"id": project_id})
            else:
                (self.settings.storage_dir / "projects" / f"{project_id}.json").unlink(
                    missing_ok=True
                )

    def close(self):
        if self.client is not None:
            self.client.close()
        if self.tunnel and self.tunnel.poll() is None:
            self.tunnel.terminate()
            try:
                self.tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tunnel.kill()
