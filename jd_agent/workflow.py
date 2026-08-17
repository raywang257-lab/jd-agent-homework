"""核心工作流 -- 需求抽取、JD 生成、风险检查、质量诊断及智能增强"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from .schemas import (
    ComparisonItem,
    ContentIssue,
    FieldIssue,
    JDContent,
    JobInput,
    KeywordInfo,
    QualityScore,
    RiskAssessment,
    RiskIssue,
    SalaryBenchmark,
    SkillGapItem,
    SmartSuggestion,
    SmartTip,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PLATFORM_OPTIONS = ["BOSS直聘", "智联招聘", "前程无忧", "拉勾", "猎聘", "脉脉"]

PLATFORM_DESCRIPTIONS: dict[str, str] = {
    "BOSS直聘": "直聊模式，适合快速触达候选人；标题和薪资是点击率核心。",
    "智联招聘": "传统综合平台，适合中高端岗位；正文结构化程度要求高。",
    "前程无忧": "覆盖面广，适合批量招聘；关键词匹配影响搜索曝光。",
    "拉勾": "互联网垂直平台，技术岗效果佳；福利和成长空间是亮点。",
    "猎聘": "中高端人才为主，适合管理岗和稀缺岗；职级和薪资需明确。",
    "脉脉": "职场社交推荐，适合内推和被动候选人；亮点和成长性更关键。",
}

FIELD_QUESTIONS: dict[str, str] = {
    "job_title": "岗位的具体名称是什么？（例如：高级 AI 产品经理）",
    "department": "这个岗位属于哪个部门或团队？",
    "location": "工作地点在哪个城市？是否支持远程？",
    "work_mode": "工作方式是现场办公、混合办公还是远程办公？",
    "seniority": "职级范围是什么？（例如：P7/P8、高级/资深/专家）",
    "experience": "需要几年相关经验？",
    "education": "学历要求是什么？",
    "salary": "薪资范围是多少？包含几薪？",
    "job_goal": "这个岗位要解决什么核心问题？衡量成功的指标是什么？",
    "responsibilities": "主要工作职责有哪些？",
    "required_skills": "必备的能力和技术有哪些？",
    "preferred_skills": "有哪些加分项？",
    "selling_points": "这个岗位和团队最吸引人的点是什么？",
    "platform": "主要在哪个招聘平台发布？",
}

REQUIRED_FIELDS = ["job_title", "location", "job_goal", "responsibilities", "required_skills"]
RECOMMENDED_FIELDS = ["department", "work_mode", "seniority", "experience", "education", "salary", "preferred_skills", "selling_points"]

# 模糊表述关键词
VAGUE_PHRASES = [
    "熟悉", "了解", "良好", "较强", "优秀", "相关", "等", "之类",
    "一定的", "基本的", "深入理解", "熟练掌握",
]

# 不可验证表述
UNVERIFIABLE_PHRASES = [
    "具有良好沟通能力", "团队合作精神", "责任心强", "抗压能力强",
    "积极主动", "自我驱动", "学习能力强", "结果导向",
]

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def content_hash(text: str) -> str:
    """计算文本内容哈希"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def platform_description(platform: str) -> str:
    return PLATFORM_DESCRIPTIONS.get(platform, "通用招聘平台")


def _split_items(text: str) -> list[str]:
    """将多行/分号/编号文本拆分为列表"""
    if not text:
        return []
    lines = re.split(r"[\n;；]+", text)
    items = []
    for line in lines:
        cleaned = re.sub(r"^[\d一二三四五六七八九十]+[.、)]\s*", "", line.strip())
        cleaned = re.sub(r"^[•·\-\*]\s*", "", cleaned)
        if cleaned:
            items.append(cleaned)
    return items


def _clean_intake_item(text: str) -> str:
    """清理原始 JD 中的 Markdown/编号，但不增加新事实。"""
    cleaned = re.sub(r"^\s*(?:[-*•·]|\d+[.、)]|[一二三四五六七八九十]+[、.)])\s*", "", text)
    return cleaned.strip().rstrip("；;。")


def _markdown_sections(source: str) -> tuple[str, dict[str, list[str]]]:
    """从常见 Markdown JD 中提取标题和分区条目。"""
    section_aliases = {
        "responsibilities": ("岗位职责", "职位职责", "主要职责", "工作职责", "工作内容"),
        "required_skills": ("任职要求", "岗位要求", "职位要求", "必备能力", "任职资格"),
        "preferred_skills": ("加分项", "优先条件", "加分能力"),
        "selling_points": ("福利待遇", "岗位亮点", "团队亮点", "我们提供", "薪酬福利"),
    }
    title = ""
    current = ""
    sections: dict[str, list[str]] = {key: [] for key in section_aliases}

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            heading_text = heading.group(1).strip()
            if not title:
                candidate = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", heading_text).strip()
                if candidate and not any(alias in candidate for aliases in section_aliases.values() for alias in aliases):
                    title = candidate
            current = ""
            for field, aliases in section_aliases.items():
                if any(alias in heading_text for alias in aliases):
                    current = field
                    break
            continue
        if current:
            item = _clean_intake_item(line)
            if item:
                sections[current].append(item)

    return title, sections


def auto_polish_job(job: JobInput, style: str = "专业清晰") -> tuple[JobInput, set[str]]:
    """保守润色事实层：仅清理格式、去重并统一少量等义表达。"""
    data = job.model_dump()
    changed: set[str] = set()
    list_fields = ("responsibilities", "required_skills", "preferred_skills", "selling_points")

    for field in list_fields:
        original = str(data.get(field, ""))
        seen: set[str] = set()
        polished_items: list[str] = []
        for raw_item in re.split(r"[\n；;]+", original):
            item = _clean_intake_item(raw_item)
            if not item:
                continue
            if field == "responsibilities":
                item = re.sub(r"^负责需求分析(?=[，,])", "开展需求分析", item)
                item = item.replace("算法、研发和业务团队", "算法、研发与业务团队")
            normalized = re.sub(r"\s+", "", item).rstrip("。")
            if normalized in seen:
                continue
            seen.add(normalized)
            polished_items.append(item)
        polished = "\n".join(polished_items)
        if polished != original.strip():
            data[field] = polished
            changed.add(field)

    polished_job = JobInput(**data)
    return polished_job, changed


def _schema() -> dict[str, Any]:
    """返回严格的 JDContent JSON Schema，供兼容网关和测试复用。"""
    schema = JDContent.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}))
    return schema


