#!/usr/bin/env python3
"""
相似缺陷检测模块
基于 OpenViking HTTP API，查找与当前问题相似的历史缺陷
"""

import json
import requests
import urllib.parse
import os
import re
import sys
from typing import List, Dict, Optional
from pathlib import Path

# 添加配置模块路径
config_dir = Path(__file__).parent
sys.path.insert(0, str(config_dir))
try:
    from config import get_openviking_config
except ImportError:
    get_openviking_config = None


class SimilarBugFinder:
    """相似缺陷搜索器 - HTTP API 模式"""
    
    def __init__(
        self,
        api_base: str = None,
        api_key: str = None,
        account: str = None,
        user: str = None,
        workspace: str = str(Path.home() / ".openviking/workspace/viking/default")
    ):
        # 从配置加载默认值
        if get_openviking_config:
            ov_cfg = get_openviking_config()
        else:
            ov_cfg = {
                "api_base": "http://127.0.0.1:1933",
                "api_key": "<OV_API_KEY>",
                "account": "default",
                "user": "xreal"
            }
        
        self.api_base = api_base or ov_cfg.get("api_base", "http://127.0.0.1:1933")
        self.api_key = api_key or ov_cfg.get("api_key", "")
        self.account = account or ov_cfg.get("account", "default")
        self.user = user or ov_cfg.get("user", "xreal")
        self.workspace = workspace
        
        self.headers = {
            'X-API-Key': self.api_key,
            'X-OpenViking-Account': self.account,
            'X-OpenViking-User': self.user,
            'Content-Type': 'application/json'
        }
    
    def _parse_bug_from_uri(self, uri: str) -> Optional[Dict]:
        """从 URI 解析缺陷信息"""
        try:
            # URI 格式: viking://resources/tmpz20pyg_s/01_画面显示/6443113939_xxx/xxx.md
            # 或: viking://temp/xxx/bug_5255059492.md
            # 排除 01_ 开头的分类目录
            
            # 去掉前缀
            path = uri.replace("viking://resources/", "").replace("viking://temp/", "")
            
            # 直接从文件名提取 bug ID（支持 bug_XXX.md 格式）
            import urllib.parse
            unquoted = urllib.parse.unquote(path)
            filename = unquoted.split('/')[-1]
            
            # 匹配 bug_数字.md 或 数字_xxx.md 格式
            bug_match = re.match(r'bug_(\d+)', filename) or re.match(r'^(\d+)_', filename)
            if bug_match:
                bug_id = bug_match.group(1)
                if int(bug_id) > 10000:  # 过滤掉无效ID
                    return {
                        "bug_id": bug_id,
                        "category": "",
                        "uri": uri
                    }
            
            # 备用：路径分割方式
            parts = path.split("/")
            if len(parts) < 2:
                return None
            
            category = ""
            # 找到以数字开头但不是 "01_" 的缺陷ID
            for i, part in enumerate(parts):
                # 检查是否是缺陷文件（数字开头+下划线，且不是 01_）
                if re.match(r'^\d+_', part) and not part.startswith('01_'):
                    # 这是缺陷ID
                    bug_id = re.match(r'^(\d+)', part).group(1)
                    # 前一个是分类
                    if i > 0:
                        cat = parts[i-1]
                        # 跳过分类目录名称（如果以01_开头）
                        if not cat.startswith('01_'):
                            category = urllib.parse.unquote(cat)
                    if bug_id and int(bug_id) > 10000:
                        return {
                            "bug_id": bug_id,
                            "category": category or "",
                            "uri": uri
                        }
        except Exception as e:
            print(f"解析URI失败: {uri}, error: {e}")
        return None
    
    def _get_bug_detail(self, bug_id: str) -> Dict:
        """从本地存储获取缺陷详情"""

        # 1. 优先从 .bug_index_cache.json 读取（4060 条完整数据，含描述）
        cache_path = str(Path.home() / ".openviking/workspace/feishu-bugs/.bug_index_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as fp:
                    cache = json.load(fp)
                idx = cache.get('index', {})
                if bug_id in idx:
                    entry = idx[bug_id]
                    return {
                        "id": bug_id,
                        "title": entry.get('name', ''),
                        "status": entry.get('status', ''),
                        "description": entry.get('desc_lower', ''),
                        "comments": [],
                    }
            except Exception as e:
                print(f"读取 bug_index_cache.json 失败: {e}")

        # 2. 从 bugs_index.json 读取（4060 条，仅有 id/name/status）
        index_path = str(Path.home() / ".openviking/workspace/feishu-bugs/batch/bugs_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as fp:
                    bugs = json.load(fp)
                for bug in bugs:
                    if str(bug.get('id')) == bug_id:
                        return {
                            "id": bug_id,
                            "title": bug.get('name', ''),
                            "status": bug.get('status', ''),
                            "comments": [],
                        }
            except Exception as e:
                print(f"读取 bugs_index.json 失败: {e}")

        # 3. 从 batch 目录读取完整数据（如果存在）
        json_path = str(Path.home() / ".openviking/workspace/feishu-bugs/batch/bugs_all_with_details.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as fp:
                    bugs = json.load(fp)
                for bug in bugs:
                    if str(bug.get('id')) == str(bug_id):
                        detail = {
                            "id": bug_id,
                            "title": bug.get('name', ''),
                            "status": bug.get('status', ''),
                        }
                        # 提取 detail 中的字段
                        detail_obj = bug.get('detail', {})
                        if detail_obj:
                            detail["module"] = detail_obj.get('module', {}).get('name', '')
                            detail["function"] = detail_obj.get('function', {}).get('name', '')
                        # 尝试从 comments 获取解决方案
                        comments_path = str(Path.home() / ".openviking/workspace/feishu-bugs/batch/bugs_with_comments.json")
                        if os.path.exists(comments_path):
                            with open(comments_path, 'r', encoding='utf-8') as cf:
                                comments_data = json.load(cf)
                            for cb in comments_data:
                                if str(cb.get('id')) == str(bug_id):
                                    detail["comments"] = [c.get('content', '')[:200] for c in cb.get('comments', [])[:3]]
                                    break
                        return detail
            except Exception as e:
                print(f"读取JSON失败: {e}")
        
        # 备用：从 docs 目录读取（旧格式）
        doc_path = str(Path.home() / f".openviking/workspace/feishu-bugs/docs/bug_{bug_id}.md")
        if os.path.exists(doc_path):
            with open(doc_path, 'r', encoding='utf-8') as fp:
                content = fp.read()
            
            detail = {"id": bug_id, "content": content}
            
            # 提取标题
            title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
            if title_match:
                detail["title"] = title_match.group(1).strip()
            
            # 提取状态
            status_match = re.search(r'\*\*状态\*\*:\s*(\S+)', content)
            if status_match:
                detail["status"] = status_match.group(1)
            
            # 提取模块
            module_match = re.search(r'\*\*模块\*\*:\s*(\S+)', content)
            if module_match:
                detail["module"] = module_match.group(1)
            
            # 提取功能分类
            func_match = re.search(r'\*\*功能\*\*:\s*(.+)', content)
            if func_match:
                detail["function"] = func_match.group(1).strip()
            
            # 提取评论（解决方案）
            comments = re.findall(r'- \*\*\w+\*\*\s*\([^)]*\):\s*(.+)', content)
            if comments:
                detail["comments"] = comments[:3]
            
            return detail
        
        # 备用：从 OpenViking workspace 读取
        for prefix in [f"feishu-bug:{bug_id}", f"bug:{bug_id}"]:
            bug_dir = f"{self.workspace}/{prefix}"
            
            if os.path.isdir(bug_dir):
                for f in os.listdir(bug_dir):
                    if f.endswith('.md'):
                        filepath = os.path.join(bug_dir, f)
                        with open(filepath, 'r', encoding='utf-8') as fp:
                            content = fp.read()
                        
                        detail = {"id": bug_id, "content": content}
                        
                        # 提取标题
                        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                        if title_match:
                            detail["title"] = title_match.group(1).strip()
                        
                        # 提取状态
                        status_match = re.search(r'\*\*状态\*\*:\s*(\S+)', content)
                        if status_match:
                            detail["status"] = status_match.group(1)
                        
                        # 提取模块
                        module_match = re.search(r'\*\*模块\*\*:\s*(\S+)', content)
                        if module_match:
                            detail["module"] = module_match.group(1)
                        
                        # 提取评论（解决方案）
                        comments = re.findall(r'- \*\*\w+\*\*\s*\([^)]*\):\s*(.+)', content)
                        if comments:
                            detail["comments"] = comments[:3]
                        
                        return detail
        
        return {"id": bug_id, "title": f"缺陷 {bug_id}"}
    
    def find_similar(
        self,
        query: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        查找相似缺陷
        
        Args:
            query: 问题描述（语义搜索关键词）
            limit: 返回结果数量
        
        Returns:
            list: 相似缺陷列表
        """
        # 调用搜索 API
        url = f"{self.api_base}/api/v1/search/find"
        data = {
            "query": query,
            "limit": limit
        }
        
        resp = requests.post(url, headers=self.headers, json=data, timeout=30)
        
        if resp.status_code != 200:
            error = resp.json().get("error", {})
            raise RuntimeError(f"搜索失败: {error.get('message', resp.text)}")
        
        result = resp.json()
        
        # 解析结果（从 resources 中获取）
        similar_bugs = []
        for item in result.get("result", {}).get("resources", []):
            uri = item.get("uri", "")
            score = item.get("score", 0)
            
            # 解析 URI 获取缺陷ID
            parsed = self._parse_bug_from_uri(uri)
            if not parsed:
                continue
            
            bug_id = parsed["bug_id"]
            
            # 获取本地缺陷详情
            detail = self._get_bug_detail(bug_id)
            
            similar_bugs.append({
                "id": bug_id,
                "score": score,
                "title": detail.get("title", detail.get("content", "")[:100]) if detail.get("content") else f"缺陷 {bug_id}",
                "status": detail.get("status", ""),
                "category": parsed.get("category", ""),
                "module": detail.get("module", ""),
                "function": detail.get("function", ""),
                "comments": detail.get("comments", []),
                "uri": uri
            })
        
        return similar_bugs
    
    def find_by_keyword(
        self,
        keyword: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        按关键词查找相似缺陷
        
        Args:
            keyword: 错误关键词（如 "NullPointer", "ANR", "死机"）
            limit: 返回结果数量
        
        Returns:
            list: 相似缺陷列表
        """
        # Validate keyword before hitting API
        if not keyword or not keyword.strip():
            return []
        if len(keyword) > 200:
            return []
        return self.find_similar(
            query=keyword,
            limit=limit
        )


def main():
    """测试入口"""
    finder = SimilarBugFinder()
    
    # 测试搜索
    print("=== 搜索测试：画面黑屏 ===")
    try:
        results = finder.find_similar("画面黑屏", limit=5)
        
        for i, bug in enumerate(results, 1):
            print(f"\n--- 相似缺陷 {i} (得分: {bug['score']:.3f}) ---")
            print(f"ID: {bug['id']}")
            print(f"分类: {bug['category']}")
            print(f"状态: {bug['status']}")
            print(f"模块: {bug.get('module', 'N/A')}")
            print(f"标题: {bug.get('title', 'N/A')[:80]}")
            if bug.get('comments'):
                c = bug['comments'][0]
                preview = c.get('content', str(c))[:100] if isinstance(c, dict) else str(c)[:100]
                print(f"评论: {preview}...")
        
        # 测试关键词搜索
        print("\n\n=== 关键词搜索：USB连接 ===")
        results = finder.find_by_keyword("USB连接异常", limit=5)
        for i, bug in enumerate(results, 1):
            print(f"{i}. [{bug.get('status', 'N/A')}] ID:{bug['id']} (得分: {bug['score']:.3f})")
            
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()