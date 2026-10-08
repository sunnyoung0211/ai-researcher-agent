"""产物版本与血缘（详细设计 1 第 3.3、3.4、4.1 节）。

- 当前文件放在工作区的自然位置（如 idea/selected.md），所有人直接读；
- 每个新版本拷贝到 .archive/<URL 编码的 artifact_id>/ 下，versions.jsonl 记录版本信息；
- 同一内容再次 put 不产生新版本（哈希相同返回已有 VersionRef），所以 step() 重入是安全的；
- 目录型产物：版本哈希 = 清单哈希。runs/、templates/ 只追加，只记清单不拷贝；
  其他目录（如 paper/）会被改写，每个版本把目录树（去掉 exclude 部分）完整拷贝。
"""

from __future__ import annotations

import fnmatch
import shutil
import threading
from pathlib import Path
from urllib.parse import quote, unquote

from .errors import NotFound, PermissionDenied, ValidationFailed
from .events import Events
from .fsutil import append_jsonl, atomic_write_bytes, now, read_jsonl, sha256_bytes, sha256_file
from .models.artifact import ArtifactVersion, LineageNode
from .models.common import ArtifactKind, Producer, VersionRef

APPEND_ONLY_PREFIXES = ("runs/", "templates/")


def _is_append_only(artifact_id: str) -> bool:
    return artifact_id.startswith(APPEND_ONLY_PREFIXES)