def explain_llm_failure(error: Exception, model: str) -> str:
    """将常见模型网关错误转换成可操作且不泄露密钥的提示。"""
    detail = str(error).lower()
    if "no_channel" in detail or "没有可用" in detail:
        return f"模型‘{model}’在当前网关没有可用通道；请使用网关 /models 返回的精确模型 ID。"
    if "401" in detail or "authentication" in detail or "invalid api key" in detail:
        return "模型密钥无效或已失效，请更新 LLM_API_KEY。"
    if "429" in detail or "rate limit" in detail:
        return "模型服务限流或额度不足，请稍后重试。"
    return f"模型‘{model}’调用失败，请检查模型 ID、网关地址和接口模式。"


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def _call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> tuple[str, str]:
    """调用 LLM，返回 (response_text, mode)。失败时抛出 ValueError。"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise ValueError("未配置 LLM_API_KEY")

    try:
        from openai import OpenAI
    except ImportError:
        raise ValueError("openai 包未安装")

    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    model = os.getenv("LLM_MODEL", "gpt-5-mini")
    api_mode = os.getenv("LLM_API_MODE", "chat").strip().lower()

    client = OpenAI(api_key=api_key, base_url=base_url)

    if api_mode == "responses":
        stream = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            max_output_tokens=max_tokens,
            stream=True,
        )
        parts = [
            event.delta
            for event in stream
            if getattr(event, "type", "") == "response.output_text.delta"
            and getattr(event, "delta", "")
        ]
    elif api_mode == "chat":
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
            stream=True,
        )
        parts = []
        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            content = getattr(chunk.choices[0].delta, "content", "")
            if content:
                parts.append(content)
    else:
        raise ValueError("LLM_API_MODE 只能是 chat 或 responses")

    text = "".join(parts).strip()
    if not text:
        raise ValueError("模型流式请求未返回文本内容")
    return text, f"llm:{model}"


# ---------------------------------------------------------------------------
# 需求抽取
# ---------------------------------------------------------------------------

def extract_job_input(
    source: str,
    existing: JobInput | None = None,
    polish_style: str = "专业清晰",
) -> tuple[JobInput, str]:
    """从原始文本抽取结构化岗位信息，返回 (JobInput, mode)"""
    source = source.strip()
    if not source:
        raise ValueError("请提供原始招聘材料")

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if api_key:
        try:
            extracted, mode = _extract_with_llm(source, existing, polish_style)
            polished, _ = auto_polish_job(extracted, polish_style)
            return polished, mode
        except Exception as exc:
            extracted = _extract_with_rules(source, existing)
            polished, _ = auto_polish_job(extracted, polish_style)
            return polished, f"fallback:{exc}"

    extracted = _extract_with_rules(source, existing)
    polished, _ = auto_polish_job(extracted, polish_style)
    return polished, "offline:rules"


def _extract_with_llm(
    source: str,
    existing: JobInput | None,
    polish_style: str = "专业清晰",
) -> tuple[JobInput, str]:
    """使用 LLM 抽取"""
    existing_json = existing.model_dump() if existing else {}

    system_prompt = (
        "你是招聘需求分析专家。从用户提供的原始招聘材料中抽取结构化岗位信息。"
        "只抽取原文明确提到的信息，不要臆测。如果原文没有提到某个字段，留空。"
        "返回 JSON 格式，字段包括：job_title, department, location, work_mode, "
        "seniority, experience, education, salary, job_goal, responsibilities, "
        "required_skills, preferred_skills, selling_points, platform。"
        "work_mode 取值：现场办公/混合办公/远程办公。"
        "responsibilities/required_skills/preferred_skills/selling_points 用换行分隔多条。"
        "如果提供了 existing 信息，在用户未提及新值时保留已有值。"
        f"按‘{polish_style}’风格整理，但只能改写原文已有事实，不得新增事实、条件或承诺。"
    )
    user_prompt = f"已有信息：{json.dumps(existing_json, ensure_ascii=False)}\n\n原始材料：\n{source}"

    text, mode = _call_llm(system_prompt, user_prompt)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    data = json.loads(text)
    job = JobInput(
        **{
            key: str(value).strip()
            for key, value in data.items()
            if key in JobInput.model_fields
        }
    )
    return job, mode


def _extract_with_rules(source: str, existing: JobInput | None) -> JobInput:
    """离线规则抽取"""
    data: dict[str, Any] = {}
    if existing:
        data = existing.model_dump()

    lines = source.split("\n")
    full_text = source

    markdown_title, markdown_sections = _markdown_sections(source)
    if markdown_title:
        data["job_title"] = markdown_title
    for field, items in markdown_sections.items():
        if items:
            data[field] = "\n".join(items)

    # 岗位名称
    title_match = re.search(r"(?:招|招聘|招募|寻找)\s*(?:一名|一个|一位)?\s*(.+?)(?:[，,。；;]|\s+负责|\s+要求|\s+岗位)", full_text)
    if title_match and not data.get("job_title"):
        data["job_title"] = title_match.group(1).strip()

    # 工作地点
    city_match = re.search(r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆|天津|青岛|大连|厦门|无锡|宁波|福州|合肥|济南|郑州|哈尔滨|沈阳|昆明|贵阳|南昌|太原|兰州|石家庄|呼和浩特|海口|银川|西宁|拉萨|乌鲁木齐|南宁|香港|澳门|台湾)", full_text)
    if city_match:
        data["location"] = city_match.group(1)

    # 工作方式
    if "现场办公" in full_text or "坐班" in full_text:
        data["work_mode"] = "现场办公"
    elif "混合办公" in full_text or "hybrid" in full_text.lower():
        data["work_mode"] = "混合办公"
    elif "远程办公" in full_text or "remote" in full_text.lower() or "居家" in full_text:
        data["work_mode"] = "远程办公"

    # 经验要求
    exp_range_match = re.search(r"(\d+)\s*[-–—~至到]\s*(\d+)\s*年", full_text)
    exp_match = re.search(r"(\d+)\s*年以上", full_text)
    if exp_range_match:
        data["experience"] = f"{exp_range_match.group(1)}-{exp_range_match.group(2)}年"
    elif exp_match:
        data["experience"] = f"{exp_match.group(1)}年以上"

    # 学历要求
    for edu in ["博士", "硕士", "研究生", "本科", "大专", "不限"]:
        if edu in full_text and ("学历" in full_text or "以上" in full_text or "毕业" in full_text):
            normalized_edu = edu if edu != "研究生" else "硕士"
            data["education"] = f"{normalized_edu}及以上" if f"{edu}及以上" in full_text else normalized_edu
            break

    # 薪资
    salary_match = re.search(r"(\d+[\-–到]\d+[Kk万])(?:[·]?(\d+)薪)?", full_text)
    if salary_match:
        salary = salary_match.group(0)
        data["salary"] = salary

    # 部门
    dept_match = re.search(r"(?:属于|隶属|所在)\s*(.+?)(?:部|组|中心|团队|部门)", full_text)
    if dept_match:
        data["department"] = dept_match.group(0).replace("属于", "").replace("隶属", "").replace("所在", "").strip()

    # 职责
    resp_match = re.search(r"(?:负责|工作内容|岗位职责|主要职责)[:：]?\s*(.+?)(?:任职要求|岗位要求|要求|加分|薪资|亮点|我们提供|$)", full_text, re.DOTALL)
    if resp_match and not data.get("responsibilities"):
        data["responsibilities"] = resp_match.group(1).strip()[:500]

    # 必备技能
    skills_match = re.search(r"(?:要求|任职要求|岗位要求|必备|需要|必须)[:：]?\s*(.+?)(?:加分|优先|薪资|亮点|我们提供|$)", full_text, re.DOTALL)
    if skills_match and not data.get("required_skills"):
        data["required_skills"] = skills_match.group(1).strip()[:500]

    # 加分项
    pref_match = re.search(r"(?:加分|优先|preferred|nice.?to.?have)[:：]?\s*(.+?)(?:薪资|亮点|我们提供|$)", full_text, re.DOTALL | re.IGNORECASE)
    if pref_match and not data.get("preferred_skills"):
        data["preferred_skills"] = pref_match.group(1).strip()[:300]

    # 亮点
    highlight_match = re.search(r"(?:亮点|吸引力|我们提供|福利待遇|优势)[:：]?\s*(.+?)(?:$)", full_text, re.DOTALL)
    if highlight_match and not data.get("selling_points"):
        data["selling_points"] = highlight_match.group(1).strip()[:300]

    # 岗位目标
    goal_match = re.search(r"(?:目标|职责目标|核心|主要)(?:是|为|包括)?\s*(.+?)(?:负责|要求|薪资|亮点|$)", full_text, re.DOTALL)
    if goal_match and not data.get("job_goal"):
        data["job_goal"] = goal_match.group(1).strip()[:200]

    return JobInput(**data)


def _demo_extract_job_input(raw_text: str, existing: JobInput | None = None) -> JobInput:
    """兼容旧调用名：使用当前离线规则完成事实抽取。"""
    return _extract_with_rules(raw_text, existing)


# ---------------------------------------------------------------------------
# 冲突检测
# ---------------------------------------------------------------------------

def detect_intake_conflicts(source: str) -> list[str]:
    """检测原始材料中的潜在冲突信息"""
    conflicts: list[str] = []

    # 薪资冲突
    salaries = re.findall(r"\d+[\-–到]\d+[Kk万](?:[·]?\d+薪)?", source)
    if len(set(salaries)) > 1:
        conflicts.append(f"原文出现多个薪资表述：{', '.join(set(salaries))}，请确认最终薪资。")

    # 地点冲突
    cities = re.findall(r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆)", source)
    if len(set(cities)) > 1:
        conflicts.append(f"原文出现多个工作地点：{', '.join(set(cities))}，请确认最终工作地点。")

    # 工作方式冲突
    modes = []
    if "现场" in source or "坐班" in source:
        modes.append("现场办公")
    if "混合" in source:
        modes.append("混合办公")
    if "远程" in source or "remote" in source.lower():
        modes.append("远程办公")
    if len(modes) > 1:
        conflicts.append(f"原文同时出现多种工作方式：{', '.join(modes)}，请确认最终工作方式。")

    # 经验冲突
    exps = re.findall(r"(\d+)\s*年以上", source)
    if len(set(exps)) > 1:
        conflicts.append(f"原文出现多个经验要求：{', '.join(set(exps))}年以上，请确认最终经验要求。")

    return conflicts


# ---------------------------------------------------------------------------
# JD 生成
# ---------------------------------------------------------------------------

def generate_jd(job: JobInput) -> tuple[JDContent, str]:
    """生成 JD 内容，返回 (JDContent, mode)"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if api_key:
        try:
            return _generate_jd_with_llm(job)
        except Exception as exc:
            return _generate_jd_offline(job), f"fallback:{exc}"

    return _generate_jd_offline(job), "offline:template"


