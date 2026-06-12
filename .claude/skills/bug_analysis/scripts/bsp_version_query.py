"""BSP 固件版本查询。
通过 BSP Git tag 建立版本索引，支持从 bug description/log/comments 中
提取 BSP 版本号并查询对应的 tag 信息。
"""

import json, os, subprocess, re
from datetime import datetime
from typing import Optional, Dict, List

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BSP_VERSION_INDEX = os.path.join(SKILL_DIR, "data", "bsp_version_index.json")
BSP_CLONE_PATH = os.path.join(SKILL_DIR, "repositories", "clones", "bsp")


class BspVersionDB:
    """BSP 固件版本数据库。"""

    def __init__(self, index_path: str = BSP_VERSION_INDEX):
        self.index_path = index_path
        self._tags = self._load_index()

    def _load_index(self) -> List[Dict]:
        if os.path.isfile(self.index_path):
            with open(self.index_path, 'r') as f:
                return json.load(f)
        return []

    def _rebuild(self):
        """从 BSP Git 仓库的 tag 重建索引。"""
        if not os.path.isdir(os.path.join(BSP_CLONE_PATH, '.git')):
            return
        tags = []
        result = subprocess.run(
            ["git", "tag", "-l", "--format='%(refname:short) %(creatordate:iso)'"],
            capture_output=True, text=True, cwd=BSP_CLONE_PATH, timeout=30
        )
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.strip("'").split(' ', 1)
            if len(parts) == 2:
                tag, date_str = parts
                if re.match(r'\d+\.\d+\.\d+\.\d+', tag):
                    tags.append({'tag': tag, 'date': date_str})
        with open(self.index_path, 'w') as f:
            json.dump(tags, f, ensure_ascii=False, indent=2)
        self._tags = tags

    def lookup_version(self, version: str) -> Optional[Dict]:
        """查找 BSP 版本对应的 tag 信息。"""
        for t in self._tags:
            if t['tag'] == version:
                return t
        return None

    def extract_bsp_version_from_text(self, text: str) -> Optional[str]:
        """从文本中提取 BSP 版本号。
        支持格式:
        - BSP Version: 15.1.03.328_20260520
        - BSP版本: 15.1.03.328_20260520
        - 固件 Version: 15.1.03.328
        """
        # 完整版本格式（带日期）
        m = re.search(r'(?:BSP|bsp|固件|firmware)\s*(?:Version|版本)?\s*[:=：]?\s*(\d+\.\d+\.\d+\.\d+_\d{8})', text)
        if m:
            return m.group(1)
        # 仅版本号
        m = re.search(r'(?:BSP|bsp|固件|firmware)\s*(?:Version|版本)?\s*[:=：]?\s*(\d+\.\d+\.\d+\.\d+)', text)
        if m:
            return m.group(1)
        return None

    def query_for_bug(
        self,
        bug_description: str = "",
        log_content: str = "",
        comments: List[Dict] = None,
    ) -> Dict:
        """从 bug 信息中查询 BSP 固件版本。"""
        # 从 description 提取
        bsp_ver = self.extract_bsp_version_from_text(bug_description or "")
        if bsp_ver:
            tag_info = self.lookup_version(bsp_ver)
            if tag_info:
                return {
                    'bsp_version': bsp_ver,
                    'tag_info': tag_info,
                    'extract_from': 'description',
                }

        # 从 log 提取
        bsp_ver = self.extract_bsp_version_from_text(log_content or "")
        if bsp_ver:
            tag_info = self.lookup_version(bsp_ver)
            if tag_info:
                return {
                    'bsp_version': bsp_ver,
                    'tag_info': tag_info,
                    'extract_from': 'log',
                }

        # 从 comments 提取
        if comments:
            for c in (comments or []):
                ctext = c.get("content", "") if isinstance(c, dict) else str(c)
                bsp_ver = self.extract_bsp_version_from_text(ctext)
                if bsp_ver:
                    tag_info = self.lookup_version(bsp_ver)
                    if tag_info:
                        return {
                            'bsp_version': bsp_ver,
                            'tag_info': tag_info,
                            'extract_from': 'comment',
                        }

        # 兜底：返回空
        return {
            'bsp_version': None,
            'tag': None,
            'tag_info': None,
            'commit_sha': None,
            'extract_from': None,
            'summary': '未找到 BSP 固件版本信息',
        }


def format_bsp_version_prompt(bsp_result: Dict) -> str:
    """将 BSP 版本查询结果格式化为 LLM prompt 片段。"""
    if not bsp_result.get('tag_info'):
        return ""
    tag = bsp_result['tag_info']
    parts = [
        "## BSP 固件版本信息",
        f"- **BSP 版本**: {bsp_result.get('bsp_version', tag['tag'])}",
        f"- **Tag 日期**: {tag.get('date', 'N/A')}",
        f"- **提取来源**: {bsp_result.get('extract_from', 'N/A')}",
    ]
    return "\n".join(parts)
