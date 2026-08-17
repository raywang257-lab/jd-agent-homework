"""智能增强功能 -- 批量处理、模板渲染、统计聚合"""

from __future__ import annotations

import re
from typing import Any

from .schemas import JobInput
from .workflow import (
    RECOMMENDED_FIELDS,
    REQUIRED_FIELDS,
    assess_risks,
    calculate_quality_score,
    diagnose_content_quality,
    extract_keywords,
    inspect_completeness,
    platform_description,
    render_jd,
)


def batch_split_requirements(raw_text: str) -> list[str]:
    """将批量需求文本拆分为单个岗位需求"""
    if not raw_text.strip():
        return []

    # 尝试按数字编号分割
    numbered = re.split(r"\n\s*(?:岗位|职位|需求)\s*[一二三四五六七八九十\d]+[.、:：]\s*", raw_text)
    if len(numbered) > 1:
        return [item.strip() for item in numbered if item.strip()]

    # 尝试按分隔符分割
    if "---" in raw_text or "===" in raw_text:
        parts = re.split(r"\n[\-=]{3,}\n", raw_text)
        return [item.strip() for item in parts if item.strip()]

    # 尝试按空行分割
    blocks = re.split(r"\n\s*\n", raw_text)
    blocks = [b.strip() for b in blocks if b.strip() and len(b.strip()) > 20]
    if len(blocks) > 1:
        return blocks

    return [raw_text.strip()]


def render_quality_gauge(score: int) -> str:
    """生成质量评分的 HTML 仪表盘描述"""
    if score >= 80:
        level = "优秀"
        color = "#28a745"
    elif score >= 60:
        level = "良好"
        color = "#17a2b8"
    elif score >= 40:
        level = "一般"
        color = "#ffc107"
    else:
        level = "待改进"
        color = "#dc3545"

    return f"{level}"


def render_keyword_cloud(keywords: list[dict[str, Any]]) -> str:
    """生成关键词标签 HTML"""
    if not keywords:
        return "<p>暂无关键词</p>"

    category_colors = {
        "skill": "#4fc3f7",
        "requirement": "#ff8a65",
        "location": "#81c784",
        "education": "#ba68c8",
        "salary": "#ffd54f",
    }

    tags = []
    for kw in keywords[:30]:
        color = category_colors.get(kw.get("category", ""), "#bdbdbd")
        freq = kw.get("frequency", 1)
        font_size = min(22, 12 + freq * 2)
        tags.append(
            f'<span style="display:inline-block;margin:4px;padding:4px 12px;'
            f'border-radius:16px;background:{color};color:#fff;font-size:{font_size}px;'
            f'font-weight:600;">{kw["keyword"]}</span>'
        )
    return " ".join(tags)


def render_progress_bar(filled: int, total: int, label: str = "完成度") -> str:
    """生成进度条 HTML"""
    pct = int(filled / total * 100) if total > 0 else 0
    if pct >= 80:
        color = "#28a745"
    elif pct >= 50:
        color = "#17a2b8"
    else:
        color = "#ffc107"

    return (
        f'<div style="margin:8px 0;">'
        f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
        f'<span style="font-size:13px;color:#aaa;">{label}</span>'
        f'<span style="font-size:13px;color:#aaa;">{filled}/{total} ({pct}%)</span>'
        f'</div>'
        f'<div style="height:8px;background:#333;border-radius:4px;overflow:hidden;">'
        f'<div style="width:{pct}%;height:100%;background:{color};border-radius:4px;'
        f'transition:width 0.3s;"></div>'
        f'</div>'
        f'</div>'
    )


def get_completion_stats(job: JobInput) -> dict[str, Any]:
    """获取完成度统计"""
    missing_required, missing_recommended, _ = inspect_completeness(job)
    tracked_fields = [*REQUIRED_FIELDS, *RECOMMENDED_FIELDS]
    filled = [field for field in tracked_fields if getattr(job, field, "").strip()]
    total = len(missing_required) + len(missing_recommended) + len(filled)
    return {
        "filled": len(filled),
        "total": total,
        "missing_required": len(missing_required),
        "missing_recommended": len(missing_recommended),
        "percentage": int(len(filled) / total * 100) if total > 0 else 0,
        "filled_list": filled,
        "missing_required_list": missing_required,
        "missing_recommended_list": missing_recommended,
    }


def quick_quality_check(job: JobInput, jd_text: str = "") -> dict[str, Any]:
    """快速质量检查，用于实时反馈"""
    assessment = assess_risks(job, jd_text or " ")
    content_issues = diagnose_content_quality(job)
    quality_score = calculate_quality_score(job, jd_text or " ", assessment, content_issues, {})
    completion = get_completion_stats(job)

    return {
        "quality_score": quality_score,
        "risk_level": assessment.overall_level,
        "risk_count": len(assessment.issues),
        "content_issues_count": len(content_issues),
        "completion": completion,
    }