def _generate_jd_with_llm(job: JobInput) -> tuple[JDContent, str]:
    """使用 LLM 生成 JD"""
    system_prompt = (
        "你是资深招聘文案专家。根据结构化岗位信息生成一份专业、清晰、有吸引力的 JD。"
        "要求：1) 职责和技能用列表形式；2) 语言专业但不生硬；"
        "3) 亮点要有具体吸引力；4) 不要添加未提供的信息。"
        "返回 JSON 格式的 JDContent，字段包括："
        "job_title, department, location, work_mode, seniority, experience, education, "
        "salary_and_benefits, job_goal, responsibilities(list), required_skills(list), "
        "preferred_skills(list), selling_points(list), platform。"
    )
    user_prompt = f"岗位信息：\n{json.dumps(job.model_dump(), ensure_ascii=False, indent=2)}"

    text, mode = _call_llm(system_prompt, user_prompt, max_tokens=3000)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    data = json.loads(text)
    jd = JDContent(**data)
    return jd, mode


def _generate_jd_offline(job: JobInput) -> JDContent:
    """离线模板生成 JD"""
    responsibilities = _split_items(job.responsibilities) if job.responsibilities else []
    if not responsibilities and job.job_goal:
        responsibilities = [f"负责{job.job_goal}"]

    required_skills = _split_items(job.required_skills) if job.required_skills else []
    preferred_skills = _split_items(job.preferred_skills) if job.preferred_skills else []
    selling_points = _split_items(job.selling_points) if job.selling_points else []

    salary_str = job.salary or "面议"

    return JDContent(
        job_title=job.job_title,
        department=job.department,
        location=job.location,
        work_mode=job.work_mode,
        seniority=job.seniority,
        experience=job.experience,
        education=job.education,
        salary_and_benefits=salary_str,
        job_goal=job.job_goal,
        responsibilities=responsibilities,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        selling_points=selling_points,
        platform=job.platform,
    )


def enforce_source_facts(job: JobInput, jd: JDContent) -> JDContent:
    """把生成内容中的关键事实恢复为 HR 已确认的 JobInput 值。"""
    return jd.model_copy(
        update={
            "job_title": job.job_title,
            "department": job.department,
            "location": job.location,
            "work_mode": job.work_mode,
            "seniority": job.seniority,
            "experience": job.experience,
            "education": job.education,
            "salary_and_benefits": job.salary or "面议",
            "selling_points": _split_items(job.selling_points),
            "platform": job.platform,
        }
    )


