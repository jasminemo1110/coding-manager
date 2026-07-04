"""LLM integration for daily change summaries.

走 OpenAI 兼容接口，服务商无关：DeepSeek / OpenAI / 通义千问都支持同一套 API，
只是 base_url 和模型名不同（在设置页配置）。默认 DeepSeek。
"""

import os

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

PROMPT = """你是一个帮我做"vibe coding"日志总结的助手。

下面是项目 "{project_name}" 今天的 git 改动（commit message + diff 片段）。请你用简洁自然的中文说清楚（改动不多时 200 字左右即可，迭代内容多的日子可以适当展开，但尽量控制在 500 字以内，务必把话说完整、不要中途截断）：
1. 今天这个项目改动了什么（出于什么目的、想达成什么效果）
2. 在尝试或讨论之后放弃了什么（出于什么原因；线索来自 revert、被删除的大段代码、commit 中的"撤销/恢复/不要"等表述）

要求：
- 用功能/动机的语言，不要罗列技术细节
- 如果今天没有"放弃"的内容，就只写改动部分
- 直接输出总结，不要前缀

--- COMMITS ---
{commits}

--- DIFF ---
{diff}
"""


DESCRIBE_PROMPT = """下面是项目 "{project_name}" 的一些信息（可能是 CLAUDE.md 内容、README，或近期的开发摘要）。

请用一句话（30 字以内的中文）概括这个项目是做什么的。直接输出这句话，不要任何前缀、引号或标点结尾。

--- 项目信息 ---
{context}
"""


def _resolve(cfg):
    """从 cfg（app 传入）或环境变量解析出 (api_key, base_url, model)。"""
    cfg = cfg or {}
    api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    base_url = cfg.get("base_url") or os.environ.get("AI_BASE_URL") or DEFAULT_BASE_URL
    model = cfg.get("model") or os.environ.get("AI_MODEL") or DEFAULT_MODEL
    return api_key, base_url, model


def _chat(cfg, prompt, max_tokens):
    api_key, base_url, model = _resolve(cfg)
    if not api_key:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return (resp.choices[0].message.content or "").strip()


def describe_project(project_name, context, cfg=None):
    if not context.strip():
        return None
    api_key, _, _ = _resolve(cfg)
    if not api_key:
        return None
    try:
        return _chat(
            cfg,
            DESCRIBE_PROMPT.format(project_name=project_name, context=context[:8000]),
            100,
        )
    except Exception as e:
        return f"[AI 生成失败：{e}]"


def summarize(project_name, commits, diff, cfg=None):
    api_key, _, _ = _resolve(cfg)
    if not api_key:
        return None
    if not commits:
        return None
    try:
        commits_text = "\n".join(f"- {c['subject']}" for c in commits)
        diff_text = (diff or "")[:15000]
        return _chat(
            cfg,
            PROMPT.format(
                project_name=project_name, commits=commits_text, diff=diff_text
            ),
            1200,
        )
    except Exception as e:
        return f"[AI 摘要失败：{e}]"
