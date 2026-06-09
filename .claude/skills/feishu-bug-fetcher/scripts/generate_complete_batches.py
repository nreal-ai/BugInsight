#!/usr/bin/env python3
"""
Generate complete Markdown batch files for OpenViking import.
Includes bug details, comments, and attachment summaries.

Usage:
    python3 generate_complete_batches.py

Configuration (edit variables below):
    BUGS_PATH       - Path to bugs_all_with_details.json
    ATTACH_DIR      - Path to attachments directory
    OUTPUT_DIR      - Output directory for batch files
    BATCH_SIZE      - Bugs per batch file (default: 50)
"""
import json, os, re
from collections import defaultdict
from datetime import datetime

BUGS_PATH = "/home/xreal/.openviking/workspace/feishu-bugs/bugs_all_with_details.json"
ATTACH_DIR = "/home/xreal/.openviking/workspace/feishu-bugs/attachments/"
OUTPUT_DIR = "/home/xreal/.openviking/workspace/feishu-bugs/import_batches/"
BATCH_SIZE = 50

os.makedirs(OUTPUT_DIR, exist_ok=True)


def extract_log_summary(filepath, max_lines=30):
    """Extract key content from log files (errors, warnings, head/tail)."""
    try:
        with open(filepath, 'r', errors='replace') as f:
            lines = f.readlines()
        if not lines:
            return "（空文件）"
        total = len(lines)
        summary = []
        kw = ['error', 'fail', 'crash', 'exception', 'fatal', 'warn', 'timeout']
        if total <= max_lines:
            for line in lines[:max_lines]:
                if any(k in line.lower() for k in kw):
                    summary.append(f"    {line.rstrip()}")
        else:
            for line in lines[:10]:
                if any(k in line.lower() for k in kw):
                    summary.append(f"    {line.rstrip()}")
            summary.append(f"    ... (共 {total} 行，省略中间部分)")
            for line in lines[-10:]:
                if any(k in line.lower() for k in kw):
                    summary.append(f"    {line.rstrip()}")
        error_count = sum(1 for l in lines if any(k in l.lower() for k in ['error', 'fail', 'crash', 'exception']))
        warn_count = sum(1 for l in lines if 'warn' in l.lower())
        header = f"(日志文件，共 {total} 行"
        if error_count > 0:
            header += f", {error_count} 个错误"
        if warn_count > 0:
            header += f", {warn_count} 个警告"
        header += ")"
        return header + "\n" + "\n".join(summary[:20])
    except Exception as e:
        return f"(读取失败: {e})"