def render_jd(job: JobInput, jd: JDContent) -> str:
    """将 JDContent 渲染为 Markdown 文本"""
    lines: list[str] = []

    # 标题
    title = jd.job_title or job.job_title or "招聘岗位"
    lines.append(f"# {title}")
    lines.append("")

    # 基本信息
    info_parts: list[str] = []
    if jd.location:
        info_parts.append(jd.location)
    if jd.work_mode:
        info_parts.append(jd.work_mode)
    if jd.seniority:
        info_parts.append(jd.seniority)
    if jd.experience:
        info_parts.append(jd.experience)
    if jd.education:
        info_parts.append(jd.education)
    if info_parts:
        lines.append(" | ".join(info_parts))
        lines.append("")

    # 薪资
    if jd.salary_and_benefits:
        lines.append(f"**薪资福利：** {jd.salary_and_benefits}")
        lines.append("")

    # 部门
    if jd.department:
        lines.append(f"**所属部门：** {jd.department}")
        lines.append("")

    # 岗位目标
    if jd.job_goal:
        lines.append("## 岗位目标")
        lines.append(jd.job_goal)
        lines.append("")

    # 岗位职责
    if jd.responsibilities:
        lines.append("## 岗位职责")
        for item in jd.responsibilities:
            lines.append(f"- {item}")
        lines.append("")

    # 任职要求
    if jd.required_skills:
        lines.append("## 任职要求")
        for item in jd.required_skills:
            lines.append(f"- {item}")
        lines.append("")

    # 加分项
    if jd.preferred_skills:
        lines.append("## 加分项")
        for item in jd.preferred_skills:
            lines.append(f"- {item}")
        lines.append("")

    # 岗位亮点
    if jd.selling_points:
        lines.append("## 岗位亮点")
        for item in jd.selling_points:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 完整性检查
# ---------------------------------------------------------------------------

def inspect_completeness(job: JobInput) -> tuple[list[str], list[str], list[str]]:
    """检查字段完整性，返回 (missing_required, missing_recommended, filled_fields)"""
    missing_required = []
    missing_recommended = []
    filled = []

    for field in REQUIRED_FIELDS:
        value = getattr(job, field, "").strip()
        if not value:
            missing_required.append(field)
        else:
            filled.append(field)

    for field in RECOMMENDED_FIELDS:
        value = getattr(job, field, "").strip()
        if not value:
            missing_recommended.append(field)
        else:
            filled.append(field)

    return missing_required, missing_recommended, filled


def inspect_field_relevance(job: JobInput) -> list[FieldIssue]:
    """检查字段内容是否合理"""
    issues: list[FieldIssue] = []

    # 岗位名称不应包含部门或地点
    if job.job_title:
        for city in ["北京", "上海", "广州", "深圳", "杭州", "成都"]:
            if city in job.job_title:
                issues.append(FieldIssue(
                    label="岗位名称",
                    message=f"岗位名称中包含城市「{city}」，建议只保留具体职位名称。",
                ))
                break

    # 岗位名称不应太长
    if len(job.job_title) > 20:
        issues.append(FieldIssue(
            label="岗位名称",
            message="岗位名称过长，建议精简到 20 字以内。",
        ))

    # 薪资格式检查：兼容 30K-45K·14薪、30-45K、3万-5万 等常见写法。
    salary_pattern = re.compile(
        r"^\s*\d+(?:\.\d+)?\s*(?:[Kk]|千|万|元)?\s*"
        r"[-–—~到至]\s*\d+(?:\.\d+)?\s*(?:[Kk]|千|万|元)"
        r"(?:\s*[·x×*]?\s*\d+\s*薪)?\s*$"
    )
    if job.salary and not salary_pattern.fullmatch(job.salary) and job.salary.strip() != "面议":
        issues.append(FieldIssue(
            label="薪资范围",
            message="未识别为常见薪资范围，建议使用「30K-45K·14薪」或「面议」。",
        ))

    # 职责不应太短
    if job.responsibilities and len(job.responsibilities) < 20:
        issues.append(FieldIssue(
            label="主要职责",
            message="主要职责描述过短，建议补充具体工作内容。",
        ))

    # 必备技能不应太短
    if job.required_skills and len(job.required_skills) < 10:
        issues.append(FieldIssue(
            label="必备能力",
            message="必备能力描述过短，建议列出具体技能。",
        ))

    return issues


# ---------------------------------------------------------------------------
# 追问问题优先级
# ---------------------------------------------------------------------------

def prioritise_follow_up_questions(job: JobInput, limit: int = 4) -> list[str]:
    """根据缺失程度优先排序追问问题"""
    missing_required, missing_recommended, _ = inspect_completeness(job)

    priority_order = REQUIRED_FIELDS + RECOMMENDED_FIELDS
    all_missing = missing_required + [f for f in missing_recommended if f not in missing_required]

    sorted_missing = sorted(all_missing, key=lambda f: priority_order.index(f) if f in priority_order else 999)

    questions = []
    for field in sorted_missing[:limit]:
        question = FIELD_QUESTIONS.get(field, f"请补充{field}信息。")
        questions.append(question)

    return questions


# ---------------------------------------------------------------------------
# 岗位目标建议
# ---------------------------------------------------------------------------

def suggest_job_goal(source: str) -> str:
    """根据原始材料推断可能的岗位目标"""
    if not source:
        return ""

    # 从原文中提取可能的岗位目标描述
    goal_patterns = [
        r"(?:负责|主导|推动|承担)\s*(.+?)(?:[，,。；;]|\n)",
        r"(?:目标|目的|使命)(?:是|为)?\s*(.+?)(?:[，,。；;]|\n)",
    ]

    for pattern in goal_patterns:
        match = re.search(pattern, source)
        if match:
            goal = match.group(1).strip()
            if 5 <= len(goal) <= 100:
                return goal

    return ""


# ---------------------------------------------------------------------------
# 风险评估
# ---------------------------------------------------------------------------