def _excluded(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatchcase(rel, pat):  # 区分大小写，Mac / Windows 行为一致
            return True
        if pat.endswith("/**") and (rel == pat[:-3] or rel.startswith(pat[:-2])):
            return True
    return False


def dir_manifest(path: Path, exclude: list[str]) -> list[tuple[str, str]]:
    """目录内文件（去掉 exclude 部分）按路径排序后的 (相对路径, sha256) 清单。不跟随符号链接。"""
    items = []
    for p in sorted(path.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(path).as_posix()
        if _excluded(rel, exclude):
            continue
        items.append((rel, sha256_file(p)))
    return items


def manifest_bytes(manifest: list[tuple[str, str]]) -> bytes:
    return "".join(f"{sha}  {rel}\n" for rel, sha in manifest).encode("utf-8")


class Archive:
    def __init__(self, root: Path, events: Events):
        self.root = Path(root).resolve()
        self.dir = self.root / ".archive"
        self.events = events
        self._lock = threading.RLock()

    # ---------- 路径 ----------
    def _adir(self, artifact_id: str) -> Path:
        return self.dir / quote(artifact_id, safe="")

    def path_of(self, artifact_id: str) -> Path:
        p = (self.root / artifact_id).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionDenied(f"产物路径越出了项目目录：{artifact_id}")
        return p

    # ---------- 写 ----------
    def put(
        self,
        artifact_id: str,
        kind: ArtifactKind,
        content: str | bytes | Path,
        parents: list[VersionRef] | None = None,
        producer: Producer = "system",
        note: str = "",
        exclude: list[str] | None = None,
    ) -> VersionRef:
        artifact_id = artifact_id.strip("/")
        exclude = list(exclude or [])
        target = self.path_of(artifact_id)
        with self._lock:
            if isinstance(content, str | bytes):
                data = content.encode("utf-8") if isinstance(content, str) else content
                atomic_write_bytes(target, data)
            elif isinstance(content, Path):
                src = content.resolve()
                if src != target:
                    if not src.is_file():
                        raise ValidationFailed(f"只能登记位于 {artifact_id} 的目录；收到 {content}")
                    atomic_write_bytes(target, src.read_bytes())
            else:
                raise TypeError("content 必须是 str、bytes 或 Path")
            if not target.exists():
                raise NotFound(f"产物不存在：{artifact_id}")

            is_dir = target.is_dir()
            manifest = dir_manifest(target, exclude) if is_dir else None
            sha = sha256_bytes(manifest_bytes(manifest)) if is_dir else sha256_file(target)

            latest = self.latest_version(artifact_id)
            if latest is not None and latest.ref.sha256 == sha:
                return latest.ref  # 幂等：内容没变，不产生新版本

            version = (latest.ref.version + 1) if latest else 1
            ref = VersionRef(artifact_id=artifact_id, version=version, sha256=sha)
            adir = self._adir(artifact_id)
            adir.mkdir(parents=True, exist_ok=True)
            stored_at: str | None
            if is_dir:
                assert manifest is not None
                atomic_write_bytes(adir / f"v{version:04d}.manifest", manifest_bytes(manifest))
                if _is_append_only(artifact_id + "/"):
                    stored_at = None
                else:
                    dest = adir / f"v{version:04d}"
                    if dest.exists():
                        shutil.rmtree(dest)  # 上次崩溃留下的半成品
                    for rel, _ in manifest:
                        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target / rel, dest / rel)
                    stored_at = dest.relative_to(self.root).as_posix()
            else:
                dest = adir / f"v{version:04d}{target.suffix}"
                shutil.copy2(target, dest)
                stored_at = dest.relative_to(self.root).as_posix()

            av = ArtifactVersion(
                ref=ref, kind=kind, path=artifact_id, stored_at=stored_at, is_dir=is_dir, exclude=exclude,
                parents=list(parents or []), producer=producer, note=note, created_at=now(),
            )
            append_jsonl(adir / "versions.jsonl", av)
        actor = producer if producer.startswith(("agent:", "human")) else "system"
        etype = "artifact.human_edited" if producer == "human-edited" else "artifact.created"
        self.events.append(
            etype, f"{artifact_id} 新版本 v{version}" + (f"：{note}" if note else ""), actor=actor, refs=[ref],
            data={"kind": kind},
        )
        return ref

    # ---------- 读 ----------
    def versions(self, artifact_id: str) -> list[ArtifactVersion]:
        rows = read_jsonl(self._adir(artifact_id.strip("/")) / "versions.jsonl")
        return [ArtifactVersion.model_validate(r) for r in rows]

    def latest_version(self, artifact_id: str) -> ArtifactVersion | None:
        vs = self.versions(artifact_id)
        return vs[-1] if vs else None

    def latest(self, artifact_id: str) -> VersionRef | None:
        v = self.latest_version(artifact_id)
        return v.ref if v else None

    def version_info(self, ref: VersionRef) -> ArtifactVersion:
        for v in self.versions(ref.artifact_id):
            if v.ref.version == ref.version:
                return v
        raise NotFound(f"找不到产物版本 {ref.artifact_id} v{ref.version}")

    def get(self, ref: VersionRef) -> bytes:
        """读指定版本并校验哈希。目录型产物返回清单文本。"""
        info = self.version_info(ref)
        adir = self._adir(ref.artifact_id)
        if info.is_dir:
            data = (adir / f"v{ref.version:04d}.manifest").read_bytes()
        else:
            if not info.stored_at:
                raise NotFound(f"{ref.artifact_id} v{ref.version} 没有保存拷贝")
            data = (self.root / info.stored_at).read_bytes()
        if sha256_bytes(data) != info.ref.sha256 or info.ref.sha256 != ref.sha256:
            raise ValidationFailed(f"{ref.artifact_id} v{ref.version} 的哈希校验失败，存档可能被改动")
        return data

    def read_text(self, ref: VersionRef) -> str:
        return self.get(ref).decode("utf-8")

    def stored_path(self, ref: VersionRef) -> Path | None:
        """版本拷贝的位置（文件或目录树）；只追加的目录返回工作区里的原目录。"""
        info = self.version_info(ref)
        if info.stored_at:
            return self.root / info.stored_at
        return self.path_of(ref.artifact_id) if info.is_dir else None

    def artifact_ids(self) -> list[str]:
        if not self.dir.exists():
            return []
        return sorted(unquote(p.name) for p in self.dir.iterdir() if (p / "versions.jsonl").exists())

    def list_latest(self, kind: str | None = None) -> list[ArtifactVersion]:
        out = []
        for aid in self.artifact_ids():
            v = self.latest_version(aid)
            if v and (kind is None or v.kind == kind):
                out.append(v)
        return out

    def lineage(self, ref: VersionRef, depth: int = 5) -> LineageNode:
        try:
            info = self.version_info(ref)
        except NotFound:
            return LineageNode(ref=ref)
        node = LineageNode(ref=ref, kind=info.kind, producer=info.producer, note=info.note)
        if depth > 0:
            node.parents = [self.lineage(p, depth - 1) for p in info.parents]
        return node

    # ---------- 人工编辑检测（3.4） ----------
    def current_sha(self, artifact_id: str, exclude: list[str] | None = None) -> str | None:
        p = self.path_of(artifact_id)
        if not p.exists():
            return None
        if p.is_dir():
            return sha256_bytes(manifest_bytes(dir_manifest(p, exclude or [])))
        return sha256_file(p)

    def sync(self, paths: list[str] | None = None) -> list[VersionRef]:
        """当前文件哈希 ≠ 最新版本哈希时，自动登记 producer="human-edited" 的新版本。"""
        created = []
        with self._lock:
            for aid in paths if paths is not None else self.artifact_ids():
                if _is_append_only(aid + "/"):
                    continue
                latest = self.latest_version(aid)
                if latest is None:
                    continue
                cur = self.current_sha(aid, latest.exclude)
                if cur is None or cur == latest.ref.sha256:
                    continue
                ref = self.put(
                    aid, latest.kind, self.path_of(aid), parents=[latest.ref], producer="human-edited",
                    note="检测到人工修改", exclude=latest.exclude,
                )
                created.append(ref)
        return created
