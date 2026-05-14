"""Claude API integration for daily change summaries."""

import os

PROMPT = """你是一个帮我做"vibe coding"日志总结的助手。

下面是项目 "{project_name}" 今天的 git 改动（commit message + diff 片段）。请你用一段自然的中文（200 字以内）说清楚：
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


def summarize(project_name, commits, diff, api_key=None):
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if not commits:
        return None
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        commits_text = "\n".join(f"- {c['subject']}" for c in commits)
        diff_text = (diff or "")[:15000]
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[
                {
                    "role": "user",
                    "content": PROMPT.format(
                        project_name=project_name,
                        commits=commits_text,
                        diff=diff_text,
                    ),
                }
            ],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"[AI 摘要失败：{e}]"