def assess_risks(job: JobInput, jd_text: str) -> RiskAssessment:
    """规则化风险评估"""
    issues: list[RiskIssue] = []

    # 1. 薪资歧视风险
    if job.salary and "面议" not in job.salary:
        salary_nums = re.findall(r"\d+", job.salary)
        if len(salary_nums) >= 2:
            low, high = int(salary_nums[0]), int(salary_nums[1])
            if high > 0 and low / high < 0.5:
                issues.append(RiskIssue(
                    level="medium",
                    category="薪资结构",
                    text=f"薪资范围跨度较大（{job.salary}）",
                    reason="薪资下限与上限差距超过2倍，可能引起候选人期望偏差。",
                    suggestion="建议将薪资范围控制在合理区间内，或在 JD 中说明定薪依据。",
                ))

    # 2. 学历歧视风险
    jd_lower = jd_text.lower()
    if any(word in jd_lower for word in ["仅限", "只要", "必须985", "必须211", "只要985", "只要211"]):
        issues.append(RiskIssue(
            level="high",
            category="就业歧视",
            text="JD 中可能包含院校歧视表述",
            reason="限定特定院校可能违反就业平等相关法规。",
            suggestion="删除院校限制性表述，改为对能力的要求。",
        ))

    # 3. 性别/年龄歧视
    discrimination_patterns = [
        (r"限(?:男|女)性", "性别限制"),
        (r"\d{2}岁以下", "年龄限制"),
        (r"仅限(?:男|女)", "性别限制"),
    ]
    for pattern, category in discrimination_patterns:
        if re.search(pattern, jd_text):
            issues.append(RiskIssue(
                level="high",
                category="就业歧视",
                text=f"JD 中包含{category}表述",
                reason=f"{category}可能违反就业促进法相关规定。",
                suggestion="删除限制性表述，改为对所有符合条件的候选人开放。",
            ))

    # 4. 薪资与原文不一致
    if job.salary and job.salary != "面议" and job.salary not in jd_text:
        issues.append(RiskIssue(
            level="medium",
            category="信息一致性",
            text="JD 中的薪资与结构化信息不一致",
            reason="最终 JD 文本中的薪资与已确认的岗位薪资不匹配。",
            suggestion="确认最终薪资并同步到结构化信息和 JD 文本。",
        ))

    # 5. 缺少关键信息
    if not job.responsibilities.strip():
        issues.append(RiskIssue(
            level="high",
            category="信息缺失",
            text="岗位职责为空",
            reason="JD 中必须包含明确的岗位职责描述。",
            suggestion="补充岗位职责后再发布。",
        ))

    if not job.required_skills.strip():
        issues.append(RiskIssue(
            level="high",
            category="信息缺失",
            text="任职要求为空",
            reason="JD 中必须包含明确的任职要求。",
            suggestion="补充任职要求后再发布。",
        ))

    # 6. 联系方式泄露
    contact_patterns = [
        (r"1[3-9]\d{9}", "手机号码"),
        (r"[\w.+-]+@[\w-]+\.[\w.-]+", "邮箱地址"),
    ]
    for pattern, label in contact_patterns:
        if re.search(pattern, jd_text):
            issues.append(RiskIssue(
                level="medium",
                category="隐私安全",
                text=f"JD 中包含{label}",
                reason="公开发布的 JD 中不应包含个人联系方式。",
                suggestion="删除联系方式，通过平台内置的沟通渠道联系候选人。",
            ))

    # 7. 福利承诺风险
    welfare_keywords = ["保证", "承诺", "一定", "必定", "100%", "必定涨薪"]
    for keyword in welfare_keywords:
        if keyword in jd_text:
            issues.append(RiskIssue(
                level="low",
                category="合规风险",
                text=f"JD 中包含绝对化表述「{keyword}」",
                reason="绝对化福利承诺可能引发劳动纠纷。",
                suggestion="将绝对化表述改为合理描述，如「提供有竞争力的薪酬」。",
            ))
            break

    # 计算总体风险
    if any(issue.level == "high" for issue in issues):
        overall = "高"
    elif any(issue.level == "medium" for issue in issues):
        overall = "中"
    else:
        overall = "低"

    return RiskAssessment(issues=issues, overall_level=overall)


def inspect_risks(text: str) -> list[RiskIssue]:
    """兼容只传文本的风险检查调用。"""
    placeholder = JobInput(responsibilities="待文本检查", required_skills="待文本检查")
    return assess_risks(placeholder, text).issues


# ---------------------------------------------------------------------------
# 薪资同步
# ---------------------------------------------------------------------------

def find_salary_update_candidate(job: JobInput, jd_text: str) -> str | None:
    """检测 JD 文本中的薪资是否与结构化信息不一致"""
    if not job.salary or job.salary == "面议":
        return None

    # 在 JD 文本中查找薪资模式
    salary_patterns = re.findall(r"\d+[\-–到]\d+[Kk万](?:[·]?\d+薪)?", jd_text)
    if not salary_patterns:
        return None

    for salary in salary_patterns:
        if salary != job.salary:
            return salary

    return None


def synchronise_confirmed_salary(jd_text: str, old_salary: str, new_salary: str) -> str:
    """将 JD 文本中的旧薪资替换为新薪资"""
    if old_salary and old_salary in jd_text:
        return jd_text.replace(old_salary, new_salary, 1)
    return jd_text


# ---------------------------------------------------------------------------
# 内容质量诊断
# ---------------------------------------------------------------------------

def diagnose_content_quality(job: JobInput) -> list[ContentIssue]:
    """诊断内容质量问题"""
    issues: list[ContentIssue] = []
    counter = 0

    fields_to_check = [
        ("responsibilities", "岗位职责"),
        ("required_skills", "必备能力"),
        ("preferred_skills", "加分能力"),
        ("selling_points", "岗位亮点"),
    ]

    for field, label in fields_to_check:
        text = getattr(job, field, "").strip()
        if not text:
            continue

        items = _split_items(text)

        for item in items:
            # 检查模糊表述
            vague_found = [p for p in VAGUE_PHRASES if p in item]
            if vague_found and len(item) < 30:
                counter += 1
                issues.append(ContentIssue(
                    issue_id=f"issue_{counter}",
                    field=field,
                    issue_type="表述空泛",
                    severity="medium",
                    original_text=item,
                    reason=f"包含模糊表述：{'、'.join(vague_found)}，缺乏可衡量标准。",
                    follow_up_question=f"「{label}」中「{item[:20]}...」的具体衡量标准是什么？",
                ))

            # 检查不可验证表述
            unverifiable_found = [p for p in UNVERIFIABLE_PHRASES if p in item]
            if unverifiable_found:
                counter += 1
                issues.append(ContentIssue(
                    issue_id=f"issue_{counter}",
                    field=field,
                    issue_type="不可验证",
                    severity="medium",
                    original_text=item,
                    safe_rewrite=f"具备跨团队协作经验，能够在多方利益相关者之间推动共识达成",
                    reason=f"「{'、'.join(unverifiable_found)}」无法在面试中有效验证。",
                    follow_up_question=f"候选人如何证明具备{'、'.join(unverifiable_found)}？",
                ))

            # 检查缺少成果标准
            if field == "responsibilities" and not any(word in item for word in ["确保", "达成", "提升", "降低", "实现", "完成", "负责", "推动", "建立", "优化"]):
                counter += 1
                issues.append(ContentIssue(
                    issue_id=f"issue_{counter}",
                    field=field,
                    issue_type="缺少预期产出",
                    severity="low",
                    original_text=item,
                    reason="职责描述缺少可衡量的预期产出或成果标准。",
                    follow_up_question=f"「{item[:30]}...」的预期成果或衡量标准是什么？",
                ))

            # 检查职责与要求混淆
            if field == "responsibilities" and any(word in item for word in ["具备", "拥有", "熟练", "精通", "熟悉", "了解"]):
                counter += 1
                issues.append(ContentIssue(
                    issue_id=f"issue_{counter}",
                    field=field,
                    issue_type="职责与要求混淆",
                    severity="medium",
                    original_text=item,
                    safe_rewrite=item.replace("具备", "运用").replace("拥有", "基于").replace("熟练", "利用").replace("精通", "运用").replace("熟悉", "基于").replace("了解", "参考"),
                    reason='岗位职责中混入了任职要求的表述，应聚焦于「做什么」而非「需要什么」。',
                    follow_up_question=f"「{item[:30]}...」具体要做什么工作？",
                ))

    return issues