def format_attachment_summary(attach_files):
    """Format attachment list with summaries by type."""
    if not attach_files:
        return ""
    sections = []
    images = [a for a in attach_files if a['ext'] in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif')]
    logs = [a for a in attach_files if a['ext'] in ('.log', '.txt', '.logcat')]
    videos = [a for a in attach_files if a['ext'] in ('.mp4', '.mov', '.wmv', '.webm', '.avi')]
    zips = [a for a in attach_files if a['ext'] in ('.zip', '.rar', '.gz', '.tar')]
    others = [a for a in attach_files if a['ext'] not in {
        '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif',
        '.log', '.txt', '.logcat', '.mp4', '.mov', '.wmv', '.webm', '.avi',
        '.zip', '.rar', '.gz', '.tar'
    }]

    if images:
        sections.append(f"  - 图片 ({len(images)} 个):")
        for img in images[:5]:
            sz = f"{img['size']/1024:.1f}KB" if img['size'] < 1024*1024 else f"{img['size']/1024/1024:.1f}MB"
            desc = img['filename'].split('_', 1)[1] if '_' in img['filename'] else img['filename']
            desc = os.path.splitext(desc)[0]
            sections.append(f"    - {img['filename']} ({sz}) — {desc}")
        if len(images) > 5:
            sections.append(f"    - ... 及其他 {len(images)-5} 个图片")

    if logs:
        sections.append(f"  - 日志文件 ({len(logs)} 个):")
        for log in logs[:2]:
            sz = f"{log['size']/1024:.1f}KB" if log['size'] < 1024*1024 else f"{log['size']/1024/1024:.1f}MB"
            summary = extract_log_summary(log['path'])
            sections.append(f"    - {log['filename']} ({sz}):")
            for line in summary.split('\n')[:5]:
                sections.append(f"      {line}")
        if len(logs) > 2:
            ts = sum(a['size'] for a in logs)
            sections.append(f"    - ... 及其他 {len(logs)-2} 个日志 (共 {ts/1024/1024:.1f}MB)")

    if videos:
        sections.append(f"  - 视频 ({len(videos)} 个):")
        for vid in videos[:3]:
            sz = f"{vid['size']/1024/1024:.1f}MB"
            desc = vid['filename'].split('_', 1)[1] if '_' in vid['filename'] else vid['filename']
            desc = os.path.splitext(desc)[0]
            sections.append(f"    - {vid['filename']} ({sz}) — {desc}")
        if len(videos) > 3:
            sections.append(f"    - ... 及其他 {len(videos)-3} 个视频")

    if zips:
        sections.append(f"  - 压缩包 ({len(zips)} 个):")
        for z in zips[:3]:
            sz = f"{z['size']/1024/1024:.1f}MB"
            sections.append(f"    - {z['filename']} ({sz})")
        if len(zips) > 3:
            sections.append(f"    - ... 及其他 {len(zips)-3} 个压缩包")

    if others:
        sections.append(f"  - 其他文件 ({len(others)} 个):")
        for o in others[:5]:
            sz = f"{o['size']/1024:.1f}KB" if o['size'] < 1024*1024 else f"{o['size']/1024/1024:.1f}MB"
            sections.append(f"    - {o['filename']} ({sz})")
        if len(others) > 5:
            sections.append(f"    - ... 及其他 {len(others)-5} 个文件")

    return "\n".join(sections)


def get_bug_name(bug):
    name = bug.get('name', '') or bug.get('detail', {}).get('work_item_name', '')
    return name or 'Unknown'


def get_bug_status(bug):
    status = bug.get('status', '')
    if not status:
        d = bug.get('detail', {})
        sd = d.get('work_item_status', {})
        status = sd.get('name', sd.get('key', ''))
    return status or 'Unknown'


def format_bug_detail(bug, attach_files):
    """Format a single bug as Markdown with details, comments, and attachments."""
    bid = str(bug.get('id', 'Unknown'))
    name = get_bug_name(bug)
    status = get_bug_status(bug)
    detail = bug.get('detail', {})
    lines = [f"## 缺陷 #{bid}: {name}", ""]
    lines.append(f"**状态**: {status}")

    wtype = detail.get('work_item_type', {})
    if isinstance(wtype, dict):
        lines.append(f"**类型**: {wtype.get('name', '')}")

    template = detail.get('template', {})
    if isinstance(template, dict):
        lines.append(f"**模板**: {template.get('name', '')}")

    mod = detail.get('work_item_mod', '')
    if mod:
        lines.append(f"**模块**: {mod}")

    ct = detail.get('create_time', '')
    if ct:
        lines.append(f"**创建时间**: {ct}")
    ut = detail.get('update_time', '')
    if ut:
        lines.append(f"**更新时间**: {ut}")

    cb = detail.get('create_by', {})
    if isinstance(cb, dict):
        lines.append(f"**创建人**: {cb.get('name', '')} ({cb.get('email', '')})")

    rm = detail.get('role_members', [])
    if rm:
        for role in rm:
            if isinstance(role, dict):
                members = role.get('members', [])
                if members:
                    names = [m.get('name', m.get('key', '')) for m in members if isinstance(m, dict)]
                    lines.append(f"**{role.get('name', '')}**: {', '.join(names)}")

    proj = detail.get('owned_project', {})
    if isinstance(proj, dict):
        lines.append(f"**项目**: {proj.get('name', '')} ({proj.get('simple_name', '')})")

    lines.append("")

    # Comments
    comments = bug.get('comments', [])
    if comments:
        lines.append(f"### 评论 ({len(comments)} 条)")
        lines.append("")
        for i, c in enumerate(comments, 1):
            content = c.get('content', '').strip()
            if not content:
                continue
            creator = c.get('creator', '')
            created_at = c.get('created_at', '')
            creator_name = creator
            for role in rm:
                if isinstance(role, dict):
                    for m in role.get('members', []):
                        if isinstance(m, dict) and m.get('key') == creator:
                            creator_name = m.get('name', creator)
                            break
            content = content.replace('[Image]', '[📷 图片]').replace('[image]', '[📷 图片]')
            lines.append(f"**评论 {i}** ({created_at}) — {creator_name}:")
            lines.append(content)
            lines.append("")

    # Attachments
    attach_summary = format_attachment_summary(attach_files)
    if attach_summary:
        lines.append("### 附件")
        lines.append("")
        lines.append(attach_summary)
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def build_attachment_index():
    attach_map = defaultdict(list)
    if not os.path.exists(ATTACH_DIR):
        return attach_map
    for f in os.listdir(ATTACH_DIR):
        fp = os.path.join(ATTACH_DIR, f)
        sz = os.path.getsize(fp)
        match = re.match(r'^(\d+)_', f)
        if match:
            attach_map[match.group(1)].append({
                'filename': f, 'size': sz,
                'ext': os.path.splitext(f)[1].lower(), 'path': fp,
            })
    return attach_map


def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 加载数据...")
    with open(BUGS_PATH) as f:
        bugs = json.load(f)
    print(f"  缺陷总数: {len(bugs)}")

    attach_map = build_attachment_index()
    print(f"  有附件的缺陷: {len(attach_map)} 个")

    batch_num = 0
    batch_start = 0
    batch_content = []

    for i, bug in enumerate(bugs):
        bid = str(bug.get('id', ''))
        attach_files = attach_map.get(bid, [])
        md = format_bug_detail(bug, attach_files)
        batch_content.append(md)

        if len(batch_content) >= BATCH_SIZE or i == len(bugs) - 1:
            batch_num += 1
            start_id = bugs[batch_start]['id']
            end_id = bugs[i]['id']
            filename = f"batch_{batch_num:03d}_{start_id}_{end_id}.md"
            filepath = os.path.join(OUTPUT_DIR, filename)
            header = f"# 飞书缺陷数据批次 {batch_num} (缺陷 {batch_start+1}-{i+1})\n"
            header += f"> 共 {len(batch_content)} 条缺陷 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            header += f"> 包含: 缺陷详情、评论、附件摘要\n\n---\n\n"
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(header)
                f.write("\n".join(batch_content))
            print(f"  [{batch_num}] {filename} ({len(batch_content)} 条)")
            batch_content = []
            batch_start = i + 1

    total_size = sum(
        os.path.getsize(os.path.join(OUTPUT_DIR, f))
        for f in os.listdir(OUTPUT_DIR) if f.endswith('.md')
    )
    print(f"\n完成! 共 {batch_num} 个批次, 总大小 {total_size/1024/1024:.1f}MB")
    print(f"输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
