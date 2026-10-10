"""模板列表（GUI P14 模板管理；详细设计 1 第 9.2 节、详细设计 4 第 7.3 节）。"""

from __future__ import annotations

from pydantic import BaseModel


class TemplateInfo(BaseModel):
    template_id: str  # tpl-01
    version: int
    path: str  # templates/tpl-01/v2/original（原始文件，永不修改）
    filename: str | None = None  # 上传时的文件名
    uploaded_at: str | None = None
    files: list[str] = []
    tex_files: list[str] = []  # 顶层 .tex 文件（入口的候选）
    in_use: bool = False  # project.yaml 的 template 是否指向这个版本
    check: dict | None = None  # 论文阶段写的 check.json（TemplateCheck），还没检查时为 null


class TemplateList(BaseModel):
    current: dict | None  # project.yaml 的 template：{id, version}；null 表示默认模板
    templates: list[TemplateInfo]
    default_check: dict | None = None  # 默认模板的检查结果（paper/.template_check_default.json）