def diagnose_requirement_quality(job: JobInput) -> list[str]:
    """返回需要 HR 补充事实的质量追问，安全改写项不进入追问。"""
    return [
        issue.follow_up_question
        for issue in diagnose_content_quality(job)
        if issue.follow_up_question and not issue.safe_rewrite
    ]


# ===========================================================================
# 新增智能功能
# ===========================================================================

def calculate_quality_score(
    job: JobInput,
    jd_text: str,
    assessment: RiskAssessment,
    content_issues: list[ContentIssue],
    decisions: dict[str, Any],
) -> QualityScore:
    """计算 JD 质量评分（0-100）"""
    # 1. 完整性（30分）
    missing_required, missing_recommended, filled = inspect_completeness(job)
    total_fields = len(REQUIRED_FIELDS) + len(RECOMMENDED_FIELDS)
    filled_count = len(filled)
    completeness = int(30 * filled_count / total_fields)

    # 2. 具体性（25分）-- 基于内容长度和具体度
    specificity = 0
    if job.responsibilities:
        specificity += min(8, len(job.responsibilities) // 50)
    if job.required_skills:
        specificity += min(7, len(job.required_skills) // 40)
    if job.job_goal and len(job.job_goal) > 20:
        specificity += 5
    if job.selling_points:
        specificity += min(5, len(job.selling_points) // 30)
    specificity = min(25, specificity)

    # 3. 风险（20分）-- 风险越低分越高
    if assessment.overall_level == "低":
        risk = 20
    elif assessment.overall_level == "中":
        risk = 10
    else:
        risk = 0

    # 4. 内容质量（15分）-- 问题越少分越高
    total_issues = len(content_issues)
    resolved_issues = len(decisions)
    unresolved = total_issues - resolved_issues
    quality = max(0, 15 - unresolved * 3)

    # 5. 优化处理（10分）-- 已处理的问题占比
    if total_issues > 0:
        optimization = int(10 * resolved_issues / total_issues)
    else:
        optimization = 10

    score = completeness + specificity + risk + quality + optimization

    return QualityScore(
        score=score,
        completeness=completeness,
        specificity=specificity,
        risk=risk,
        quality=quality,
        optimization=optimization,
        breakdown={
            "filled_fields": filled_count,
            "total_fields": total_fields,
            "missing_required": len(missing_required),
            "missing_recommended": len(missing_recommended),
            "total_issues": total_issues,
            "resolved_issues": resolved_issues,
            "unresolved_issues": unresolved,
            "risk_level": assessment.overall_level,
            "jd_length": len(jd_text),
        },
    )


def suggest_field_values(job_title: str) -> list[SmartSuggestion]:
    """根据岗位名称智能推荐字段值"""
    suggestions: list[SmartSuggestion] = []
    title_lower = job_title.lower()

    # 岗位类别匹配
    role_patterns: list[tuple[str, list[dict[str, str]]]] = [
        (
            r"(ai|人工智能|大模型|llm|agent|智能)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "具备 RAG / Agent 工作流设计经验\n理解 LLM 能力边界与评估方法\n能定义 AI 产品的效果指标", "reason": "AI 岗位核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有模型评估或 Agent 产品上线经验\n了解 Prompt Engineering 最佳实践\n熟悉 LangChain / LlamaIndex 等框架", "reason": "AI 岗位差异化优势"},
                {"field": "selling_points", "label": "岗位亮点", "value": "参与核心 AI 产品从0到1建设\n直接对接算法团队，技术深度高\nAI 应用落地经验可复用", "reason": "AI 岗位吸引点"},
            ],
        ),
        (
            r"(产品经理|产品总监|product)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "需求分析与 PRD 撰写\n跨团队协调与项目推进\n数据分析与用户洞察", "reason": "产品经理核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有 B 端 SaaS 产品经验\n具备技术背景，能与研发深度沟通\n有产品 0-1 经验", "reason": "产品经理差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "主导核心产品方向\n直接向业务负责人汇报\n产品决策权与资源支持", "reason": "产品岗吸引力"},
            ],
        ),
        (
            r"(前端|frontend|web开发|react|vue)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "精通 JavaScript / TypeScript\n熟练使用 React 或 Vue 框架\n掌握 HTML5 / CSS3 / 响应式布局", "reason": "前端核心技能"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有 SSR / Next.js / Nuxt.js 经验\n了解 Webpack / Vite 构建工具\n有前端性能优化经验", "reason": "前端进阶能力"},
                {"field": "selling_points", "label": "岗位亮点", "value": "前端技术栈自由度高\n参与从架构设计到上线的全流程\n团队注重技术分享与成长", "reason": "前端岗吸引点"},
            ],
        ),
        (
            r"(后端|backend|java|python|go|golang|服务端)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "精通至少一门后端语言（Java/Go/Python）\n熟悉微服务架构与分布式系统\n掌握 MySQL / Redis 等存储中间件", "reason": "后端核心技能"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有高并发系统设计经验\n熟悉 Kafka / RabbitMQ 等消息中间件\n了解 K8s / Docker 容器化部署", "reason": "后端进阶能力"},
                {"field": "selling_points", "label": "岗位亮点", "value": "处理亿级流量核心系统\n技术驱动型团队文化\n完善的导师制与技术成长路径", "reason": "后端岗吸引点"},
            ],
        ),
        (
            r"(数据|data|算法|algorithm|机器学习|ml|深度学习|dl)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "扎实的数学与统计基础\n精通 Python 及数据科学生态（Pandas/NumPy/Scikit-learn）\n具备模型训练与评估经验", "reason": "数据/算法核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有深度学习框架经验（PyTorch/TensorFlow）\n了解大模型微调与部署\n有 Kaggle 或顶会论文经历", "reason": "算法岗差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "海量真实业务数据\n充足 GPU 算力资源\n研究成果可直接落地产生业务价值", "reason": "算法岗吸引点"},
            ],
        ),
        (
            r"(设计|design|ui|ux|交互|视觉)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "精通 Figma / Sketch 等设计工具\n具备用户研究与交互设计能力\n理解设计系统与组件化思维", "reason": "设计岗核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有动效设计经验\n了解前端实现原理\n有 B 端复杂产品设计经验", "reason": "设计岗差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "设计话语权充分\n参与产品设计全流程\n团队重视设计价值与用户体验", "reason": "设计岗吸引点"},
            ],
        ),
        (
            r"(测试|qa|quality|质量)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "掌握自动化测试框架（Selenium/Pytest/Jest）\n熟悉接口测试与性能测试\n理解 CI/CD 流程", "reason": "测试核心技能"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有测试平台开发经验\n了解安全测试\n有代码审查与白盒测试能力", "reason": "测试进阶能力"},
                {"field": "selling_points", "label": "岗位亮点", "value": "质量保障体系完善\n测试工具链先进\n参与质量标准制定", "reason": "测试岗吸引点"},
            ],
        ),
        (
            r"(运营|operation|增长|growth)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "数据分析与 SQL 查询能力\n用户增长方法论与 A/B 测试经验\n内容策划与活动运营能力", "reason": "运营核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有私域运营经验\n了解 SEO/SEM\n有跨部门项目管理经验", "reason": "运营差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "直接对增长指标负责\n数据驱动决策文化\n丰富的用户触达渠道", "reason": "运营岗吸引点"},
            ],
        ),
    ]

    for pattern, fields in role_patterns:
        if re.search(pattern, title_lower):
            for field_data in fields:
                suggestions.append(SmartSuggestion(
                    field=field_data["field"],
                    label=field_data["label"],
                    value=field_data["value"],
                    confidence=0.8,
                    reason=field_data["reason"],
                ))
            break

    # 通用建议
    if not suggestions:
        suggestions.append(SmartSuggestion(
            field="selling_points",
            label="岗位亮点",
            value="核心业务直接参与\n清晰的职业发展路径\n有竞争力的薪酬福利",
            confidence=0.5,
            reason="通用岗位亮点模板",
        ))

    return suggestions


def extract_keywords(text: str) -> list[KeywordInfo]:
    """从文本中提取关键词"""
    if not text:
        return []

    keywords: dict[str, KeywordInfo] = {}

    # 技术关键词
    tech_keywords = [
        "Python", "Java", "Go", "JavaScript", "TypeScript", "Rust", "C++",
        "React", "Vue", "Angular", "Next.js", "Node.js",
        "MySQL", "Redis", "MongoDB", "PostgreSQL", "Elasticsearch",
        "Kafka", "RabbitMQ", "Docker", "Kubernetes", "K8s",
        "AWS", "Azure", "GCP", "阿里云", "腾讯云",
        "PyTorch", "TensorFlow", "LangChain", "LlamaIndex",
        "RAG", "Agent", "LLM", "NLP", "CV", "机器学习", "深度学习",
        "微服务", "分布式", "高并发", "CI/CD",
        "Figma", "Sketch", "SQL", "NoSQL",
        "PRD", "用户研究", "数据分析", "A/B测试",
        "项目管理", "敏捷开发", "Scrum",
    ]

    for kw in tech_keywords:
        count = text.lower().count(kw.lower())
        if count > 0:
            category = "skill"
            if kw in ["PRD", "用户研究", "数据分析", "A/B测试", "项目管理", "敏捷开发", "Scrum"]:
                category = "requirement"
            keywords[kw] = KeywordInfo(keyword=kw, category=category, frequency=count)

    # 城市关键词
    cities = ["北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "苏州", "长沙", "重庆"]
    for city in cities:
        if city in text:
            keywords[city] = KeywordInfo(keyword=city, category="location", frequency=text.count(city))

    # 学历关键词
    for edu in ["博士", "硕士", "本科", "大专"]:
        if edu in text:
            keywords[edu] = KeywordInfo(keyword=edu, category="education", frequency=text.count(edu))

    # 薪资关键词
    salary_matches = re.findall(r"\d+[Kk万]", text)
    for s in salary_matches:
        if s not in keywords:
            keywords[s] = KeywordInfo(keyword=s, category="salary", frequency=1)

    return sorted(keywords.values(), key=lambda x: x.frequency, reverse=True)


def analyze_skill_gaps(job: JobInput) -> list[SkillGapItem]:
    """分析技能缺口"""
    # 典型岗位技能库
    role_skill_map: dict[str, list[tuple[str, str, str]]] = {
        "ai": [
            ("RAG", "技术", "high", "检索增强生成是 AI 应用的核心模式"),
            ("LLM", "技术", "high", "大语言模型理解是 AI 产品的基础"),
            ("Prompt Engineering", "技术", "medium", "提示词工程影响模型效果"),
            ("模型评估", "方法", "high", "效果评估是 AI 产品的关键环节"),
            ("Agent", "技术", "medium", "Agent 是 AI 应用的主流方向"),
        ],
        "product": [
            ("PRD", "方法", "high", "产品需求文档是产品经理的核心产出"),
            ("用户研究", "方法", "high", "用户洞察驱动产品决策"),
            ("数据分析", "方法", "high", "数据驱动是现代产品管理的基础"),
            ("A/B测试", "方法", "medium", "实验思维验证产品假设"),
            ("项目管理", "方法", "medium", "跨团队协作需要项目管理能力"),
        ],
        "frontend": [
            ("JavaScript", "技术", "high", "前端开发的基础语言"),
            ("TypeScript", "技术", "high", "现代前端项目的标配"),
            ("React", "技术", "high", "主流前端框架"),
            ("CSS", "技术", "high", "页面样式与响应式设计基础"),
            ("性能优化", "方法", "medium", "前端性能影响用户体验"),
        ],
        "backend": [
            ("MySQL", "技术", "high", "关系型数据库是后端基础"),
            ("Redis", "技术", "high", "缓存是高并发系统的关键"),
            ("微服务", "架构", "high", "现代后端系统主流架构"),
            ("Docker", "技术", "medium", "容器化是部署的标准实践"),
            ("消息队列", "技术", "medium", "异步解耦的关键组件"),
        ],
        "data": [
            ("Python", "技术", "high", "数据科学的主力语言"),
            ("SQL", "技术", "high", "数据查询的基础技能"),
            ("机器学习", "技术", "high", "数据科学的核心能力"),
            ("Pandas", "技术", "medium", "数据处理的标准工具"),
            ("数据可视化", "方法", "medium", "数据表达的重要能力"),
        ],
    }

    title_lower = (job.job_title or "").lower()
    all_text = f"{job.job_title} {job.responsibilities} {job.required_skills} {job.preferred_skills}".lower()

    # 确定岗位类别
    role_key = None
    if re.search(r"(ai|人工智能|大模型|llm|agent|智能)", title_lower):
        role_key = "ai"
    elif re.search(r"(产品经理|product)", title_lower):
        role_key = "product"
    elif re.search(r"(前端|frontend|react|vue)", title_lower):
        role_key = "frontend"
    elif re.search(r"(后端|backend|java|python|go|服务端)", title_lower):
        role_key = "backend"
    elif re.search(r"(数据|data|算法|机器学习)", title_lower):
        role_key = "data"

    if not role_key:
        return []

    gaps: list[SkillGapItem] = []
    for skill, category, importance, note in role_skill_map.get(role_key, []):
        in_jd = skill.lower() in all_text
        if not in_jd:
            gaps.append(SkillGapItem(
                skill=skill,
                category=category,
                in_jd=False,
                importance=importance,
                note=note,
            ))

    return sorted(gaps, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.importance])


def salary_benchmark(job_title: str, location: str, experience: str) -> SalaryBenchmark:
    """薪资基准建议"""
    # 简化版薪资基准表
    city_multiplier = {
        "北京": 1.15, "上海": 1.15, "深圳": 1.10, "杭州": 1.05,
        "广州": 0.95, "成都": 0.75, "南京": 0.85, "武汉": 0.70,
        "西安": 0.70, "苏州": 0.85, "长沙": 0.65, "重庆": 0.70,
    }

    base_ranges: list[tuple[str, tuple[int, int]]] = [
        (r"(ai|人工智能|大模型|算法)", (35, 60)),
        (r"(产品经理|product)", (25, 45)),
        (r"(前端|backend|后端|frontend)", (20, 40)),
        (r"(数据|data)", (25, 50)),
        (r"(设计|design|ui|ux)", (18, 35)),
        (r"(测试|qa)", (15, 30)),
        (r"(运营|operation)", (12, 28)),
    ]

    title_lower = (job_title or "").lower()
    base_low, base_high = 15, 30

    for pattern, (low, high) in base_ranges:
        if re.search(pattern, title_lower):
            base_low, base_high = low, high
            break

    # 经验调整
    exp_match = re.search(r"(\d+)", experience or "")
    if exp_match:
        years = int(exp_match.group(1))
        if years >= 5:
            base_low = int(base_low * 1.4)
            base_high = int(base_high * 1.4)
        elif years >= 3:
            base_low = int(base_low * 1.15)
            base_high = int(base_high * 1.15)
        elif years <= 1:
            base_low = int(base_low * 0.7)
            base_high = int(base_high * 0.7)

    # 城市调整
    mult = city_multiplier.get(location, 0.80)
    base_low = int(base_low * mult)
    base_high = int(base_high * mult)

    suggested = f"{base_low}K-{base_high}K·14薪"

    confidence = "medium"
    if location and experience and job_title:
        confidence = "high"
    elif location or experience:
        confidence = "medium"
    else:
        confidence = "low"

    return SalaryBenchmark(
        job_title=job_title or "未知岗位",
        location=location or "未指定",
        suggested_range=suggested,
        confidence=confidence,
        source="行业经验基准",
        notes="此为参考范围，实际薪资应根据公司情况、候选人能力等因素调整。",
    )


def generate_smart_tips(
    job: JobInput,
    jd_text: str,
    assessment: RiskAssessment,
    quality_score: QualityScore | None = None,
) -> list[SmartTip]:
    """生成上下文感知的智能提示"""
    tips: list[SmartTip] = []
    tip_counter = 0

    missing_required, missing_recommended, _ = inspect_completeness(job)

    # 必填字段缺失
    if missing_required:
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="danger",
            title="必填信息不完整",
            content=f"还缺少 {len(missing_required)} 个必填字段：{'、'.join(missing_required)}。这些信息是生成 JD 的前提。",
            action="请补充缺失字段后继续",
        ))

    # 推荐字段缺失
    if missing_recommended and len(missing_recommended) > 3:
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="warning",
            title="建议补充更多信息",
            content=f"有 {len(missing_recommended)} 个推荐字段未填写，补充后可显著提升 JD 质量。",
        ))

    # 风险提示
    if assessment.overall_level == "高":
        tip_counter += 1
        high_issues = [i for i in assessment.issues if i.level == "high"]
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="danger",
            title="存在发布阻断风险",
            content=f"检测到 {len(high_issues)} 个高风险项，必须修改后才能发布。",
        ))
    elif assessment.overall_level == "中":
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="warning",
            title="存在需要核实的内容",
            content="有中等风险项需要人工核实后再发布。",
        ))

    # 质量评分提示
    if quality_score:
        if quality_score.score < 50:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="warning",
                title=f"JD 质量评分较低（{quality_score.score}/100）",
                content=f"完整度 {quality_score.completeness}/30，具体性 {quality_score.specificity}/25。建议补充更多细节。",
            ))
        elif quality_score.score >= 80:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="success",
                title=f"JD 质量优秀（{quality_score.score}/100）",
                content="当前 JD 质量较高，可以进行审批发布。",
            ))

    # 技能缺口提示
    gaps = analyze_skill_gaps(job)
    high_gaps = [g for g in gaps if g.importance == "high"]
    if high_gaps:
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="info",
            title="检测到潜在技能缺口",
            content=f"同类岗位通常还要求：{'、'.join(g.skill for g in high_gaps[:3])}。确认是否需要补充。",
        ))

    # 薪资建议
    if not job.salary and job.job_title:
        benchmark = salary_benchmark(job.job_title, job.location, job.experience)
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="info",
            title="薪资参考建议",
            content=f"根据岗位和地点，建议薪资范围：{benchmark.suggested_range}（{benchmark.confidence}置信度）",
        ))

    # JD 长度检查
    if jd_text:
        if len(jd_text) < 200:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="warning",
                title="JD 内容偏短",
                content="JD 正文较短，可能影响候选人对岗位的理解。建议补充更多细节。",
            ))
        elif len(jd_text) > 3000:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="info",
                title="JD 内容较长",
                content="JD 正文较长，在部分平台可能影响阅读体验。建议精简非核心内容。",
            ))

    return tips


def compare_requirement_vs_jd(
    original_text: str,
    job: JobInput,
    jd_text: str,
) -> list[ComparisonItem]:
    """对比原始需求与生成的 JD"""
    items: list[ComparisonItem] = []

    field_labels = {
        "job_title": "岗位名称",
        "location": "工作地点",
        "work_mode": "工作方式",
        "experience": "经验要求",
        "education": "学历要求",
        "salary": "薪资范围",
        "department": "所属部门",
    }

    for field, label in field_labels.items():
        original_val = ""
        generated_val = getattr(job, field, "")
        match = True

        if original_text:
            if field == "job_title":
                m = re.search(r"(?:招|招聘|招募)\s*(?:一名|一个|一位)?\s*(.+?)(?:[，,。；;]|\s+负责|\s+要求)", original_text)
                if m:
                    original_val = m.group(1).strip()
            elif field == "location":
                m = re.search(r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆|天津)", original_text)
                if m:
                    original_val = m.group(1)
            elif field == "salary":
                m = re.search(r"\d+[\-–到]\d+[Kk万](?:[·]?\d+薪)?", original_text)
                if m:
                    original_val = m.group(0)
            elif field == "experience":
                m = re.search(r"(\d+)\s*年以上", original_text)
                if m:
                    original_val = f"{m.group(1)}年以上"

        match = original_val.lower() == generated_val.lower() if original_val and generated_val else True

        items.append(ComparisonItem(
            field=field,
            label=label,
            original=original_val or "(未提取)",
            generated=generated_val or "(未填写)",
            match=match,
        ))

    return items
